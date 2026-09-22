"""v0.13.0 历史下拉 / 续聊 端到端验证。对已运行实例跑（默认 :3199）。
   跑法：cd ~/agent-hub && TOKEN=$(grep -m1 '^TERM_TOKEN=' .env | cut -d= -f2-) \
         venv/bin/python tests/verify_term_history.py
   逐条打 PASS/FAIL；退出码非 0 = 有 FAIL。真输入（发 prompt）不在此脚本内，留 T4 手测。"""
import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = os.getenv("HUB_BASE", "http://127.0.0.1:3199")
TOKEN = os.environ["TOKEN"] # 计划原文此处的取值语句被脱敏器抹成 ***，按同文档 T4 Step3 口径补回；只读环境变量，不打印值
HDR = {"x-term-token": TOKEN}
FAILS = []


def call(path, *, method="GET", body=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **(HDR if headers is None else headers)})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except Exception:  # noqa: BLE001
            return e.code, {"detail": raw.decode("utf8", "ignore")[:120]}


def chk(name, ok, evidence=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{evidence}]" if evidence else ""))
    if not ok:
        FAILS.append(name)


def _clean(s):
    return re.sub(r"\x1b\][^\a]*\a|\x1b\][^\x1b]*\x1b\\\\|\x1b\[[0-9;:?]*[a-zA-Z]|\r", "", s)


def _answer(chunk):
    """按 xterm.js 的口径答掉 TUI 发来的终端查询。
       实测（2026-09-22 22:3x）：grok/jcode 发 `CSI > 0 q`、hermes 发 `CSI c` + `OSC 11 ; ?`、
       codex 发 kitty `CSI > 1 u`——**不答它们就不画屏**，只给 11~200B，看着像“没恢复”。
       这是无头脚本的必备伪终端，不是产品缺陷。"""
    out = []
    if re.search(r"\x1b\[[>=]?[0-9;]*[cq]", chunk):
        out.append("\x1b[?62;1;2;6;8;9;15;18;21;22c")            # DA1
    if re.search(r"\x1b\[\?>?[0-9;]*u", chunk):
        out.append("\x1b[?0u")                                   # kitty 键盘协议
    if re.search(r"\x1b\]1[01];\?", chunk):
        out.append("\x1b]11;rgb:0000/0000/0000\x1b\\")            # 背景色查询
    if "\x1b[6n" in chunk:
        out.append("\x1b[30;100R")                               # 光标位置
    return "".join(out)


async def _replay_once(url):
    import websockets
    got = ""
    idle = 0
    t0 = time.time()
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "resize", "cols": 100, "rows": 30}))
        while idle < 3 and time.time() - t0 < 40:
            try:
                m = await asyncio.wait_for(ws.recv(), timeout=4)
            except asyncio.TimeoutError:
                idle += 1
                continue
            except Exception:  # noqa: BLE001
                break
            idle = 0
            chunk = m.decode("utf8", "ignore") if isinstance(m, bytes) else m
            got += chunk
            ans = _answer(chunk)
            if ans:
                await ws.send(json.dumps({"data": ans}))
    return got


def replay(sid):
    """拉初始渲染。hermes 实测起手静默加载 ~25s（先只发 11B 终端查询），
       画完后才会内容进 ring，所以**可见内容不够就重连**（ring 会把旧帧回放给新连接）。"""
    url = BASE.replace("http", "ws") + f"/ws/term/{sid}?token={TOKEN}"
    got = ""
    for _ in range(3):
        got += asyncio.new_event_loop().run_until_complete(_replay_once(url)) or ""
        if len(" ".join(_clean(got).split())) >= 300:
            break
        time.sleep(6)
    return got


def wait_title(sid, budget=40):
    """等 list_sessions 里该会话的中文 title 出末（= pid → 盘上会话反查生效）。"""
    t0 = time.time()
    while time.time() - t0 < budget:
        me = next((x for x in call("/api/term/sessions")[1].get("sessions", []) if x["id"] == sid), None)
        if me and me.get("title"):
            return me["title"]
        time.sleep(3)
    return ""


