#!/usr/bin/env python3
"""L0 hermetic：D6 可见性铺设脚本（scripts/skill_visibility_sync.py）。

纯逻辑断言：禁改面必须被拒、幂等必须成立、冲突必须**不覆盖**、默认必须干跑。
全部用 tmp 目录做夹具，不碰任何真实 Agent 目录。
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))
import skill_visibility_sync as S  # noqa: E402


class TestTargetsAreRealRoots(unittest.TestCase):
    def test_opencode_is_not_a_target(self):
        """B3 已证伪 ~/.config/opencode/skill：opencode 1.18.34 不扫它。
        把它留在目标表里 = 铺一堆永远不生效的软链（静默不可用）。"""
        dirs = [str(t[1]) for t in S.TARGETS]
        self.assertNotIn(str(pathlib.Path.home() / ".config/opencode/skill"), dirs)

    def test_every_target_carries_evidence(self):
        for route, _d, ev, _g in S.TARGETS:
            self.assertTrue(ev.strip(), "%s 目标没写证据来源" % route)

    def test_guarded_targets_are_the_four_protected_ones(self):
        g = sorted(t[0] for t in S.TARGETS if t[3])
        self.assertEqual(g, ["codebuddy", "hermes", "jcode", "qwenpaw"])


class TestPlanSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vis-")
        self.src = pathlib.Path(self.tmp) / "src"
        self.src.mkdir()
        (self.src / "demo").mkdir()
        (self.src / "demo" / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
        self.src_file_only = self.src / "notaskill"
        self.src_file_only.mkdir()
        (self.src_file_only / "README.md").write_text("x", encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _with_src(self, fn):
        old = S.SRC_ROOT
        S.SRC_ROOT = self.src
        try:
            return fn()
        finally:
            S.SRC_ROOT = old

    def test_guarded_route_is_refused_with_reason(self):
        tgt = pathlib.Path(self.tmp) / "t"
        tgt.mkdir()
        p = self._with_src(lambda: S.plan([("x", tgt, "ev", True)], ["demo"]))
        self.assertEqual(p["blocked"][0]["route"], "x")
        self.assertIn("禁改", p["blocked"][0]["why"])
        self.assertEqual(p["would_create"], [])

    def test_would_create_for_fresh_target(self):
        tgt = pathlib.Path(self.tmp) / "t"
        tgt.mkdir()
        p = self._with_src(lambda: S.plan([("x", tgt, "ev", False)], ["demo"]))
        self.assertEqual(len(p["would_create"]), 1)

    def test_idempotent_second_pass(self):
        tgt = pathlib.Path(self.tmp) / "t"
        tgt.mkdir()
        t = [("x", tgt, "ev", False)]
        p1 = self._with_src(lambda: S.plan(t, ["demo"]))
        self._with_src(lambda: S.apply_plan(p1))
        p2 = self._with_src(lambda: S.plan(t, ["demo"]))
        self.assertEqual(p2["would_create"], [])
        self.assertEqual(len(p2["already_ok"]), 1)

    def test_conflict_is_reported_and_never_overwritten(self):
        tgt = pathlib.Path(self.tmp) / "t"
        tgt.mkdir()
        other = pathlib.Path(self.tmp) / "other"
        other.mkdir()
        (other / "SKILL.md").write_text("x", encoding="utf-8")
        os.symlink(other, tgt / "demo")
        p = self._with_src(lambda: S.plan([("x", tgt, "ev", False)], ["demo"]))
        self.assertEqual(len(p["conflict"]), 1)
        self.assertEqual(p["would_create"], [])
        self._with_src(lambda: S.apply_plan(p))          # 即便 apply 也不得动冲突项
        self.assertEqual(os.path.realpath(tgt / "demo"), str(other.resolve()))

    def test_real_dir_also_counts_as_conflict_not_overwrite(self):
        tgt = pathlib.Path(self.tmp) / "t"
        (tgt / "demo").mkdir(parents=True)
        p = self._with_src(lambda: S.plan([("x", tgt, "ev", False)], ["demo"]))
        self.assertEqual(len(p["conflict"]), 1)
        self.assertEqual(p["would_create"], [])

    def test_missing_src_skillmd_skipped(self):
        tgt = pathlib.Path(self.tmp) / "t"
        tgt.mkdir()
        p = self._with_src(lambda: S.plan([("x", tgt, "ev", False)], ["notaskill"]))
        self.assertEqual(p["missing_src"], ["notaskill"])
        self.assertEqual(p["would_create"], [])

    def test_missing_target_dir_reported(self):
        p = self._with_src(lambda: S.plan([("x", pathlib.Path(self.tmp) / "nope", "ev", False)], ["demo"]))
        self.assertEqual(len(p["target_missing"]), 1)
        self.assertEqual(p["would_create"], [])


class TestDryRunDefault(unittest.TestCase):
    def test_main_without_apply_writes_nothing(self):
        import io
        import contextlib
        before = sorted(os.listdir(pathlib.Path.home() / ".claude/skills"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            S.main(["--target", "claude"])
        after = sorted(os.listdir(pathlib.Path.home() / ".claude/skills"))
        self.assertEqual(before, after, "干跑却改了真实 Agent 目录")
        self.assertIn("干跑", buf.getvalue())

    def test_unknown_target_exits_nonzero(self):
        self.assertEqual(S.main(["--target", "nosuchroute"]), 2)
