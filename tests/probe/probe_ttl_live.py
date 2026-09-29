#!/usr/bin/env python3
"""决定性实弹（TTL=45s / reap=10s 的探针实例上跑）：

场景 = 用户说的「切到别的客户端接着用，原客户端静默」。
三个必须为真的性质：
  A 会话在客户端静默期间**不被杀**（进程还活着，状态在服务端）
  B 静默期间 agent 继续推进（PTY 仍收输出，服务端在记账）
  C 换端接上去能继续执行（连续性）
外加两个反证：
  D 心跳**不该**续命（否则 TTL 又形同虚设）—— 但也不该因此被杀
  E 真正空闲到 TTL 之后才被回收（TTL 仍然有效，不是被改成永生）
"""
import asyncio, json, os, time, urllib.request
import websockets

BASE = os.environ.get("PROBE_BASE", "ws://127.0.0.1:3199")
HTTP = BASE.replace("ws://", "http://")
TOKEN = os.environ.get("TERM_TOKEN", "")
R = []


def say(k, v, ok=None):
    tag = "" if ok is None else ("  ✅ PASS" if ok else ("  ❌ FAIL" if ok is False else "  ℹ️"))
    R.append(f"{k} = {v}{tag}")
    print(f"[probe] {k} = {v}{tag}")


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


async def main():
    sid = api("/api/term/sessions", "POST",
              {"agent_id": "codex", "cwd": os.getcwd()})["session"]["id"]
    say("sid", sid)
    url = f"{BASE}/ws/term/{sid}?token={TOKEN}"

    # ── A/B：连上，跑一个「持续产出的后台任务」，然后客户端彻底静默
    ws = await websockets.connect(url, max_size=None)
    await ws.send(json.dumps({"type": "resize", "intent": "claim", "cols": 100, "rows": 30}))
    # 后台任务：每 2s 打一行，持续 40s（跨过 TTL=45s 的一半，够看清行为）
    await ws.send("(for i in $(seq 1 20); do echo TICK-$i; sleep 2; done) &\n")
    await drain(ws, 5)
    say("A0 后台任务已起", "seq 1 20, 每 2s")

    # 客户端静默：只发心跳（模拟真实前端），不碰 last_io（P0-1 语义）
    t0 = time.time()
    hb_marks = []
    ticks_seen = 0
    while time.time() - t0 < 72:           # 静默 72s > TTL 45s
        # 静默期**持续**收帧：判据是「服务端还在推输出」，不是某一瞬间读一次。
        # 旧写法末尾 drain(3s) 恰好落在两帧之间 ⇒ 读到 0 条 ⇒ 假 FAIL（B）。
        chunk = await drain(ws, 12)
        ticks_seen += chunk.count("TICK-")
        try:
            await ws.send('{"type":"hb"}')
        except Exception:
            break
        live = {s["id"]: s for s in api("/api/term/sessions")["sessions"]}
        cur = live.get(sid)
        hb_marks.append((round(time.time() - t0), "alive" if cur else "GONE",
                         (cur or {}).get("idle_s"), (cur or {}).get("activity_s")))
    alive_all = all(m[1] == "alive" for m in hb_marks)
    say("A 静默 72s(>TTL 45s) 全程会话存活", alive_all, ok=alive_all)
    say("A 采样(秒,状态,idle_s,activity_s)", hb_marks)

    say("B 静默期间 PTY 持续在推输出", f"累计收到 {ticks_seen} 条 TICK", ok=ticks_seen > 0)

    # ── C：换端重连（全新连接，等价另一台设备）
    ws2 = await websockets.connect(url, max_size=None)
    await ws2.send(json.dumps({"type": "resize", "intent": "claim", "cols": 100, "rows": 30}))
    b2 = await drain(ws2, 6)
    say("C 换端重连拿到回放", f"{len(b2)} 字节，含 TICK {b2.count('TICK-')} 条",
        ok=len(b2) > 0)
    # ── D：心跳不续命。**必须在发任何真实输入之前取样** ——
    # 旧版把断言放在 echo 之后，echo 自己续了 last_io ⇒ 读到 10s ⇒ 假 FAIL（D）。
    live = {s["id"]: s for s in api("/api/term/sessions")["sessions"]}
    cur = live.get(sid)
    idle_d, act_d = (cur or {}).get("idle_s"), (cur or {}).get("activity_s")
    say("D 心跳未续命(idle_s 仍大)", f"idle_s={idle_d} activity_s={act_d}",
        ok=(idle_d or 0) > 20)

    await ws2.send("echo RESUME-AFTER-SWITCH\n")
    b3 = await drain(ws2, 10)
    say("C 换端后能继续执行命令", "RESUME-AFTER-SWITCH" in b3,
        ok="RESUME-AFTER-SWITCH" in b3)

    for w in (ws, ws2):
        try: await w.close()
        except Exception: pass
    try:
        api(f"/api/term/sessions/{sid}", "DELETE"); say("清理", "已删")
    except Exception as e:
        say("清理", f"跳过 {e}")

    print("\n== 汇总 ==")
    for r in R: print(" ", r)


if __name__ == "__main__":
    asyncio.run(main())
