"""回归测（P0-4）：空闲 TTL 与死会话回收必须有独立心跳，不能寄生在前端轮询上。

缺陷出处（实测）：`_reap()` 全仓唯一调用点是 `list_sessions()`（src/term.py:188），
  而 `startup()` 只建了 sweep_stale_tasks / vitals_loop 两个后台任务。
  ⇒ 浏览器一关就没人调 GET /api/term/sessions ⇒ 45min 空闲 TTL 形同虚设、
    死会话不从 _sessions 摘除 ⇒ MAX_SESSIONS(8) 可被占满后新终端直接 429。

全 duck-type 桩，不 fork pty、不起服务、不 import src.main。
跑法：cd ~/agent-hub && venv/bin/python -m unittest tests.test_term_reaper -v
"""
import asyncio
import inspect
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("TERM_TOKEN", "unit-test-token-not-production")

import term  # noqa: E402


class _Stub:
    """只暴露 _reap() 真正用到的那几样：id / alive / last_io / kill() / _cleanup()。"""

    def __init__(self, sid, alive=True, idle_s=0.0):
        self.id = sid
        self.alive = alive
        self.last_io = time.time() - idle_s
        self.killed = 0
        self.cleaned = 0

    def kill(self):
        self.killed += 1

    def _cleanup(self):
        self.cleaned += 1


class TestReap(unittest.TestCase):
    def _run(self, stubs):
        """在干净的 _sessions 上跑一轮 _reap()，返回 (剩余 id 集合, stubs)。"""
        with mock.patch.object(term, "_sessions", {s.id: s for s in stubs}):
            term._reap()
            return {s.id for s in term._sessions.values()}

    def test_dead_session_is_popped_and_cleaned(self):
        s = _Stub("dead1", alive=False)
        left = self._run([s])
        self.assertNotIn("dead1", left, "已死会话必须从登记表摘除（否则 dict 无限增长）")
        self.assertEqual(1, s.cleaned, "死会话必须走幂等 _cleanup()")

    def test_idle_expired_gets_killed(self):
        s = _Stub("idle1", alive=True, idle_s=term.IDLE_TTL_S + 10)
        left = self._run([s])
        self.assertIn("idle1", left, "kill 是异步升级的，本轮不该直接摘除")
        self.assertEqual(1, s.killed, "超过 IDLE_TTL_S 必须被 kill —— 这就是没人轮询时失效的那条")

    def test_fresh_session_untouched(self):
        s = _Stub("fresh", alive=True, idle_s=5)
        left = self._run([s])
        self.assertIn("fresh", left)
        self.assertEqual(0, s.killed)
        self.assertEqual(0, s.cleaned)

    def test_mixed_batch(self):
        a, b, c = _Stub("a", alive=False), _Stub("b", alive=True, idle_s=term.IDLE_TTL_S + 1), \
            _Stub("c", alive=True, idle_s=0)
        left = self._run([a, b, c])
        self.assertEqual({"b", "c"}, left)
        self.assertEqual((1, 0, 0), (a.cleaned, b.cleaned, c.cleaned))


class TestReapLoopExists(unittest.TestCase):
    def test_reap_loop_is_a_coroutine(self):
        self.assertTrue(hasattr(term, "reap_loop"),
                        "缺 reap_loop：回收又只剩寄生在前端轮询上（P0-4 原缺陷）")
        self.assertTrue(inspect.iscoroutinefunction(term.reap_loop))

    def test_interval_is_sane(self):
        self.assertTrue(5 <= term.REAP_INTERVAL_S <= 300,
                        f"回收间隔 {term.REAP_INTERVAL_S}s 不合理")

    def test_loop_actually_reaps_without_any_http_call(self):
        """核心断言：一个 HTTP 请求都不发，跑一轮循环体就要把过期会话 kill 掉。"""
        s = _Stub("z1", alive=True, idle_s=term.IDLE_TTL_S + 5)
        with mock.patch.object(term, "_sessions", {s.id: s}), \
                mock.patch.object(term, "REAP_INTERVAL_S", 0.05):
            async def one_round():
                task = asyncio.create_task(term.reap_loop())
                await asyncio.sleep(0.2)                      # 够跑数轮
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            asyncio.run(one_round())
        self.assertGreaterEqual(s.killed, 1, "reap_loop 没真的调 _reap()")

    def test_loop_survives_internal_exception(self):
        """_reap 抛异常不得让心跳死掉（否则又会回到"静默不回收"）。"""
        with mock.patch.object(term, "_reap", side_effect=RuntimeError("boom")), \
                mock.patch.object(term, "REAP_INTERVAL_S", 0.05):
            async def one_round():
                task = asyncio.create_task(term.reap_loop())
                await asyncio.sleep(0.2)
                self.assertFalse(task.done(), "一轮异常就把心跳任务弄死了 = 又回到静默失效")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            asyncio.run(one_round())


class TestWiredIntoStartup(unittest.TestCase):
    def test_main_starts_the_reaper(self):
        """回归销：有人把 create_task 删了，本测必须红。"""
        body = (Path(__file__).resolve().parents[1] / "src" / "main.py").read_text(encoding="utf-8")
        self.assertTrue("term_mod.reap_loop()" in body,
                        "startup() 里没人起 reap_loop —— P0-4 白修")


class TestSelfAttestHelpersUsed(unittest.TestCase):
    def test_alive_count_and_idle_max_exist(self):
        self.assertEqual(0, term.alive_count())
        self.assertEqual(0, term.idle_max_s())
        s = _Stub("q", alive=True, idle_s=42)
        with mock.patch.object(term, "_sessions", {s.id: s}):
            self.assertEqual(1, term.alive_count())
            self.assertAlmostEqual(42, term.idle_max_s(), delta=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
