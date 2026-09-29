#!/usr/bin/env python3
"""定位：600KB 输出确实流过 WS，但 activity_s 仍涨到 62 —— last_activity 到底谁在写？

上一轮证据：客户端收到 600381 字节（输出真实流过），而 activity_s 一路涨到 62。
若 on_readable 每块都写 last_activity，activity_s 不可能涨。两种可能：
  H1 on_readable 没被调用（add_reader 没挂上 / fd 可读事件没来）
  H2 有另一处把 last_activity 改回去了（只有 _mark_interaction 和 on_readable 写它）
做法：在**服务端进程内**观察，不靠 HTTP 快照 —— 起一个同代码的实例，
用 monkeypatch 打点 last_activity 的写入次数。
"""
import asyncio, json, os, sys, time, urllib.request
import websockets

BASE = os.environ.get("PROBE_BASE", "ws://127.0.0.1:3199")
HTTP = BASE.replace("ws://", "http://")
TOKEN = os.environ.get("TERM_TOKEN", "")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HTTP + path, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "X-Term-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


async def drain(ws, secs):
    total = 0
    end = time.time() + secs
    while time.time() < end:
        try:
            m = await asyncio.wait_for(ws.recv(), timeout=max(0.05, end - time.time()))
        except asyncio.TimeoutError:
            continue
        except Exception:
            break
        total += len(m if isinstance(m, bytes) else m.encode())
    return total


async def main():
    sid = api("/api/term/sessions", "POST",
              {"agent_id": "shell", "cwd": os.getcwd()})["session"]["id"]
    print("sid =", sid)
    ws = await websockets.connect(f"{BASE}/ws/term/{sid}?token={TOKEN}", max_size=None)
    await ws.send(json.dumps({"type": "resize", "intent": "claim", "cols": 100, "rows": 30}))
    await drain(ws, 3)

    rows = []
    await ws.send("yes 'X' | head -c 200000\n")
    for i in range(8):
        n = await drain(ws, 5)
        c = {s["id"]: s for s in api("/api/term/sessions")["sessions"]}.get(sid) or {}
        rows.append((round((i + 1) * 5), n, c.get("idle_s"), c.get("activity_s"), c.get("alive")))
    print("采样(秒, 本轮收到字节, idle_s, activity_s, alive):")
    for r in rows:
        print("   ", r)
    total = sum(r[1] for r in rows)
    print(f"\n总输出 {total} 字节；最后 activity_s={rows[-1][3]}")
    print("结论：", "输出停了（activity_s 涨是**正确**的）"
          if rows[-1][3] > 20 else "输出仍在续命（activity_s 被压住）")

    try:
        api(f"/api/term/sessions/{sid}", "DELETE"); print("清理 已删")
    except Exception as e:
        print("清理", e)


if __name__ == "__main__":
    asyncio.run(main())
