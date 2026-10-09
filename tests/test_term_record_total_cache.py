#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L0 hermetic：全局录制预算的进程内缓存（v0.13.100）。

**背景（为什么这批用例长这样）**：用户 2026-10-09 报障「嵌入式终端输入非常慢」。
取证结论：`Recorder.feed()` 每次都跑 `SELECT SUM(bytes) FROM term_recordings`
（`EXPLAIN` = `SCAN term_recordings`，无索引可用），而 feed 被**同步**调在 asyncio
事件循环里 ⇒ 44 万行时单次 110~124ms，每个按键与每帧输出各压一次，端到端逐键
RTT p50 实测 677ms。本批把那条 SUM 收进进程内缓存。

钉的不是「缓存命中率」这种自我表扬的指标，而是**四条会让预算失效的静默路径**：
缓存一旦比真值偏高，全局预算就形同虚设（用户以为录了，其实超配额），
而且偏高之后**没有任何反向修正能把它拉回来**。

1. **等价性**：同一串 feed，缓存口径的 `total_bytes()` 必须与真查 SUM 逐次相等。
2. **换库即失效**：`db.init_db()` 会把模块级 `_conn` 重指到另一个文件（测试与
   `/mcp/call` 都靠它切库）。切库后缓存必须作废，否则拿旧库的字节数去判新库的预算。
3. **DELETE 后必作废**：`sweep()` 与「同 sid 覆盖」都删行。增量法在删除后永久偏高，
   所以这两处一律作废而不是做减法修正（行数≠字节数，减不出来）。
4. **写失败不推进缓存**：`_exec` 失败时缓存不许加，否则缓存悄悄高于真值。

隔离：DB 走 tmp（`init_db` 换模块级 `_conn`，测后 rmtree）。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import db  # noqa: E402
import term_record  # noqa: E402


def _mktmp(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix))


def _true_total() -> int:
    """绕过缓存直接查真值。凡是断言缓存正确性的地方都用它当**对照**。"""
    return term_record._query_total_bytes()


