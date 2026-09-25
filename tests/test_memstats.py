#!/usr/bin/env python3
"""L0 hermetic：本地记忆便签的 staleness 观测（src/memstats.py）。

★ 本闸门最重要的钉子是**红向的"不许动数据"**：这个模块一旦长出 DELETE/UPDATE/LLM 调用，
就等于把 09-23 那次「后台 rebuild 静默重写用户手写 L2 记忆」的事故做成常态化。
所以除了算得对，还要静态断言它**没有能力**改库。

改判依据（spec §5，2026-09-25 生产库只读实测）：
    memories rows=4  status={'active':4}  最老=2026-09-06T03:51  最新=2026-09-06T03:57
⇒ 4 行全 active、零软删行、19 天没长过一行；清理任务会永远空转，而记忆权威副本在 TDAI(:8420)。
"""
import pathlib
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

import memstats  # noqa: E402

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


class TestAgeDays(unittest.TestCase):
    def test_aware_and_naive_both_work(self):
        self.assertAlmostEqual(memstats.age_days(_iso(3), NOW), 3.0, places=1)
        naive = (NOW - timedelta(days=2)).replace(tzinfo=None).isoformat()
        self.assertAlmostEqual(memstats.age_days(naive, NOW), 2.0, places=1,
                               msg="无时区形态必须按 UTC 兜住（kb.py 已在 built_at 上栽过一次 TypeError）")

    def test_garbage_returns_none_never_raises(self):
        for bad in (None, "", "not-a-date", "2026-13-45T99:99:99", 12345):
            self.assertIsNone(memstats.age_days(bad, NOW), "输入 %r 该回 None" % (bad,))

    def test_future_timestamp_clamps_to_zero(self):
        self.assertEqual(memstats.age_days(_iso(-1), NOW), 0.0)


class TestLocalStats(unittest.TestCase):
    def test_counts_and_ages(self):
        rows = [{"status": "active", "created_at": _iso(19)},
                {"status": "active", "created_at": _iso(19)},
                {"status": "deleted", "created_at": _iso(1)}]
        s = memstats.local_stats(rows, [], NOW)
        self.assertEqual(s["rows"], 3)
        self.assertEqual(s["by_status"], {"active": 2, "deleted": 1})
        self.assertAlmostEqual(s["oldest_age_days"], 19.0, places=1)
        self.assertAlmostEqual(s["newest_age_days"], 1.0, places=1)

    def test_docs_report_manual_presence(self):
        docs = [{"layer": "L2", "content": "abc", "manual": "手写", "updated_at": _iso(5)},
                {"layer": "L3", "content": "", "manual": "", "updated_at": None}]
        s = memstats.local_stats([], docs, NOW)
        self.assertTrue(s["docs"]["L2"]["has_manual"])
        self.assertEqual(s["docs"]["L2"]["manual_chars"], 2)
        self.assertFalse(s["docs"]["L3"]["has_manual"])
        self.assertIsNone(s["docs"]["L3"]["age_days"])

    def test_empty_inputs_are_safe(self):
        s = memstats.local_stats([], [], NOW)
        self.assertEqual(s["rows"], 0)
        self.assertIsNone(s["oldest_age_days"])


class TestVerdict(unittest.TestCase):
    def test_empty(self):
        v = memstats.verdict(memstats.local_stats([], [], NOW))
        self.assertEqual(v["state"], "empty")
        self.assertIn("TDAI", v["reason"], "空态必须说清权威副本在哪，否则会被读成故障")

    def test_stale_uses_newest_row(self):
        rows = [{"status": "active", "created_at": _iso(30)},
                {"status": "active", "created_at": _iso(19)}]
        v = memstats.verdict(memstats.local_stats(rows, [], NOW))
        self.assertEqual(v["state"], "stale")
        self.assertIn("19", v["reason"])
        self.assertIn(str(memstats.STALE_DAYS), v["reason"])

    def test_fresh(self):
        rows = [{"status": "active", "created_at": _iso(1)}]
        self.assertEqual(memstats.verdict(memstats.local_stats(rows, [], NOW))["state"], "fresh")

    def test_verdict_wording_never_implies_deletion(self):
        """★ 红向：这个模块只报告，不清理 ⇒ 文案里不许出现会被读成"我会删"的字样。"""
        for st in ("empty", "stale", "fresh"):
            rows = [] if st == "empty" else [{"status": "active", "created_at": _iso(19)}]
            v = memstats.verdict(memstats.local_stats(rows, [], NOW))
            for banned in ("已清理", "将删除", "自动删除", "已删除", "清理完成"):
                self.assertNotIn(banned, v["reason"], "%s 态文案暗示会动数据：%s" % (st, banned))


class TestModuleCannotMutate(unittest.TestCase):
    """★ 静态护栏：模块文本里出现写库/LLM/后台任务即判红。"""

    def setUp(self):
        self.src = (_REPO / "src" / "memstats.py").read_text(encoding="utf-8")
        # 只判**去注释后**的代码，否则本模块 docstring 里"绝不 DELETE"的自律声明会被当成违规
        self.code = re.sub(r'"""[\s\S]*?"""', "", self.src)
        self.code = "\n".join(l for l in self.code.splitlines()
                              if not l.strip().startswith("#"))

    def test_no_write_sql(self):
        for kw in ("DELETE FROM", "UPDATE ", "INSERT INTO", "DROP ", "execute("):
            self.assertNotIn(kw, self.code, "memstats 出现写库能力：%s" % kw)

    def test_no_llm_and_no_background_task(self):
        for kw in ("import llm", "llm.", "create_task", "while True", "asyncio.sleep"):
            self.assertNotIn(kw, self.code, "memstats 出现 %s（观测模块不许调 LLM/起后台循环）" % kw)

    def test_only_select_queries(self):
        self.assertIn("db.query", self.src)
        for q in re.findall(r'db\.query\(\s*"([^"]+)', self.src):
            self.assertTrue(q.strip().upper().startswith("SELECT"), "非 SELECT 语句：%s" % q)


if __name__ == "__main__":
    unittest.main(verbosity=2)
