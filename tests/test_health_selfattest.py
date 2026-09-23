"""回归测（P0-2）：/health 必须能自证「跑的是哪份代码」。

缺陷出处（实测，不是推断）：生产 /health 只回 {status,service,version,port}，且
  · 进程 09-23 06:50:02 启动 → version 恒为 0.13.2，而 HEAD 已是 v0.13.3（1bd58e7）
  · 没有任何字段能区分「代码已改但服务没重启」与「服务在跑最新代码」
⇒ 版本漂移不可见。本测把 sha 解析与 stale 判定做成纯函数并钉死。

只用手工拼装的 .git 文件：不跑 git 命令、不碰真仓库、不起服务、不 import src.main。
跑法：cd ~/agent-hub && venv/bin/python -m unittest tests.test_health_selfattest -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import selfattest  # noqa: E402

FAKE = "a" * 39 + "1"
OTHER = "b" * 39 + "2"


def _mk_repo(root: Path, head: str, loose: dict | None = None, packed: str | None = None):
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".git" / "HEAD").write_text(head, encoding="utf-8")
    for ref, sha in (loose or {}).items():
        p = root / ".git" / ref
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(sha + "\n", encoding="utf-8")
    if packed:
        (root / ".git" / "packed-refs").write_text(packed, encoding="utf-8")


class TestHeadSha(unittest.TestCase):
    def test_branch_ref_loose(self):
        """最普通的情形：HEAD 是 `ref: refs/heads/master`，走松散引用。"""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _mk_repo(root, "ref: refs/heads/master\n", loose={"refs/heads/master": FAKE})
            self.assertEqual(FAKE, selfattest.head_sha(root))

    def test_branch_ref_via_packed_refs(self):
        """仓库被 gc 过：没有松散引用，只有 packed-refs。"""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _mk_repo(root, "ref: refs/heads/master\n", packed=f"{FAKE} refs/heads/master\n")
            self.assertEqual(FAKE, selfattest.head_sha(root), "packed-refs 回落失败")

    def test_detached_head(self):
        """detached：HEAD 直接就是 sha。"""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _mk_repo(root, FAKE + "\n")
            self.assertEqual(FAKE, selfattest.head_sha(root))

    def test_not_a_repo_returns_empty(self):
        """非 git 环境（拷贝出来的树）必须安静返回空，绝不让 /health 500。"""
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual("", selfattest.head_sha(Path(t)))

    def test_broken_head_returns_empty(self):
        """HEAD 指向不存在的引用 → 空串，不抛。"""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _mk_repo(root, "ref: refs/heads/nope\n")
            self.assertEqual("", selfattest.head_sha(root))

    def test_short_is_8_chars(self):
        self.assertEqual(FAKE[:8], selfattest.short(FAKE))
        self.assertEqual("", selfattest.short(""))


class TestStaleFlag(unittest.TestCase):
    def test_same_sha_not_stale(self):
        self.assertFalse(selfattest.is_stale(FAKE, FAKE))

    def test_diff_sha_is_stale(self):
        self.assertTrue(selfattest.is_stale(FAKE, OTHER), "代码变了却没重启必须报 stale")

    def test_unknown_sha_is_never_stale(self):
        """取不到 sha（非 git 环境）不得虚报 stale。"""
        self.assertFalse(selfattest.is_stale("", OTHER))
        self.assertFalse(selfattest.is_stale(FAKE, ""))
        self.assertFalse(selfattest.is_stale("", ""))


class TestSnapshot(unittest.TestCase):
    def test_drift_becomes_visible(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _mk_repo(root, "ref: refs/heads/master\n", loose={"refs/heads/master": FAKE})
            selfattest.set_repo(root)
            selfattest.boot()
            snap = selfattest.snapshot()
            self.assertEqual(selfattest.short(FAKE), snap["git_sha_boot"])
            self.assertFalse(snap["code_stale"])
            # 模拟「boot 之后又提交了代码，但服务没重启」
            (root / ".git" / "refs" / "heads" / "master").write_text(OTHER + "\n", encoding="utf-8")
            snap2 = selfattest.snapshot()
            # ★09-24 改语义：commit 指针变了**不再**等于"需要重启"。漂移仍然必须可见，
            #   但由溯源字段负责；`code_stale` 现在只在有 boot 内容指纹时才给结论，
            #   这个合成仓里 src/ 是空的 ⇒ 必须 None→False + 写明原因，不许拿 sha 差冒充结论。
            self.assertNotEqual(snap2["git_sha_boot"], snap2["git_sha_now"],
                                "指针漂移必须在字段里看得见（这是本例原本钉的东西）")
            self.assertEqual(selfattest.short(OTHER), snap2["git_sha_now"])
            self.assertFalse(snap2["code_stale"],
                             "无 boot 内容指纹时不得声称需要重启（宁可不可判定）")
            self.assertIn("code_stale_reason", snap2)
            self.assertIsInstance(snap2["uptime_s"], int)

    def test_survives_no_repo(self):
        with tempfile.TemporaryDirectory() as t:
            selfattest.set_repo(Path(t))
            selfattest.boot()
            snap = selfattest.snapshot()
            self.assertEqual("", snap["git_sha_boot"])
            self.assertFalse(snap["code_stale"])
            self.assertIn("boot_at", snap)

    def tearDown(self):
        # 别把临时仓的状态留给后面的用例
        selfattest.set_repo(None)


class TestWorktreeProvenance(unittest.TestCase):
    """补 code_stale 的半个盲区：工作区未提交改动在生产跑时不得自称「跑的是 HEAD」。
       全部是纯函数表驱动用例，不碰盘、不起 git（L0 hermetic）。"""

    def setUp(self):
        selfattest._dirty_cache.update({"at": 0.0, "counts": None})
        selfattest.set_repo(None)

    def test_interpret_classifies_by_runtime_impact(self):
        cases = [
            ([], (0, 0, 0), "干净树"),
            ([" M src/main.py"], (1, 0, 0), "已跟踪的 src"),
            (["M  VERSION"], (1, 0, 0), "VERSION 算运行时"),
            ([" M static/hub.js"], (1, 0, 0), "static 会被服务"),
            ([" D templates/index.html"], (1, 0, 0), "删除模板也是改"),
            (["AM src/deep/nested/x.py"], (1, 0, 0), "暂存+再改"),
            ([" M README.md"], (0, 1, 0), "文档不进运行时"),
            ([" M src/main.py", "?? data/hub.db"], (1, 0, 0), "未跟踪 db 不算"),
            (["?? src/newmod.py"], (0, 0, 1), "新增未跟踪模块可能被 import"),
            (["?? static/evil.js"], (0, 0, 1), "未跟踪 static 会被直接服务"),
            (["?? templates/p.html"], (0, 0, 1), "未跟踪模板会被渲染"),
            (["?? src/main.py.bak-20260923_x"], (0, 0, 0), "src 下的备份件不算"),
            (["?? static/hub.js.bak-20260923_x"], (0, 0, 0), "static 备份件已被 P1-8 闸门 404"),
            (["?? src/notes.txt"], (0, 0, 0), "未跟踪非 py 不進模块"),
            (["R  src/a.py -> src/b.py"], (1, 0, 0), "重命名取新路径"),
            (["R  docs/a.md -> README.md"], (0, 1, 0), "文档重命名归 other"),
            (["!! src/gitignored.py"], (1, 0, 0), "xy 未识别时保守归入运行时"),
            (["?? docs/x.py"], (0, 0, 0), "docs 下的 py 不会被 import"),
        ]
        for lines, want, why in cases:
            with self.subTest(why=why, lines=lines):
                c = selfattest._interpret_status(lines)
                self.assertEqual(
                    (c["runtime_dirty_files"], c["other_dirty_files"], c["untracked_code_files"]),
                    want)

    def test_matches_head_only_when_provable(self):
        S = "a" * 40
        clean = {"runtime_dirty_files": 0, "other_dirty_files": 3, "untracked_code_files": 0}
        dirty = {"runtime_dirty_files": 1, "other_dirty_files": 0, "untracked_code_files": 0}
        newmod = {"runtime_dirty_files": 0, "other_dirty_files": 0, "untracked_code_files": 1}
        cases = [
            ((S, S, clean), True, "同 sha + 运行时干净（文档脏不影响声称）"),
            ((S, S, dirty), False, "有未提交运行时改动 ⇒ 不得自称是 HEAD"),
            ((S, S, newmod), False, "src 下有未跟踪新模块 ⇒ 不得自称"),
            ((S, S, None), False, "git 不可用时 fail-closed"),
            ((S, "b" * 40, clean), False, "sha 不同"),
            (("", "", clean), False, "取不到 sha 就不肯定声称"),
        ]
        for args, want, why in cases:
            with self.subTest(why=why):
                self.assertEqual(selfattest.matches_head(*args), want)

    def test_snapshot_without_probe_never_shells_out(self):
        """单测/低开销路径：probe_dirty=False 必须一次 git 都不起。"""
        orig = selfattest._run_status
        selfattest._run_status = lambda base: self.fail("probe_dirty=False 不应该起 git 子进程")
        try:
            with tempfile.TemporaryDirectory() as td:
                selfattest.set_repo(Path(td))
                snap = selfattest.snapshot(probe_dirty=False)
                self.assertIsNone(snap["code_matches_head"])   # 没探就不声称
                self.assertNotIn("runtime_dirty_files", snap)
        finally:
            selfattest._run_status = orig
            selfattest.set_repo(None)

    def test_git_failure_is_fail_closed(self):
        """git 挂了/超时 ⇒ 计数 None，且绝不能 True（不可虚报）。"""
        orig = selfattest._run_status
        selfattest._run_status = lambda base: None
        try:
            with tempfile.TemporaryDirectory() as td:
                repo = Path(td)
                (repo / ".git").mkdir()
                (repo / ".git" / "HEAD").write_text("b" * 40 + "\n", encoding="utf-8")
                selfattest.set_repo(repo)
                selfattest.boot()
                snap = selfattest.snapshot()
                self.assertIsNone(snap["runtime_dirty_files"])
                self.assertFalse(snap["code_matches_head"])
        finally:
            selfattest._run_status = orig
            selfattest.set_repo(None)

    def test_snapshot_never_shells_out_even_with_probe(self):
        """/health 是生命线端点：即使 probe_dirty=True 也只能读缓存。
           一旦把 git 子进程放进请求路径，一次挂住就能把健康检查拖到超时。"""
        orig = selfattest._run_status
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".git").mkdir()
            (repo / ".git" / "HEAD").write_text("d" * 40 + "\n", encoding="utf-8")
            selfattest.set_repo(repo)
            selfattest._run_status = lambda base: [" M src/main.py"]
            try:
                selfattest.boot()                      # 启动路径可以起 git
                selfattest._run_status = lambda base: self.fail(
                    "snapshot() 不得起子进程，只能读 refresh() 刷过的缓存")
                snap = selfattest.snapshot()
                self.assertEqual(snap["runtime_dirty_files"], 1, "读的是缓存里的旧值")
                self.assertIn("dirty_age_s", snap, "陈旧程度必须可见，不能伪装成现测")
                self.assertFalse(snap["code_matches_head"])
            finally:
                selfattest._run_status = orig
                selfattest.set_repo(None)

    def test_dirty_tree_beats_stale_flag(self):
        """本例是这次修正的全部理由：sha 两边一视（code_stale=False）
           但工作区有未提交代码时，新字段必须报 False。"""
        orig = selfattest._run_status
        selfattest._run_status = lambda base: [" M src/term.py"]
        try:
            with tempfile.TemporaryDirectory() as td:
                repo = Path(td)
                (repo / ".git").mkdir()
                sha = "c" * 40
                (repo / ".git" / "HEAD").write_text(sha + "\n", encoding="utf-8")
                selfattest.set_repo(repo)
                selfattest.boot()
                snap = selfattest.snapshot()
                self.assertFalse(snap["code_stale"], "旧字段确实看不出问题")
                self.assertEqual(snap["runtime_dirty_files"], 1)
                self.assertFalse(snap["code_matches_head"], "新字段必须拒称「跑的是 HEAD」")
        finally:
            selfattest._run_status = orig
            selfattest.set_repo(None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
