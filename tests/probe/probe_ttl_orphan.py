#!/usr/bin/env python3
"""验「后台任务被 TUI 带走」这个假设，并补最后一块证据：真正在产出的会话不会被杀。

上一轮（probe_ttl_shell）里 `(for ...)&` 只吐出 1 条就停了，于是 activity_s 一路涨到 57。
两种解释，判据不同：
  H1 后台循环被 claude TUI 带走 ⇒ pty 真静默 ⇒ activity_s 涨是对的，实现无缺陷
  H2 我们的 reap 误杀了它 ⇒ 严重缺陷
区分办法：看会话 alive 期间那条后台进程是否还在（/proc 里找 seq 循环）。
再用**一条真正持续输出**的会话（不靠后台子进程，pty 自己每帧都在动）复测一次。
"""
import asyncio, json, os, time, urllib.request
import websockets

BASE = os.environ.get("PROBE_BASE", "ws://127.0.0.1:3199")
HTTP = BASE.replace("ws://", "http://")
TOKEN = os.environ.get("TERM_TOKEN", "")


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HTTP + path, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "X-Term-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


async def drain(ws, secs):
    buf, end = "", time.time() + secs
    while time.time() < end:
        try:
            m = await asyncio.wait_for(ws.recv(), timeout=max(0.05, end - time.time()))
        except asyncio.TimeoutError:
            continue
        except Exception:
            break
        buf += m.decode("utf-8", "replace") if isinstance(m, bytes) else m
    return buf


def pgrep_seq():
    r = os.popen("pgrep -af 'SHELLPROBE-MARK' 2>/dev/null").read().strip()
    return [l for l in r.split("\n") if l]


async def main():
    sid = api("/api/term/sessions", "POST",
              {"agent_id": "shell", "cwd": os.getcwd()})["session"]["id"]
    print("sid =", sid)
    ws = await websockets.connect(f"{BASE}/ws/term/{sid}?token={TOKEN}", max_size=None)
    await ws.send(json.dumps({"type": "resize", "intent": "claim", "cols": 100, "rows": 30}))
    await drain(ws, 5)

    await ws.send("(for i in $(seq 1 12); do echo SHELLPROBE-$i; sleep 2; done) &\n")
    print("起后台循环后立刻查:", pgrep_seq() or "（没找到）")
    await drain(ws, 10)
    print("10s 后再查      :", pgrep_seq() or "（已消失 ⇒ H1 成立：被 TUI 带走，不是被 reap）")

    cur = {s["id"]: s for s in api("/api/term/sessions")["sessions"]}.get(sid) or {}
    print(f"会话此刻 alive={cur.get('alive')} idle_s={cur.get('idle_s')} "
          f"activity_s={cur.get('activity_s')} exit={cur.get('exit_reason')}")

    # 第二段：换成「pty 自身持续产输出」——用 yes 灌满 pty（不靠后台子进程）
    ws2 = await websockets.connect(f"{BASE}/ws/term/{sid}?token={TOKEN}", max_size=None)
    await ws2.send(json.dumps({"type": "resize", "intent": "claim", "cols": 100, "rows": 30}))
    await drain(ws2, 2)
    # 真持续产出：每轮补一次 200KB，72s 全程 pty 都有字节。
    # 旧写法只灌一次 200KB，5 秒内吐完，之后 pty 真静默 ⇒ activity_s 涨是**正确**的
    # （实测 300KB/5s 一次性流完，之后 activity_s 一路涨到 40）—— 那不是缺陷，是判据写错。
    print("\n灌输出：每轮 200KB × 6 轮（pty 全程持续产字节）")
    marks, got = [], 0
    for i in range(6):
        try:
            await ws2.send("yes 'X' | head -c 200000\n")
        except Exception:
            break
        await asyncio.sleep(0.3)
        buf = await drain(ws2, 12)
        got += len(buf)
        try:
            await ws2.send('{"type":"hb"}')
        except Exception:
            pass
        c = {s["id"]: s for s in api("/api/term/sessions")["sessions"]}.get(sid) or {}
        marks.append((round((i + 1) * 12), c.get("alive"), c.get("idle_s"),
                      c.get("activity_s")))
    print("采样(秒, alive, idle_s, activity_s):")
    for m in marks:
        print("   ", m)
    print(f"\nE 持续产出期间会话存活(72s > TTL45s) : "
          f"{'PASS' if all(m[1] for m in marks) else 'FAIL'}")
    print(f"E activity_s 被产出压住(不涨)        : "
          f"{'PASS' if marks[-1][3] < 20 else 'FAIL'}  activity_s={marks[-1][3]}")
    print(f"E 客户端收到 {got} 字节输出")

    for w in (ws, ws2):
        try: await w.close()
        except Exception: pass
    try:
        api(f"/api/term/sessions/{sid}", "DELETE"); print("清理 已删")
    except Exception as e:
        print("清理", e)


if __name__ == "__main__":
    asyncio.run(main())
