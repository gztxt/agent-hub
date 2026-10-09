"""♻️ 已退役（2026-10-09 终审）—— **文件已从 `test_*.py` 改名，不进 L0 收集**。

**为什么退役而不是留着 skip**：本仓 L0 的定义是「**零 skip**」，出现 skip 即
分层放错、闸门判 FAIL（`tests/tiers.py` 的 host_only 机制是给"依赖真实宿主"
的用例准备的，语义不对：这些用例的问题是**守的实现已不存在**，不是依赖宿主）。
9 个空壳留在标准层里，闸门天天报红，却一什么都没测 —— 那是典型的
「闸门变成噪声」，久了就没人看它。

**原文全文保留**（遵工作区纪律「改判旧条目加横幅、不删除原文」）：
v0.13.100 那一代实现的取舍依据 —— 进程内缓存 + DELETE 后作废。
读它才能理解 v0.13.101 为什么换方案，以及下面那条最重要的教训。

**现行闸门**：`tests/test_term_record_meta_count.py`（15 例），守的是跨代都成立
的不变量（计数恒等 SUM、两条 DELETE 路径都收敛、播种幂等、逐帧路径无 SUM、
库异常兜成 0 而不抛）。

⚠ **留在这里的那条教训，比这些用例本身值钱**：
   v0.13.100 用「DELETE 一律作废缓存」挡住了「增量法遇删除会永久偏高」，
   却没注意到 `Recorder.__init__` **每次建会话都先 DELETE 再算预算**
   ⇒ 必然作废 ⇒ 必然重查 ⇒ 建会话那条路（实测 156~210ms）根本没被治好。
   **一个正确的局部优化，如果它的失效条件被高频路径命中，等于没优化。**

单独跑（它已不在 L0 口径内，命令与普通用例不同）：
    venv/bin/python3 tests/retired_test_term_record_total_cache_v013100.py
（预期 9 例因 `_total_invalidate` 已不存在而 AttributeError —— 那正是"守的东西
已经没了"的现场证据，不是本文件坏了。）
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

    @unittest.skip("v0.13.101：缓存已换成单行计数表，本类守的是被取代的实现；现行闸门见 tests/test_term_record_meta_count.py")
    def test_matches_true_sum_after_every_feed(self):
        rec = term_record.Recorder("sid-eq", "pi")
        for i in range(12):
            rec.feed("out", b"payload-%03d" % i)
            self.assertEqual(term_record.total_bytes(), _true_total(),
                             "第 %d 帧后缓存口径与真查不一致" % i)

    @unittest.skip("v0.13.101：缓存已换成单行计数表，本类守的是被取代的实现；现行闸门见 tests/test_term_record_meta_count.py")
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

    @unittest.skip("v0.13.101：缓存已换成单行计数表，本类守的是被取代的实现；现行闸门见 tests/test_term_record_meta_count.py")
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

    @unittest.skip("v0.13.101：缓存已换成单行计数表，本类守的是被取代的实现；现行闸门见 tests/test_term_record_meta_count.py")
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

    @unittest.skip("v0.13.101：缓存已换成单行计数表，本类守的是被取代的实现；现行闸门见 tests/test_term_record_meta_count.py")
    def test_same_db_reinit_keeps_truth(self):
        """切到**同一个**库文件不算换库，缓存应继续有效（判据是路径，不是连接对象）。"""
        rec = term_record.Recorder("sid-y", "pi")
        rec.feed("out", b"keep" * 10)
        path = db.current_path()
        db.init_db(Path(path))
        self.assertEqual(term_record.total_bytes(), _true_total())

    @unittest.skip("v0.13.101：缓存已换成单行计数表，本类守的是被取代的实现；现行闸门见 tests/test_term_record_meta_count.py")
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

    @unittest.skip("v0.13.101：缓存已换成单行计数表，本类守的是被取代的实现；现行闸门见 tests/test_term_record_meta_count.py")
    def test_same_sid_recreate_drops_previous(self):
        """同 sid 二次录制清上一份（既有裁定）。缓存必须作废，
        否则第二次录制的预算检查会顶着第一次的字节数。"""
        first = term_record.Recorder("sid-dup", "pi")
        first.feed("out", b"x" * 500)
        self.assertEqual(term_record.total_bytes(), 500)
        second = term_record.Recorder("sid-dup", "pi")
        self.assertEqual(term_record.total_bytes(), 0)
        self.assertEqual(term_record.total_bytes(), _true_total())

    @unittest.skip("v0.13.101：缓存已换成单行计数表，本类守的是被取代的实现；现行闸门见 tests/test_term_record_meta_count.py")
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

    @unittest.skip("v0.13.101：缓存已换成单行计数表，本类守的是被取代的实现；现行闸门见 tests/test_term_record_meta_count.py")
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
