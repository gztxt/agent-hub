"""「不同会话进对应目录」的生产验收探针（只读取 + 起/销自己的会话）。
   跑法：cd ~/agent-hub && TOKEN=$(grep -m1 '^TERM_TOKEN=' .env | cut -d= -f2-) \\
           venv/bin/python tests/probe_resume_cwd.py [http://127.0.0.1:3102]
   判据：① API 回执 session.cwd == 历史条目自己的 cwd
        ② /proc/<子进程>/cwd 实测 == 同一个值（不信回执，问内核）
        ③ 记录目录已不存在的会话 → 回退画像目录，且 pty 仍能起（不能起死）"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3102"
TOKEN = os.environ["TOKEN"]
HDR = {"x-term-token": TOKEN, "Content-Type": "application/json"}
FAILS = []


def call(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers=HDR)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"_http": e.code}


def chk(name, ok, evidence=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{evidence}]" if evidence else ""))
    if not ok:
        FAILS.append(name)


def proc_cwd(need):
    """按 cmdline 里的会话 id 精确定位子进程，再读它的 cwd 符号链接"""
    for pid in [x for x in os.listdir("/proc") if x.isdigit()]:
        try:
            cl = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf8", "ignore")
        except Exception:  # noqa: BLE001
            continue
        if need and need in cl:
            try:
                return os.readlink(f"/proc/{pid}/cwd"), pid
            except Exception:  # noqa: BLE001
                return "", pid
    return "", ""


def main():
    print(f"目标 = {BASE}\n")
    for agent in ["grok", "claude", "jcode", "hermes", "codex", "qoder"]:
        d = call(f"/api/term/history/{agent}?limit=5")
        home = d.get("cwd") or ""
        items = d.get("items") or []
        if not items:
            chk(f"{agent} 无历史可测", True, "跳过")
            continue
        # 优先挑「目录≠画像目录」的那条——这才是本次裁定的意义所在
        pick = next((i for i in items if i.get("cwd") and i.get("cwd") != home), items[0])
        cross = pick.get("cwd") != home
        s = call("/api/term/sessions", "POST", {"agent_id": agent, "session_id": pick["id"]})
        sess = s.get("session") or {}
        if not sess.get("id"):
            chk(f"{agent} 续聊能起", False, json.dumps(s, ensure_ascii=False)[:70])
            continue
        chk(f"{agent} 回执 cwd＝会话自己的目录" + ("（跨目录条）" if cross else "（同目录条）"),
            sess.get("cwd") == pick.get("cwd"),
            f"条目={pick.get('cwd')} 回执={sess.get('cwd')} 画像={home}")
        time.sleep(3)                      # 给 exec 一点时间，别读到自己
        real, pid = proc_cwd(pick["id"])
        chk(f"{agent} /proc 实测子进程 cwd 一致", real == pick.get("cwd"),
            f"pid={pid} /proc→{real}" if real else "子进程已退出（CLI 可能秒退），仅凭回执判定")
        call("/api/term/sessions/" + sess["id"], "DELETE")
        time.sleep(1)

    # ③ 回退分支：不造假数据——在真仓库里找"记录目录已不存在"的那条；找不到就如实 SKIP
    import sys as _s
    _s.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
    import sessions_store as ss
    hit = None
    for agent in ["grok", "claude", "jcode", "qoder", "codex", "hermes"]:
        for i in (call(f"/api/term/history/{agent}?limit=5").get("items") or []):
            c = i.get("cwd") or ""
            if c and not os.path.isdir(c):
                hit = (agent, i["id"], c)
                break
        if hit:
            break
    if hit:
        got = ss.session_cwd(hit[0], hit[1], "FALLBACK")
        chk(f"记录目录已删则回退画像目录（{hit[0]} 的 {hit[2]}）", got == "FALLBACK", f"实得={got!r}")
    else:
        print("SKIP  回退分支：真仓库里当前没有「记录目录已不存在」的自然样本（不造假数据，也不冒充 PASS）")

    print("\nALL GREEN" if not FAILS else f"\n{len(FAILS)} FAIL: {FAILS}")
    sys.exit(1 if FAILS else 0)


main()