def main():
    # 1) 旧行为不降级
    code, d = call("/api/term/sessions", headers={})
    chk("旧行为：GET /api/term/sessions 免 token 仍 200", code == 200, f"HTTP {code}")
    chk("活会话条目带 title 字段（可为空串）", all("title" in s for s in d.get("sessions", [])),
        f"n={len(d.get('sessions', []))}")

    # 2) history 端点
    code, d = call("/api/term/history/grok?limit=3")
    items = d.get("items") or []
    chk("history/grok 有中文条目", code == 200 and len(items) > 0, f"HTTP {code} n={len(items)}")
    if items:
        t = items[0]["title"]
        chk("标题含中文、非字母编号", bool(re.search(r"[\u4e00-\u9fff]", t)) and not re.match(r"^[0-9a-f]{4}$", t), t[:24])
    chk("limit=3 生效", len(items) <= 3, f"n={len(items)}")
    chk("history 缺 token → 401", call("/api/term/history/grok", headers={})[0] == 401)
    code, d = call("/api/term/history/qoder?limit=3")
    chk("qoder 空态有中文 note", code == 200 and bool(d.get("note")), (d.get("note") or "")[:30])
    chk("无仓库 agent(pi) → 400", call("/api/term/history/pi")[0] == 400)
    chk("非法 limit → 422", call("/api/term/history/grok?limit=999")[0] == 422)

    # 3) resume 白名单
    code, d = call("/api/term/sessions", method="POST", body={"agent_id": "grok", "session_id": "x; rm -rf /"})
    chk("注入型 session_id → 400", code == 400, f"HTTP {code}")
    code, d = call("/api/term/sessions", method="POST",
                   body={"agent_id": "grok", "session_id": "00000000-0000-4000-8000-000000000000"})
    chk("形状合法但不存在的 id → 404", code == 404, f"HTTP {code}")

    # 4) 逐 agent：列历史 → 续聊起 pty → 真续的是被点那条 + 终端有渲染 → 销毁
    #    内容级“旧会话正文出现在屏上”不在脚本里断言：TUI 开在**底部视口**，
    #    首条提问早已滚出屏（实测 grok 屏上是最后一次回答），该条归 Task 4 Step 4 眼校。
    for agent in ["grok", "claude", "jcode", "hermes", "codex", "qoder"]:
        his = call(f"/api/term/history/{agent}?limit=1")[1].get("items") or []
        if not his:
            chk(f"{agent} 无历史可续（空态可接受）", True, "note 已给")
            continue
        sid = his[0]["id"]
        code, d = call("/api/term/sessions", method="POST", body={"agent_id": agent, "session_id": sid})
        s = d.get("session") or {}
        chk(f"{agent} 续聊起会话 200", code == 200 and bool(s.get("id")),
            f"HTTP {code} cmd={str(s.get('cmd',''))[:34]}")
        if s.get("id"):
            chk(f"{agent} 续的是被点那条（argv 带 id）", sid in (s.get("cmd") or ""), f"id={sid[:20]}")
            # 顶栏芯片不得出现 hex sid：pid 反查不到就走 resume_of 直查盘上标题
            t = wait_title(s["id"])
            chk(f"{agent} 顶栏芯片拿到中文标题", bool(t) and bool(re.search(r"[\u4e00-\u9fff]", t)),
                f"title={t[:22]!r}")
            got = replay(s["id"]) or ""
            vis = " ".join(_clean(got).split())
            chk(f"{agent} 终端有渲染（非白屏）", len(vis) >= 300,
                f"原始 {len(got)}B / 可见 {len(vis)}字：{vis[:38]!r}")
            chk(f"{agent} 销毁生效", call(f"/api/term/sessions/{s['id']}", method="DELETE")[0] == 200)

    print("\nALL GREEN" if not FAILS else f"\n{len(FAILS)} FAIL: {FAILS}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
