#!/usr/bin/env python3
"""P1 后端实况探针（多观看者独立流 + 应用层心跳 + 静态目录闸门）。

这是 **实况探针**，不是离线单测：需要一个在跑的 hub，默认打影子实例。
  跑法：venv/bin/python tests/verify_p1_backend.py [http://127.0.0.1:3199]
影子实例起法（与生产 DATA_DIR 隔离，务必 cd 进影子树再起）：
  ( cd /tmp/ah-p0 && <repo>/venv/bin/python -m uvicorn src.main:app --port 3199 )

为什么需要它：`src/term.py` 的 P1-5 缺陷在离线单测里看不见 —— 必须两条 WS 同时挂在
同一个会话上才会显形（旧实现共用一个输出队列，两个消费者互相 get()，
各自只拿到约一半流；本机正是「桌面 + 手机同看一条终端」的用法）。

红-绿对照（同一份探针分别打改前/改后两棵树）：
  改前 :3197  观看者A 实收 0 / B 实收 34（共 120 条被劈开）  → FAIL
  改后 :3199  观看者A、B 各实收 120（互不偷字节）            → PASS
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else os.getenv("HUB_BASE", "http://127.0.0.1:3199")
TOK = os.getenv("HUB_TOKEN", "shadow-only-p0")
WSBASE = BASE.replace("http", "ws", 1)

try:
    import websockets
except ImportError as e:  # pragma: no cover
    print(f"缺依赖：{e}")
    sys.exit(2)

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(name)


def api(path, body=None, method="POST"):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json", "x-term-token": TOK})
    with urllib.request.urlopen(r, timeout=30) as f:
        return json.loads(f.read())


def dual_view(sid, cmd, want_n, budget=25.0):
    """两条 WS 同挂一个会话，各自独立收流（不做任何 hb 干扰）。"""
    buf = {0: bytearray(), 1: bytearray()}   # 用容器而非裸名，避开嵌套函数的赋值作域问题

    async def main():
        c0 = await websockets.connect(f"{WSBASE}/ws/term/{sid}?token={TOK}")
        c1 = await websockets.connect(f"{WSBASE}/ws/term/{sid}?token={TOK}")
        for c in (c0, c1):
            await c.send(json.dumps({"type": "resize", "cols": 100, "rows": 40}))
        await c0.send(json.dumps({"type": "data", "data": cmd + "\r"}))
        t0 = time.time()
        while time.time() - t0 < budget:
            m = await _recv(c0, 0.6)
            if m:
                buf[0] += m if isinstance(m, bytes) else b""
            m = await _recv(c1, 0.6)
            if m:
                buf[1] += m if isinstance(m, bytes) else b""
            if len(set(re.findall(rb"ZZ\d+", bytes(buf[0])))) >= want_n and \
               len(set(re.findall(rb"ZZ\d+", bytes(buf[1])))) >= want_n:
                break
        for c in (c0, c1):
            try:
                await c.close()
            except Exception:
                pass
    asyncio.run(main())
    return bytes(buf[0]).decode("utf-8", "replace"), bytes(buf[1]).decode("utf-8", "replace")


async def _recv(ws, t):
    try:
        return await asyncio.wait_for(ws.recv(), timeout=t)
    except asyncio.TimeoutError:
        return None
    except Exception:
        raise


def hb_roundtrip(sid):
    """单独一条连接验心跳回执：返回 (是否回执, 回执原文, pty 是否被污染, MARKER 是否出现)。"""
    ack = {"txt": None}
    polluted = {"v": False}
    marker = {"v": False}

    async def main():
        ws = await websockets.connect(f"{WSBASE}/ws/term/{sid}?token={TOK}")
        try:
            while True:
                await asyncio.wait_for(ws.recv(), 0.3)
        except Exception:
            pass
        t0 = time.time()
        await ws.send(json.dumps({"type": "hb"}))
        while time.time() - t0 < 5 and not ack["txt"]:
            m = await _recv(ws, 1.0)
            if isinstance(m, str) and '"hb"' in m:
                ack["txt"] = m
        await ws.send(json.dumps({"type": "data", "data": "echo HBPROBE-$((21*2))\r"}))
        t1 = time.time()
        buf = bytearray()
        while time.time() - t1 < 3:
            m = await _recv(ws, 1.0)
            if m:
                buf += m if isinstance(m, bytes) else b""
        polluted["v"] = b"not found" in bytes(buf)
        marker["v"] = b"HBPROBE-42" in bytes(buf)
        await ws.close()
    asyncio.run(main())
    return bool(ack["txt"]), ack["txt"], polluted["v"], marker["v"]


def static_get(path, extra=None):
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}"] + (extra or []) + [BASE + path]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()


print(f"目标：{BASE}")

# ── 1. 多观看者：两条 WS 必须各拿到完整流 ────────────────────────────
print("\n[1] 多观看者独立流（P1-5）")
N = 120
s = api("/api/term/sessions", {"agent_id": "shell"})["session"]
sid = s["id"]
print(f"  会话 {sid}  cmd={s['cmd']}")
try:
    a, b = dual_view(sid, f"for i in $(seq 1 {N}); do echo \"ZZ$i\"; done", N)
    ids = lambda t: {int(x[2:]) for x in re.findall(r"ZZ\d+", t)}
    na, nb = len(ids(a)), len(ids(b))
    check(f"观看者A 收到全部 {N} 条", na >= N, f"实收 {na}")
    check(f"观看者B 收到全部 {N} 条", nb >= N, f"实收 {nb}（旧实现被 A 偷走后仅 ~34）")
    check("两边集合一致（互不丢、互不偷）", ids(a) == ids(b) and na >= N,
          f"A-B={sorted(ids(a)-ids(b))[:5]} B-A={sorted(ids(b)-ids(a))[:5]}")
    ok, txt, polluted, marker = hb_roundtrip(sid)
    check("服务端 hb 有回执（应用层心跳可供前端识半开）", ok, str(txt)[:60])
    check("hb 未被写进 pty（不会污染终端）", not polluted and marker, f"MARKER={marker}")
finally:
    try:
        api("/api/term/sessions/" + sid, method="DELETE")
    except Exception:
        pass

# ── 2. 静态目录闸门 ────────────────────────────────────────────────
print("\n[2] 静态目录闸门（P1-8）")
check("历史备份件 .bak-* 不再可取（404）",
      static_get("/static/hub.js.bak-20260922_133021-replay-query-gate") == "404",
      "改前实测 200 / 88779B")
check("兄弟目录逃逸 ../static_evil/secret.txt 被拒（404）",
      static_get("/static/../static_evil/secret.txt", ["--path-as-is"]) == "404",
      "改前实测 200 且能取回 CANARY —— startswith 不是目录边界")
check("正常资源 hub.js 仍 200（闸门未误伤）", static_get("/static/hub.js") == "200")
h = subprocess.run(["curl", "-s", "-D", "-", "-o", "/dev/null", BASE + "/static/hub.js"],
                   capture_output=True, text=True, timeout=30).stdout
etag = next((l.split(" ", 1)[1].strip() for l in h.splitlines() if l.lower().startswith("etag:")), "")
h2 = subprocess.run(["curl", "-s", "-D", "-", "-o", "/dev/null", "-H", f"If-None-Match: {etag}",
                     BASE + "/static/hub.js"], capture_output=True, text=True, timeout=30).stdout
check("304 分支带 Vary: Accept-Encoding",
      "304" in h2.splitlines()[0] and "accept-encoding" in h2.lower(),
      "改前 304 无 Vary")

# ── 3. WS 关闭码对客户端是否可见 ──────────────────────────────
# 旧服务端在 accept() **之前** close ⇒ Starlette 只回 HTTP 403 拒握手 ⇒
# 客户端拿到 1006/403（与"链路断了"同签名）⇒ 前端会对已不存在的 sid 无限重连。
print("\n[3] WS 关闭码可见性（服务端协议）")


def ws_close_code(sid):
    got = {"code": None, "exc": None}

    async def main():
        try:
            ws = await websockets.connect(f"{WSBASE}/ws/term/{sid}?token={TOK}")
            try:
                await asyncio.wait_for(ws.recv(), timeout=3)
            except Exception:
                pass
            got["code"] = ws.close_code
            await ws.close()
        except Exception as e:
            got["exc"] = f"{type(e).__name__}:{getattr(getattr(e, 'response', None), 'status_code', None)}"
    asyncio.run(main())
    return got["code"], got["exc"]


code, exc = ws_close_code("nosuchsid0")
check("不存在的 sid 能给客户端真实业务码 4404", code == 4404,
      f"close_code={code} exc={exc}（改前实测：close_code=None exc=InvalidStatus:403）")

print("\n" + ("全部通过 ✅" if not FAILS else f"失败 {len(FAILS)} 项 ❌：{FAILS}"))
sys.exit(1 if FAILS else 0)
