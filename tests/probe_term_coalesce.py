"""实弹探针：B1 输出合并到底省了多少帧（L1，不进 L0，手动跑）。

方案 §7 要求「实证，非看起来没问题」：合并前后帧数应显著下降，且字节一个不少。
本探针 fork 一个真 pty 跑高频输出，同一份代码跑两遍做对照：

  delay_ms=0  ⇒ 前沿恒真，读一块刷一块 ≡ **合并前**的行为
  delay_ms=5  ⇒ 合并后的行为

两遍走的是同一条代码路径，唯一的变量就是窗口大小，所以差值只能来自合并本身。

跑法：cd ~/agent-hub && venv/bin/python tests/probe_term_coalesce.py
"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("TERM_TOKEN", "probe-token-not-production")

import term  # noqa: E402


class CountingCoalescer(term._OutputCoalescer):
    """只为计数：handle 次数 = PTY 吐的块数（合并前的帧数），flush 次数 = 实际帧数。"""

    def __init__(self, on_flush, delay_ms):
        super().__init__(on_flush, delay_ms)
        self.handles = 0
        self.flushes = 0
        self.bytes = 0

    def handle(self, data):
        self.handles += 1
        self.bytes += len(data)
        super().handle(data)

    def flush(self):
        had = bool(self._buf)
        super().flush()
        if had:
            self.flushes += 1


async def measure(delay_ms: float, cmd):
    sess = term.Session(sid="probe-%d" % int(delay_ms * 1000), agent_id="probe", cmd=cmd, cwd="/tmp")
    frames = []
    cc = CountingCoalescer(frames.append, delay_ms)
    sess.coalescers["v"] = cc
    term._attach_reader(sess)
    deadline = time.time() + 20
    while sess.alive and time.time() < deadline:
        await asyncio.sleep(0.02)
    cc.flush()
    await asyncio.sleep(0.05)
    sess.hub_killed = True
    sess.kill()
    await asyncio.sleep(0.05)
    return cc, b"".join(frames)


async def main():
    cmd = ["/bin/sh", "-c", "seq 1 60000"]
    base_c, base_bytes = await measure(0.0, cmd)
    new_c, new_bytes = await measure(term.TERM_COALESCE_MS, cmd)

    def row(tag, c, payload):
        print(f"{tag:<10} PTY块数={c.handles:<7} 实际帧数={c.flushes:<7} "
              f"字节={c.bytes:<9} 平均帧大小={c.bytes // max(c.flushes, 1)}B")
        return payload

    row("合并前", base_c, base_bytes)
    row("合并后", new_c, new_bytes)
    if base_c.flushes:
        print(f"帧数下降：{new_c.flushes}/{base_c.flushes} = "
              f"{new_c.flushes / base_c.flushes:.2%}（越小越好）")
    print("字节守恒：", "✅ 一致" if base_c.bytes == new_c.bytes else f"❌ {base_c.bytes} vs {new_c.bytes}")


if __name__ == "__main__":
    asyncio.run(main())
