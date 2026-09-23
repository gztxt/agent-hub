#!/usr/bin/env python3
"""重启后一次性复合体检（**不建 pty 会话** ⇒ 不打扰任何在用的终端）。

为什么要单独一个生产体检脚本：09-23 用户裁定「探活最多 2 次即停」。重启是要紧动作，
体检必须**一轮取齐**（响应码 + 时间戳 + /health 语义字段 + WS 关闭码 + 日志异常计数），
不能一个端点一个端点串行试。跑两遍的意义只是"再确认一次没抖动"，不在验证正确性。

覆盖 10 组（全部对着真生产，不建影子）：
  1 /health 语义：status/version/git_sha_boot==git_sha_now/code_stale/db_ok
     以及 **code_matches_head 与 runtime_dirty_files 的自洽性**（不是要求它必须 True ——
     同仓有别人的未提交改动时它就是该 False，那正是这个字段存在的理由）
  2 pid 与 systemd MainPID 一致（防"脚本说 active、其实是别的进程在听这个口"）
  3 磁盘上真实存在的 *.bak-* 在 /static/ 下必须 404（P1-8 闸门）
  4 /static/../ 兄弟目录穿越必须 404（--path-as-is，urllib 会自己规范化掉）
  5 服务吐出的 hub.js 的 md5 == 磁盘 == git HEAD（前端改动是不是真上线了）
  6 协商缓存 304 必须带 Vary: Accept-Encoding
  7 终端鉴权：免 token GET/DELETE 401、错 token 401、对 token 200
  8 不存在的 sid 走 WS 必须拿到业务码 4404（不是 1006/403）
  9 首页 200 且版本号与 /health 同源
 10 启动后日志里 Traceback/ERROR 计数为 0；chat 端点真跑一次 LLM（--no-chat 可跳）

跑法：venv/bin/python tests/verify_prod_smoke.py [http://127.0.0.1:3102] [--no-chat]
退出码 0=全绿 / 1=有 FAIL / 2=环境不满足（.env 读不到 token 等）
"""
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import websockets                                     # noqa: E402
except ImportError:
    print("  环境不满足：venv 里没有 websockets")
    sys.exit(2)

REPO = Path(__file__).resolve().parents[1]
ARGS = [a for a in sys.argv[1:]]
BASE = next((a for a in ARGS if a.startswith("http")), "http://127.0.0.1:3102")
DO_CHAT = "--no-chat" not in ARGS
WSBASE = BASE.replace("http://", "ws://").replace("https://", "wss://")

F, fails = [], 0


def check(name, ok, detail=""):
    global F
    F.append((name, bool(ok)))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))


class Headers(dict):
    """大小写不敏感的响应头袋。第一版探针直接 .get("Vary") 判成 FAIL，
       而 curl -D - 实测服务端确实发了 `vary: Accept-Encoding` —— 是读法错，不是产品回归。"""

    def get(self, k, dflt=None):
        if dict.__contains__(self, k):
            return self[k]
        lk = str(k).lower()
        for kk, vv in self.items():
            if str(kk).lower() == lk:
                return vv
        return dflt


def http(path, headers=None, method="GET", body=None, timeout=12):
    data = json.dumps(body).encode() if body is not None else None
    h = dict(headers or {})
    if data:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, headers=h, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, Headers(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, Headers(e.headers), e.read()
    except Exception as e:                                  # noqa: BLE001
        return 0, {}, str(e).encode()


def curl(path_as_is):
    """--path-as-is：不让客户端把 `/../` 规范化掉，否则穿越测试是假的。"""
    r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                        "--path-as-is", "-m", "8", BASE + path_as_is],
                       capture_output=True, text=True)
    return r.stdout.strip()


def md5(b):
    return hashlib.md5(b).hexdigest()


