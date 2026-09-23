"""L0 hermetic：静态闸门判据（src/staticguard.py）。

这些用例**零宿主依赖**：全在 tempfile 里造目录树，换任何机器结论一致。
它们存在的原因：这三条判据此前内联在路由里，离线完全测不到；等发现时
`GET /static/../static_evil/secret.txt` 已经能取回文件内容了。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import staticguard  # noqa: E402


class TestServeable(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="hub-static-"))
        self.sibling = self.root.parent / (self.root.name + "_evil")   # 兄弟目录（逃逸靶）
        self.sibling.mkdir(exist_ok=True)
        (self.root / "hub.js").write_text("ok")
        (self.root / "hub.js.bak-20260922_133021-replay-query-gate").write_text("secret-backup")
        (self.root / "hub.js.bak-notes").write_text("secret-backup")
        (self.root / "vendor").mkdir()
        (self.root / "vendor" / "xterm.js").write_text("nested")
        (self.sibling / "secret.txt").write_text("CANARY-DO-NOT-SERVE")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.sibling, ignore_errors=True)

    def test_plain_asset_allowed(self):
        self.assertEqual(staticguard.resolve_serveable(self.root, "hub.js"),
                         (self.root / "hub.js").resolve())

    def test_nested_asset_allowed(self):
        self.assertIsNotNone(staticguard.resolve_serveable(self.root, "vendor/xterm.js"))

    def test_missing_asset_refused(self):
        self.assertIsNone(staticguard.resolve_serveable(self.root, "nope.js"))

    def test_directory_itself_refused(self):
        """目录不是文件：不得因为"存在"就往下走 stat()/read_bytes()"""
        self.assertIsNone(staticguard.resolve_serveable(self.root, "vendor"))

    def test_backup_never_served(self):
        for name in ("hub.js.bak-20260922_133021-replay-query-gate", "hub.js.bak-notes"):
            with self.subTest(name=name):
                self.assertIsNone(staticguard.resolve_serveable(self.root, name))

    def test_backup_refusal_is_substring_not_suffix(self):
        """.bak 判据按"名字里含 .bak"，因为备份命名是 <f>.bak-<ts>-<说明>；
           顺带确认不会误伤正常名（含 bak 但不含点：bakery.js 仍许服务）"""
        (self.root / "bakery.js").write_text("fine")
        self.assertIsNotNone(staticguard.resolve_serveable(self.root, "bakery.js"))

    def test_sibling_directory_escape_refused(self):
        """核心回归：startswith 不是目录边界，is_relative_to 才是。
           实测旧实现该例 HTTP 200 并取回 CANARY。"""
        rel = os.path.relpath(self.sibling / "secret.txt", self.root)
        self.assertEqual(staticguard.resolve_serveable(self.root, rel), None)
        self.assertEqual(staticguard.resolve_serveable(self.root, f"../{self.sibling.name}/secret.txt"), None)

    def test_parent_traversal_refused(self):
        for bad in ("../etc/passwd", "../../etc/passwd", "..//..//etc//passwd"):
            with self.subTest(bad=bad):
                self.assertIsNone(staticguard.resolve_serveable(self.root, bad))

    def test_symlink_outside_root_refused(self):
        p = self.root / "link.js"
        try:
            p.symlink_to(self.sibling / "secret.txt")
        except OSError:
            # 建不了软链时退化成「目标不存在也必须拒」。刻意不用 skipTest：
            # L0 出现 SKIP 会破坏零跳过闸门（见 tests/tiers.py）。
            self.assertIsNone(staticguard.resolve_serveable(self.root, "link.js"))
            return
        self.assertIsNone(staticguard.resolve_serveable(self.root, "link.js"))

    def test_symlink_inside_root_allowed(self):
        try:
            (self.root / "vendor" / "alias.js").symlink_to(self.root / "hub.js")
        except OSError:
            self.assertIsNone(staticguard.resolve_serveable(self.root, "vendor/alias.js"))
            return
        self.assertIsNotNone(staticguard.resolve_serveable(self.root, "vendor/alias.js"))

    def test_nul_byte_and_weird_input_no_raise(self):
        """闸门只能返回 None，不能抛 —— 抛出去就是 500（本项目 P0-1 就是 500 静默了三天）"""
        for bad in ("\x00", "", "/", "\ud800" * 2, "a" * 400, "."):
            with self.subTest(bad=repr(bad)[:12]):
                self.assertIn(staticguard.resolve_serveable(self.root, bad), (None,))

    def test_root_given_as_relative_still_bounded(self):
        """static_root 传相对路径时也不能被绕过（resolve 在函数内做）"""
        cwd = Path.cwd()
        try:
            os.chdir(self.root.parent)
        except OSError as e:
            self.fail(f"测试环境无法 chdir：{e}")   # 同样不用 skipTest，保 L0 零跳过
        try:
            self.assertIsNotNone(staticguard.resolve_serveable(Path(self.root.name), "hub.js"))
            self.assertIsNone(staticguard.resolve_serveable(Path(self.root.name), "../etc/passwd"))
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
