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
            self.assertTrue(snap2["code_stale"], "sha 变了必须 code_stale=true")
            self.assertEqual(selfattest.short(OTHER), snap2["git_sha_now"])
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
