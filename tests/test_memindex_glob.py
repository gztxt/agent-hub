#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""glob 与 realpath 语义闸门（G18=L0 hermetic，G19=L1 host）。

**为什么这两条必须独立成文件**：它们在 2026-10-02 真的咬人了。
`dry_run` 第一次跑出来 `grok_memory=0 / hermes_memory=0 / workbuddy_memory=0`，
而同一时刻用真 `rg` 实测这三路各有 33/164/13 个文件。根因是

    _glob_match: any(_fmatch(head + a + tail, rel) for a in alts)
                        ^^^^^^^^^^^^^^  ^^^^  参数顺序反了

`_fmatch(name, pattern)` 被喂成了 `_fmatch(pattern, name)`。**不报错、不抛异常、
返回 False** ⇒ 三路源静默投影成空。而 `pi_sessions`(`*.jsonl`)、`claude_projects`
(`**/memory/*.md`) 不走花括号分支所以照常生效 ⇒ **只打三分之一的源**。

这正是本仓反复写进设计书的那句：「全指标绿而功能层已死」。
它同时暴露了一个更难受的事实：**G13（内容等价）也没抓到它**——
因为当时的夹具只用 `*.md`，花括号分支压根没被执行到。
⇒ 闸门有盲区就得补盲区，不能事后说一句「已知悉」。

所以这里不只钉住 bug，还钉住**为什么当初没抓到**：
G18 保证花括号语义正确，G19 保证**生产 specs 里的每个 glob 都必须真的命中东西**
（空命中即报红——既防参数传反，也防某一路目录被重命名/清空后悄悄变成空源，
就像 workbuddy_memory 那 76KB 的既有案例）。
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from memindex import Spec, _glob_match, enumerate_corpus, proj_specs  # noqa: E402

import tiers  # noqa: E402

RG_BIN = shutil.which("rg")


class TestG18BraceGlob(unittest.TestCase):
    """G18：rg 风格 glob 的展开与分隔符语义。"""

    CASES = [
        # (rel, pattern, expected)
        ("MEMORY.md", "{*.md,prompt_history.jsonl}", True),
        ("prompt_history.jsonl", "{*.md,prompt_history.jsonl}", True),
        ("prompt_history.txt", "{*.md,prompt_history.jsonl}", False),
        # （期望值以 2026-10-02 真 rg `-g` 对拍为准，不以脑补为准：
        #   `{*.md,session_*.json}` 不吃裸 .json；`*.md`（pattern 内无 `/`）跨 `/` 匹配）
        ("note.json", "{*.md,session_*.json}", False),
        ("session_1.json", "{*.md,session_*.json}", True),
        ("note.md", "{*.md,session_*.json}", True),
        ("note.txt", "{*.md,session_*.json}", False),
        ("a.json", "{*.md,*.json}", True),
        ("a.md", "{*.md,*.json}", True),
        ("a.log", "{*.md,*.json}", False),
        ("USER.md", "{*.md,*.json}", True),
        # 非花括号
        ("s.jsonl", "*.jsonl", True),
        ("s.json", "*.jsonl", False),
        ("proj/memory/note.md", "**/memory/*.md", True),
        ("memory/note.md", "**/memory/*.md", True),
        ("proj/note.md", "**/memory/*.md", False),
        # `*` 在 pattern 内无 `/` 时跨 `/`（与 rg 一致，已实测）
        ("deep/nested/x.md", "*.md", True),
    ]

    def test_g18_逐条(self):
        for rel, pat, want in self.CASES:
            with self.subTest(rel=rel, pat=pat):
                self.assertEqual(_glob_match(rel, pat), want)

    def test_g18_花括号必须真的展开而不是当字面量(self):
        """红向：把 `{*.md,*.json}` 当普通字符 ⇒ 无一命中 → 静默空源。"""
        self.assertTrue(_glob_match("a.md", "{*.md,*.json}"))
        self.assertTrue(_glob_match("a.json", "{*.md,*.json}"))

    def test_g18_与真rg逐条对齐(self):
        """与真 rg 的 `-g` 对拍。这是 G13 的同源纪律：不用 fake 当替身。"""
        if not RG_BIN:
            self.skipTest("无 rg")
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="glob-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        names = ["a.md", "a.json", "a.txt", "prompt_history.jsonl",
                 "session_1.json", "s.jsonl", "deep/nested/x.md"]
        for n in names:
            p = tmp / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("needle_token_here\n", encoding="utf-8")
        pats = ["{*.md,prompt_history.jsonl}", "{*.md,session_*.json}",
                "{*.md,*.json}", "*.jsonl"]
        for pat in pats:
            rg_hits = {l for l in subprocess.run(
                [RG_BIN, "--files", "--no-ignore", "--hidden", "-g", pat, str(tmp)],
                capture_output=True, text=True).stdout.splitlines() if l.strip()}
            rg_rel = {str(pathlib.Path(l).relative_to(tmp)) for l in rg_hits}
            for n in names:
                with self.subTest(pat=pat, name=n):
                    self.assertEqual(
                        _glob_match(n, pat), n in rg_rel,
                        f"{n!r} vs {pat!r}：与 rg 不一致（rg={sorted(rg_rel)}）")