class TestTotalCacheEquivalence(unittest.TestCase):
    """判据 1：缓存口径必须与真查逐次相等（而不是「差不多」）。"""

    def setUp(self):
        self.tmp = _mktmp("termrec-cache-eq-")
        db.init_db(self.tmp / "t.db")
        term_record._total_invalidate()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.addCleanup(term_record._total_invalidate)
        self.env = mock.patch.dict(
            os.environ, {"TERM_RECORD": "1", "TERM_RECORD_MAX_SESSION": "1048576",
                         "TERM_RECORD_MAX_TOTAL": "104857600"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_matches_true_sum_after_every_feed(self):
        rec = term_record.Recorder("sid-eq", "pi")
        for i in range(12):
            rec.feed("out", b"payload-%03d" % i)
            self.assertEqual(term_record.total_bytes(), _true_total(),
                             "第 %d 帧后缓存口径与真查不一致" % i)

    def test_grows_monotonically_with_writes(self):
        """缓存必须是**累加**而不是恒定：漏掉 _total_add 会让缓存永远停在初值，
        于是全局预算在真值早已超限时仍判「未满」⇒ 超配额且无任何告警。"""
        rec = term_record.Recorder("sid-mono", "pi")
        seen = []
        for i in range(6):
            rec.feed("out", b"x" * 100)
            seen.append(term_record.total_bytes())
        self.assertEqual(seen, sorted(seen), "缓存必须单调不减")
        self.assertGreater(seen[-1], seen[0], "缓存必须随写入增长")
        self.assertEqual(seen[-1], _true_total())

    def test_two_recorders_share_one_truth(self):
        """两个 Recorder 各自的增量必须汇进**同一份**缓存（模块级，不是实例级）。"""
        a = term_record.Recorder("sid-a", "pi")
        b = term_record.Recorder("sid-b", "pi")
        a.feed("out", b"a" * 200)
        b.feed("out", b"b" * 300)
        self.assertEqual(term_record.total_bytes(), _true_total())
        self.assertEqual(term_record.total_bytes(), 500)


class TestCacheInvalidation(unittest.TestCase):
    """判据 2/3/4：三条会让缓存永久高于真值的路径。"""

    def setUp(self):
        self.tmp = _mktmp("termrec-cache-inv-")
        db.init_db(self.tmp / "t.db")
        term_record._total_invalidate()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.addCleanup(term_record._total_invalidate)
        self.env = mock.patch.dict(
            os.environ, {"TERM_RECORD": "1", "TERM_RECORD_MAX_SESSION": "1048576",
                         "TERM_RECORD_MAX_TOTAL": "104857600"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_switching_db_drops_cache(self):
        """换库后必须重查：否则拿 A 库的字节数判 B 库的预算。"""
        rec = term_record.Recorder("sid-x", "pi")
        rec.feed("out", b"first-db" * 10)
        self.assertGreater(term_record.total_bytes(), 0)
        before = term_record.total_bytes()

        db.init_db(self.tmp / "other.db")      # 切库：新库是空的
        self.assertEqual(term_record.total_bytes(), 0,
                         "切库后缓存必须作废，不能沿用旧库的字节数")
        self.assertNotEqual(term_record.total_bytes(), before)

    def test_same_db_reinit_keeps_truth(self):
        """切到**同一个**库文件不算换库，缓存应继续有效（判据是路径，不是连接对象）。"""
        rec = term_record.Recorder("sid-y", "pi")
        rec.feed("out", b"keep" * 10)
        path = db.current_path()
        db.init_db(Path(path))
        self.assertEqual(term_record.total_bytes(), _true_total())

    def test_sweep_deletes_and_cache_follows_down(self):
        """sweep 删行后缓存必须回落。旧实现每帧真查，天然跟得上；
        缓存若忘了作废，就会**永久偏高** —— 而偏高不可逆。"""
        rec = term_record.Recorder("sid-old", "pi")
        rec.feed("out", b"ancient-frame")
        self.assertGreater(term_record.total_bytes(), 0)
        old_ts = time.time() - 8 * 86400
        db.execute("UPDATE term_recordings SET ts=? WHERE session_id='sid-old'", (old_ts,))

        term_record.sweep()
        self.assertEqual(term_record.total_bytes(), 0,
                         "sweep 后缓存必须作废重查，不能停在删前的值")

    def test_same_sid_recreate_drops_previous(self):
        """同 sid 二次录制清上一份（既有裁定）。缓存必须作废，
        否则第二次录制的预算检查会顶着第一次的字节数。"""
        first = term_record.Recorder("sid-dup", "pi")
        first.feed("out", b"x" * 500)
        self.assertEqual(term_record.total_bytes(), 500)
        second = term_record.Recorder("sid-dup", "pi")
        self.assertEqual(term_record.total_bytes(), 0)
        self.assertEqual(term_record.total_bytes(), _true_total())

    def test_failed_write_does_not_advance_cache(self):
        """写失败时缓存不许加：缓存一旦高于真值就没有任何机制能拉回来。"""
        rec = term_record.Recorder("sid-fail", "pi")
        rec.feed("out", b"warm")               # 先成功一帧，缓存 = 4
        self.assertEqual(term_record.total_bytes(), 4)
        with mock.patch.object(term_record, "_exec", return_value=False):
            rec.feed("out", b"must-not-count")  # 写失败
        self.assertEqual(term_record.total_bytes(), 4,
                         "写失败后缓存必须原地不动")
        self.assertEqual(term_record.total_bytes(), _true_total())


class TestCachePerFrameCost(unittest.TestCase):
    """判据 5：缓存的**意义**就是每帧不再查库。用查询次数钉，不拿耗时钉
    （耗时随机器/卷变化，拿它当判据就是拿噪声当证据）。"""

    def setUp(self):
        self.tmp = _mktmp("termrec-cache-cost-")
        db.init_db(self.tmp / "t.db")
        term_record._total_invalidate()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.addCleanup(term_record._total_invalidate)
        self.env = mock.patch.dict(
            os.environ, {"TERM_RECORD": "1", "TERM_RECORD_MAX_SESSION": "1048576",
                         "TERM_RECORD_MAX_TOTAL": "104857600"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_full_table_sum_runs_at_most_once_for_many_frames(self):
        """20 帧的 SUM 查询次数必须 ≤1（首次建立缓存时那一次）。

        旧实现是 20 次 —— 这条就是本批的红向自证：把 `_total_add` 与
        `_total_invalidate` 去掉、total_bytes 退回每帧真查，本条必红。
        """
        rec = term_record.Recorder("sid-cost", "pi")
        with mock.patch.object(db, "query", wraps=db.query) as spy:
            for i in range(20):
                rec.feed("out", b"frame-%02d" % i)
            sums = [c for c in spy.call_args_list
                    if c.args and "SUM(bytes)" in str(c.args[0])]
        self.assertLessEqual(
            len(sums), 1,
            "20 帧触发了 %d 次全表 SUM（应 ≤1）；这条判据在旧实现下是 20" % len(sums))


if __name__ == "__main__":
    unittest.main(verbosity=2)
