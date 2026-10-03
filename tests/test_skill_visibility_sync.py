#!/usr/bin/env python3
"""L0 hermetic：D6 可见性铺设脚本（scripts/skill_visibility_sync.py）。

纯逻辑断言：禁改面必须被拒、幂等必须成立、冲突必须**不覆盖**、默认必须干跑。
全部用 tmp 目录做夹具，不碰任何真实 Agent 目录。
"""
import json
import os
import pathlib
import shutil
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
    """干跑不得写任何东西——**在「真写确实能成功」的前提下**验。

    【这组用例踩过的坑，按序说】

    坑一（2026-10-03 已修）：直接 `os.listdir(Path.home()/".claude/skills")`，
    在 `hermetic-clean`（`--fake-home`）档报 `FileNotFoundError`——因为那句
    **「$HOME/.claude/skills 一定存在」本身就是一句宿主假设**，而假 HOME 是空目录。
    与本仓已记的 memindex 投影读到生产索引（09-25）、`ARCHIVE_ROOTS` 写死本机绝对
    路径（v0.13.71，开发机必红/CI 必绿）同族：**L0 里混进了「这台机器长这样」**。

    坑二（**修完坑一才发现，这才是真问题**）：把 `os.listdir` 换成「不存在就当空」
    之后测试变绿了，但那是**恒真**。把 `apply_plan` 挪到 `--apply` 判断之前
    （= 制造「干跑却真写了」的回归）重跑，**测试依然绿**。
    根因：`apply_plan` 里的 `os.symlink` 在父目录不存在时抛 `FileNotFoundError`，
    被 `except OSError` 收进 `failed` ⇒ **那个环境下压根写不进去**，
    于是「干跑没写」不是因为干跑克制，而是因为它想写也写不成。
    这与 v0.13.71 的假绿夹具、09-23 `vitals_loop`、09-24 TDZ 是同一个家族。

    ⇒ 本组断言的前提必须改成：**目标目录真实存在**（测试自己建），
    源目录里**真有**一条待链技能。这样「干跑后没多出软链」才是有效结论。
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="vis-dry-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # 源：真有 2 条带 SKILL.md 的技能（没有它们 would_create 为空，断言又是恒真）
        self.src = self.tmp / "src"
        (self.src / "demo").mkdir(parents=True)
        (self.src / "demo" / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
        (self.src / "demo2").mkdir()
        (self.src / "demo2" / "SKILL.md").write_text("---\nname: demo2\n---\n", encoding="utf-8")
        # 目标：**真实存在**（这是断言能成立的前提，见坑二）
        self.tgt = self.tmp / "tgt"
        self.tgt.mkdir()
        (self.tgt / "pre-existing").mkdir()          # 预置一条，干跑不得动它
        self._patched = False
        self._patch()

    def _patch(self):
        old_src, old_targets = S.SRC_ROOT, S.TARGETS
        S.SRC_ROOT = self.src
        S.TARGETS = [("dryrun", self.tgt, "测试夹具", False)]
        self.addCleanup(lambda: (setattr(S, "SRC_ROOT", old_src),
                                 setattr(S, "TARGETS", old_targets)))

    def _entries(self):
        return sorted(os.listdir(self.tgt))

    def test_plan_would_actually_have_written_something(self):
        """**前提守卫**：先证明「非干跑确实写得进去」，否则下面两条都是空断言。

        把这条放在最前面，是因为它才是坑二的照妖镜：若 `would_create` 为空或
        写不进去，后面那两条断言无论干跑做什么都会绿。
        """
        p = S.plan(S.TARGETS, S.curated())
        self.assertEqual(len(p["would_create"]), 2,
                         "夹具前提破了：没有待建软链 ⇒ 干跑断言恒真")
        p2 = S.plan(S.TARGETS, S.curated())
        applied = S.apply_plan(p2)
        self.assertEqual(len(applied["created"]), 2, "真写应当成功（前提：目录存在且可写）")
        self.assertEqual(self._entries(), ["demo", "demo2", "pre-existing"])
        # 清干净，别把写入留给后面的用例
        for n in ("demo", "demo2"):
            (self.tgt / n).unlink()

    def test_main_without_apply_writes_nothing(self):
        import io
        import contextlib
        before = self._entries()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            S.main(["--target", "dryrun"])
        self.assertEqual(before, self._entries(),
                         "干跑却改了目标目录（存在 2 条待建软链时仍必须一条不建）")
        self.assertIn("干跑", buf.getvalue())

    def test_dry_run_never_creates_the_target_dir(self):
        """干跑不得**凭空建出**目标目录（不是「目录不存在就算过」）。"""
        import io
        import contextlib
        gone = self.tmp / "gone"
        self.assertFalse(gone.exists())
        with contextlib.redirect_stdout(io.StringIO()):
            S.plan([("dryrun", gone, "夹具", False)], S.curated())   # plan 不建目录
            S.apply_plan(S.plan([("dryrun", gone, "夹具", False)], S.curated()))
        self.assertFalse(gone.exists(), "干跑凭空建出了 %s" % gone)

    def test_unknown_target_exits_nonzero(self):
        self.assertEqual(S.main(["--target", "nosuchroute"]), 2)

    def test_unknown_target_exits_nonzero(self):
        self.assertEqual(S.main(["--target", "nosuchroute"]), 2)