def main():
    tok = ""
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TERM_TOKEN="):
            tok = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not tok:
        print("  环境不满足：.env 里读不到 TERM_TOKEN（不猜、不用空串去撞）")
        return 2
    print(f"  目标：{BASE}   TERM_TOKEN=<{len(tok)} 字符，已脱敏>")

    # ── 1/2 /health 语义 + pid 自洽 ─────────────────────────────
    st, _, raw = http("/health")
    d = json.loads(raw or b"{}")
    print("\n[1] /health 语义字段")
    check("status ok", st == 200 and d.get("status") == "ok", f"HTTP {st}")
    ver_src = re.search(r'VERSION = "([\d.]+)"', (REPO / "src" / "main.py").read_text(encoding="utf-8"))
    check("服务版本 == 源码 VERSION", d.get("version") == (ver_src and ver_src.group(1)),
          f"health={d.get('version')} src={ver_src and ver_src.group(1)}")
    boot, now = str(d.get("git_sha_boot")), str(d.get("git_sha_now"))
    check("boot sha == now sha（进程跑的是当前提交）", boot == now and len(boot) >= 8,
          f"boot={boot[:8]} now={now[:8]}")
    check("code_stale 为 False", d.get("code_stale") is False, f"={d.get('code_stale')}")
    dirty = int(d.get("runtime_dirty_files") or 0)
    mh = d.get("code_matches_head")
    check("code_matches_head 与 dirty 计数自洽（有运行时脏文件就必须 False）",
          (mh is True and dirty == 0) or (mh is False and dirty > 0),
          f"matches_head={mh} runtime_dirty_files={dirty} untracked_code={d.get('untracked_code_files')}")
    check("db_ok True", d.get("db_ok") is True)
    pid = d.get("pid")
    mp = subprocess.run(["systemctl", "--user", "show", "agent-hub", "-p", "MainPID",
                         "--value"], capture_output=True, text=True).stdout.strip()
    check("pid 与 systemd MainPID 一致（在听这个口的就是本尊）", str(pid) == mp,
          f"health={pid} systemd={mp}")

    # ── 3 磁盘上真存在的 .bak 必须 404 ──────────────────────────
    print("\n[2] 静态闸门")
    baks = sorted(REPO.glob("static/*.bak-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if baks:
        code, _, _ = http("/static/" + baks[0].name)
        check("磁盘上真实存在的 .bak 在 /static/ 下 404", code == 404,
              f"{baks[0].name[:28]}… → HTTP {code}")
    else:
        check("static/ 下确有 .bak 可试（没样本＝这条没测）", False, "零个 .bak")
    check("兄弟目录穿越 404（--path-as-is）", curl("/static/../static_evil/secret.txt") == "404",
          "改前同路径返回 200 + CANARY")

    # ── 4 服务吐出的前端 == 仓库 HEAD ──────────────────────────
    st, _, served = http("/static/hub.js")
    disk = (REPO / "static" / "hub.js").read_bytes()
    head = subprocess.run(["git", "-C", str(REPO), "show", "HEAD:static/hub.js"],
                          capture_output=True).stdout
    check("hub.js 三处一致：服务/磁盘/HEAD", st == 200 and md5(served) == md5(disk) == md5(head),
          f" served={md5(served)[:8]} disk={md5(disk)[:8]} head={md5(head)[:8]}")
    _, h1, _ = http("/static/hub.js")
    et = h1.get("ETag") or h1.get("etag")
    st2, h2, _ = http("/static/hub.js", headers={"If-None-Match": et or "*",
                                                 "Accept-Encoding": "gzip"})
    check("304 带 Vary: Accept-Encoding", st2 == 304 and "accept-encoding" in
          str(h2.get("Vary", "")).lower(), f"HTTP {st2} Vary={h2.get('Vary')}")

    # ── 5 终端鉴权 ─────────────────────────────────────────────
    print("\n[3] 终端鉴权（P1-7）")
    check("免 token GET /api/term/sessions → 401", http("/api/term/sessions")[0] == 401)
    check("错 token → 401", http("/api/term/sessions",
                                 headers={"x-term-token": "wrong-token-here"})[0] == 401)
    stt, _, rb = http("/api/term/sessions", headers={"x-term-token": tok})
    sj = json.loads(rb or b"{}")
    check("对 token（头）→ 200 且形状是 {sessions:[...]}",
          stt == 200 and isinstance(sj.get("sessions"), list), f"HTTP {stt} keys={list(sj)}")
    stq, _, _ = http("/api/term/sessions?token=" + tok)
    check("对 token（query）等价可用", stq == 200, f"HTTP {stq}")
    check("免 token DELETE 一个不存在的 sid 也是 401（不泄露 sid 存在性）",
          http("/api/term/sessions/deadbeef", method="DELETE")[0] == 401)

    # ── 6 WS 关闭码 ────────────────────────────────────────────
    print("\n[4] WS 关闭码可见性")

    got = {"code": None, "exc": None}

    async def closecode():
        try:
            ws = await websockets.connect(f"{WSBASE}/ws/term/deadbeef?token={tok}")
            try:
                await asyncio.wait_for(ws.recv(), timeout=3)
            except Exception:                               # noqa: BLE001
                pass
            got["code"] = ws.close_code    # 与 verify_p1_backend.py 同一取法，别各自发明
            await ws.close()
        except Exception as e:              # noqa: BLE001
            got["exc"] = "%s:%s" % (type(e).__name__,
                                    getattr(getattr(e, "response", None), "status_code", None))

    asyncio.run(closecode())
    code, wsexc = got["code"], got["exc"]
    check("不存在的 sid 拿到业务码 4404（不是 1006/403）", code == 4404,
          f"实得 {code} exc={wsexc}")

    # ── 7 页面 + 日志异常 + chat ───────────────────────────────
    print("\n[5] 页面 / 日志 / 真实 LLM")
    st3, _, page = http("/")
    check("首页 200 且版本号与 /health 同源",
          st3 == 200 and str(d.get("version")).encode() in page, f"HTTP {st3}")
    j = subprocess.run(["journalctl", "--user", "-u", "agent-hub", "--no-pager", "-n", "400"],
                       capture_output=True, text=True).stdout
    tail = j[j.rfind("启动完成"):] if "启动完成" in j else j
    errs = len(re.findall(r"Traceback|ERROR|\[term\] 异常", tail))
    check("本次启动之后日志零异常", errs == 0, f"命中 {errs} 行")

    if DO_CHAT:
        stl, _, ab = http("/api/agents")
        ids = [a.get("id") for a in (json.loads(ab or b"{}").get("agents") or []) if isinstance(a, dict)]
        if not ids:
            check("拿到可用 agent id 以测 chat", False, f"HTTP {stl}，跳过 chat 实跑")
        else:
            t0 = __import__("time").time()
            # v0.13.6 写端点闸门上线后，chat 也归 32 条受闸门路由之一 —— 不带凭据这一发
            # 拿到的 401 是**正确行为**，不是缺陷（探针比闸门早生，得跟上）。前端同理：
            # hub.js 的 api() 只在写方法上带 x-hub-token。
            stc, _, cb = http(f"/api/agents/{ids[0]}/chat", method="POST",
                              body={"message": "只回复四个字：收到了"}, timeout=90,
                              headers={"x-hub-token": tok})
            try:
                cj = json.loads(cb or b"{}")
            except Exception:                               # noqa: BLE001
                cj = {}
            # 实测字段名是 `response`（不是 reply/text）—— 第一版按记忆里的名字读，
            # 把一条成功的真 LLM 回复判成空。判据同时要求 usage 里有 token 计数，
            # 免得将来"字段有值但根本没调模型"这类假绿混进来。
            txt = str(cj.get("response") or cj.get("reply") or cj.get("text") or "")
            check("chat 端点真跑通一次 LLM（工具环之外的文本环）",
                  stc == 200 and cj.get("success") is True and len(txt) > 0
                  and isinstance(cj.get("usage"), dict) and (cj["usage"].get("total_tokens") or 0) > 0,
                  f"HTTP {stc} {__import__('time').time()-t0:.1f}s model={cj.get('model')} reply={txt[:16]!r}")
    else:
        print("  SKIP  chat（--no-chat）")

    print("\n[6] 活终端会话与时间戳（只报，不判）")
    print(f"  term_sessions={d.get('term_sessions')} uptime_s={d.get('uptime_s')} "
          f"boot_at={d.get('boot_at')} dirty_age_s={d.get('dirty_age_s')}")
    bad = [n for n, ok in F if not ok]
    print("\n" + (f"全部通过 ✅  {len(F) - len(bad)}/{len(F)}" if not bad else
                  f"失败 {len(bad)}/{len(F)} ❌：\n   " + "\n   ".join(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
