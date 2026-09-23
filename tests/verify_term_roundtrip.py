"""终端真工具环端到端验证：HTTP 建会话 → WS 发命令 → 读回显 → 清理。

用途：**任何 agent-hub 重启都必须先跑它**（重启会 term.kill_all() 杀掉所有活跃 pty，
只验 /health 200 不足以证明终端还能用——本机军规：一次文本请求 + 一次真工具调用）。

判据：
  T1 POST /api/term/sessions(agent_id=shell) 返回 200、拿到 sid 且 alive=true
  T1b hub 的 cgroup 子 pid 集出现新增（pty 真被拉起，不信 API 自己的账）
  T2 WS /ws/term/{sid} 握手成功并能收到回放/输出字节
  T3 发一条真命令 `echo <nonce>`，回显里出现该 nonce  => pty 输入与输出双向通
  T4 DELETE 后：子 pid 消失（不产无主副本）且会话不在列表里
  T5 无 token 时必须被拒（HTTP 401 / WS 4401），证明鉴权没被改动削弱

跑法：cd ~/agent-hub && venv/bin/python tests/verify_term_roundtrip.py
      env：HUB_BASE（默认 http://127.0.0.1:3199，即影子实例；要打生产显式指 :3102）
token 只从 .env 读，绝不打印、绝不落盘。
"""
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

AG = Path(__file__).resolve().parents[1]
BASE = os.getenv("HUB_BASE", "http://127.0.0.1:3199")   # 默认影子；打生产需显式覆盖
FAILS = []


def token():
    s = (AG / ".env").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^TERM_TOKEN=(.*)$", s, re.M)
    return (m.group(1).strip() if m else "")


def api(path, method="GET", body=None, tok=None, timeout=15):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"x-term-token": tok} if tok else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"_body": e.read().decode("utf-8", "replace")[:120]}


def check(name, ok, detail=""):
    print(("  PASS " if ok else "  FAIL ") + name + (" :: " + str(detail) if detail else ""))
    if not ok:
        FAILS.append(name)


def roundtrip(tok, sid, nonce):
    """连 WS（同步客户端），发一条真命令，攒回显直到看到 nonce（或超时）。"""
    from websockets.sync.client import connect
    got = []
    url = BASE.replace("http", "ws") + "/ws/term/%s?token=%s" % (sid, tok)
    with connect(url, max_size=8_000_000, open_timeout=10) as ws:
        # 先报尺寸（FitAddon 同款），否则 pty 可能拿到 0 列
        ws.send(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
        time.sleep(0.3)
        ws.send(json.dumps({"data": "\r"}))
        ws.send(json.dumps({"data": "echo %s\r" % nonce}))
        t0 = time.time()
        while time.time() - t0 < 12:
            try:
                msg = ws.recv(timeout=2.0)
            except Exception:
                break
            got.append(msg.decode("utf-8", "replace") if isinstance(msg, (bytes, bytearray)) else str(msg))
            if nonce in "".join(got):
                break
    return "".join(got)


def hub_children():
    """agent-hub 主进程的直接子 pid 集（pty 子进程的父就是它）——用于物理旁证。"""
    mpid = subprocess.run(["systemctl", "--user", "show", "-p", "MainPID", "--value",
                           "agent-hub"], capture_output=True, text=True).stdout.strip()
    if not mpid.isdigit():
        return set(), ""
    out = subprocess.run(["ps", "-o", "pid=", "--ppid", mpid],
                         capture_output=True, text=True).stdout
    return {int(x) for x in out.split()}, mpid


def main():
    tok = token()
    check("T0 .env 里 TERM_TOKEN 可读（不打印值）", bool(tok), "len=%d" % len(tok))
    if not tok:
        print("\nRESULT: FAIL -> 无 token，后续判据不可跑")
        return 1

    base_kids, mpid = hub_children()
    nonce = "PI-RT-" + secrets.token_hex(4)
    print("== T1 建 shell 终端会话 ==")
    st, res = api("/api/term/sessions", "POST", {"agent_id": "shell"}, tok)
    sess = (res or {}).get("session") or {}
    sid = sess.get("id")
    # 注：to_dict 不暂露 pid（有意），所以 T1 只断言会话层字段，进程层证据交给 T4b 的子 pid 差集
    check("T1 POST /api/term/sessions 200 且会话 alive",
          st == 200 and bool(sid) and sess.get("alive") is True and bool(sess.get("cmd")),
          "HTTP %s sid=%s alive=%s cmd=%r cwd=%s" % (
              st, sid, sess.get("alive"), sess.get("cmd"), sess.get("cwd")))
    if not sid:
        print("\nRESULT: FAIL -> 建会话失败"); return 1

    kids_now, _ = hub_children()
    new_kids = kids_now - base_kids
    check("T1b pty 子进程真被拉起（cgroup 子 pid 差集）", len(new_kids) >= 1,
          "hub MainPID=%s 新增子 pid=%s" % (mpid, sorted(new_kids)))

    try:
        print("== T2/T3 WS 真发命令并读回显 ==")
        out = roundtrip(tok, sid, nonce)
        out = out if isinstance(out, str) else ""
        check("T2 WS 握手成功且有输出字节", len(out) > 0, "回显长度 %d 字符" % len(out))
        check("T3 真命令回显命中 nonce（pty 双向通）", nonce in out,
              "nonce=%s %s" % (nonce, ("命中" if nonce in out else "未见，尾 80 字=%r" % out[-80:])))

        print("== T4 清理与无主副本 ==")
        st2, _ = api("/api/term/sessions/" + sid, "DELETE", None, tok)
        check("T4a DELETE 返回 2xx", st2 in (200, 202, 204), "HTTP %s" % st2)
        time.sleep(1.5)
        # kill() 是 SIGTERM → 2s 后升级为组级 SIGKILL，1.5s 时仍在属正常升级周期；轮询等到 8s 再判定
        left, orphan = hub_children()
        for _ in range(13):
            left, _ = hub_children()
            orphan = left & new_kids
            if not orphan:
                break
            time.sleep(0.5)
        check("T4b 该 pty 子进程已消失（不产无主副本，轮询窗口 8s）", orphan == set(),
              "新增过 %s，8s 后残留 %s" % (sorted(new_kids), sorted(orphan)))
        st3, lst = api("/api/term/sessions", "GET", None, tok)
        still = [s for s in (lst.get("sessions") or []) if s.get("id") == sid]
        check("T4c 会话已从列表移除", not still, "列表内该 sid 条目=%s" % still)

        print("== T5 鉴权未被削弱 ==")
        from websockets.sync.client import connect as wsc
        st3, _ = api("/api/term/sessions", "POST", {"agent_id": "shell"}, "wrong-token-xyz")
        denied_http = st3 in (401, 403)
        code = None
        try:
            with wsc(BASE.replace("http", "ws") + "/ws/term/%s?token=wrong" % sid,
                     max_size=100, open_timeout=6, close_timeout=3) as _w:
                pass
        except Exception as e:
            m = re.search(r"code=(\d+)", str(e))
            code = m.group(1) if m else None
        check("T5 错 token 被拒（HTTP 4xx 或 WS 4401）",
              denied_http or code == "4401",
              "POST 错 token -> HTTP %s；WS 错 token -> close code %s" % (st3, code))
    finally:
        api("/api/term/sessions/" + sid, "DELETE", None, tok)

    print()
    if FAILS:
        print("RESULT: FAIL -> " + ", ".join(FAILS))
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