@tiers.host_only
class TestG19EveryProductionSpecMatches(unittest.TestCase):
    """G19：生产 specs 的每一路 glob 都必须真的命中东西，空命中即报红。

    这不是「今天恰好不为空」的快照断言——一旦某路 glob 写错、参数传反、
    或上游把目录改了名，这一条会在**建库前**就报红，而不是等到
    `per_source={"grok_memory": 0}` 被人从 JSON 里划过去。

    ⚠️ **本类是 L1（host）不是 L0**：它 `enumerate_corpus(proj_specs())` 读的是本机真目录
    （`~/.pi/agent/sessions`、`~/.grok/memory`…）。初版把它放在 L0 且未打 host 标，
    `hermetic-clean`（HOME 换成空目录）下 10 个用例集体报红——而失败信息恰好是
    「投影为 0 个文件」，与本闸门要抓的那个 bug **字面同形**：
    真遇到投影空源时，人会把这条红当成「目录没了」而忽略。
    这就是分层放错的真正代价：**不是报红，是报出一个与真故障无法区分的红**。
    规矩见 `tests/README.md`「新测试该放哪一层」第 2 条。
    """

    @classmethod
    def setUpClass(cls):
        cls.rep = enumerate_corpus(proj_specs())

    def test_g19_所有rs口径源都非空(self):
        """rs 口径那八路。workbuddy_memory 允许极小但不允许为 0。"""
        for sid in ("pi_sessions", "codex_sessions", "claude_projects", "grok_memory",
                    "hermes_memory", "workbuddy_memory", "workspace_files",
                    "archived_sessions"):
            with self.subTest(sid=sid):
                self.assertGreater(
                    self.rep.per_source.get(sid, 0), 0,
                    f"{sid} 投影为 0 个文件：要么 glob 写错（花括号/参数传反），"
                    f"要么目录没了。per_source={self.rep.per_source}")

    def test_g19_花括号glob的三路必须命中(self):
        """就是 2026-10-02 咬人的那三路。单独拎出来钉，失败信息直接指到病灶。"""
        for sid in ("grok_memory", "hermes_memory", "workbuddy_memory"):
            with self.subTest(sid=sid):
                self.assertGreater(
                    self.rep.per_source.get(sid, 0), 0,
                    f"{sid} 的 glob 是花括号形式，这是历史上参数传反的重灾区")

    def test_g19_与rg对拍确认每路都真的可达(self):
        """不信任自己的枚举器：拿真 rg 去问「这一路到底有多少文件」。"""
        if not RG_BIN:
            self.skipTest("无 rg")
        checked = 0
        for spec in proj_specs():
            if spec.home_mode or spec.glob is None or "{" not in spec.glob:
                continue
            for root in spec.roots:
                rootp = pathlib.Path(root)
                if not rootp.is_dir():
                    continue
                n_rg = len([l for l in subprocess.run(
                    [RG_BIN, "--files", "--no-ignore", "--hidden",
                     "--max-filesize", "20M", "-g", spec.glob, str(rootp)],
                    capture_output=True, text=True).stdout.splitlines() if l.strip()])
                with self.subTest(root=root, glob=spec.glob):
                    self.assertGreater(n_rg, 0, f"rg 在 {root} 上也没命中 {spec.glob}")
                checked += 1
        self.assertGreater(checked, 0, "一条都没对上，对拍无意义")


if __name__ == "__main__":
    unittest.main(verbosity=2)