"""L0：`code_stale`/`code_matches_head` 的**内容指纹**语义（PT-20260924-01）。

要治的假红（实测）：旧语义比的是 commit 指针 ⇒「带未提交改动重启 → 验证通过 → 再提交」
这条本机标准工序，每次提交后 boot 指针必然落后一格 ⇒ `code_stale` 长期为 True。
09-23 就因此产生 3 例 verify_prod_smoke 假 FAIL。

fixture 里的 blob sha 是用**真实 `git hash-object`** 在临时仓里对拍后抄下来的
（见 agent-knowledge/40 与提交信息），所以本文件不需要 git 二进制、属真 hermetic。
"""
import hashlib
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import selfattest  # noqa: E402

# ★以下三个 sha 一律由 `printf '<内容>' | git hash-object --stdin` 现取，**不得手填/凭记忆补**
#   （09-24 就干过一次：只实测了前 12 位，后 28 位是我编的，闸门立刻红给自己看）。
#   改动 fixture 时必须重新用该命令取值，并在 diff 里能看到取值方式。
BLOB_A = "b917a726c93f902e43291d9009d6488385133b67"     # "print(1)\n"
BLOB_B = "bafc5d9a1a836cea09dadcec13e66fe498cd997a"     # "x=1\n"
BLOB_A2 = "d0e0fd661801c4c3bce1ec217d272b788e2a4955"    # "print(2)\n" ← 一字之差


class TestGitBlobSha(unittest.TestCase):
    """工作区侧必须与 git 同坐标系，否则两侧指纹永不相等。"""

    def test_matches_real_git_hash_object(self):
        self.assertEqual(selfattest.git_blob_sha(b"print(1)\n"), BLOB_A)
        self.assertEqual(selfattest.git_blob_sha(b"x=1\n"), BLOB_B)

    def test_one_byte_difference_changes_sha(self):
        self.assertNotEqual(selfattest.git_blob_sha(b"print(1)\n"),
                            selfattest.git_blob_sha(b"print(2)\n"))
        self.assertEqual(selfattest.git_blob_sha(b"print(2)\n"), BLOB_A2)


class TestContentFp(unittest.TestCase):
    def test_order_independent(self):
        a = {"src/a.py": BLOB_A, "src/b.py": BLOB_B}
        b = {"src/b.py": BLOB_B, "src/a.py": BLOB_A}
        self.assertEqual(selfattest.content_fp(a), selfattest.content_fp(b),
                         "指纹不得随 dict 迭代顺序变化")

    def test_content_sensitive(self):
        base = {"src/a.py": BLOB_A, "src/b.py": BLOB_B}
        moved = dict(base, **{"src/a.py": BLOB_A2})
        self.assertNotEqual(selfattest.content_fp(base), selfattest.content_fp(moved),
                            "任一文件内容变了，指纹必须变")

    def test_empty_is_empty_string(self):
        self.assertEqual(selfattest.content_fp({}), "")
        self.assertEqual(selfattest.content_fp(None), "")

    def test_path_is_part_of_identity(self):
        """同名同内容换了路径 ⇒ 指纹也该变（防止"文件被移动"被判成没改）。"""
        self.assertNotEqual(selfattest.content_fp({"src/a.py": BLOB_A}),
                            selfattest.content_fp({"src/zz/a.py": BLOB_A}))


class TestRuntimePaths(unittest.TestCase):
    def test_only_src_py_and_no_pycache(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "src" / "sub").mkdir(parents=True)
            (root / "src" / "a.py").write_text("x=1\n", encoding="utf-8")
            (root / "src" / "sub" / "b.py").write_text("y=2\n", encoding="utf-8")
            (root / "src" / "note.txt").write_text("skip", encoding="utf-8")
            pc = root / "src" / "__pycache__"
            pc.mkdir()
            (pc / "a.cpython-311.pyc").write_bytes(b"junk")
            (root / "static").mkdir()
            (root / "static" / "hub.js").write_text("nope", encoding="utf-8")
            got = selfattest.runtime_paths(root)
            self.assertEqual(got, ["src/a.py", "src/sub/b.py"],
                             "只收 src 下 .py：不得含 .txt/__pycache__/static")


class TestHeadParsing(unittest.TestCase):
    """`ls-tree` 解析：必须按**默认输出的真 tab** 切；`--format` 里的 \\t git 不解释。"""

    def test_parses_default_format_and_filters(self):
        fake = ("100644 blob %s\tsrc/a.py\n"
                "100644 blob %s\tsrc/b.py\n"
                "100644 blob deadbeef\tsrc/note.txt\n"
                "100644 blob cafef00d\tsrc/__pycache__/a.pyc\n"
                "040000 tree 12345678\tsrc/sub\n" % (BLOB_A, BLOB_B))
        out = selfattest._parse_ls_tree(fake)
        self.assertEqual(out, {"src/a.py": BLOB_A, "src/b.py": BLOB_B})

    def test_literal_backslash_t_must_not_parse(self):
        """git --format 会输出字面 "\\t"，若哪天有人改回 --format，这里必须抓到它解析为空。"""
        self.assertEqual(selfattest._parse_ls_tree("src/a.py\\t%s" % BLOB_A), {},
                         "字面反斜杠-t 不是分隔符：解析不出东西才是正确行为（静默全空会被下游当不可判定）")


class TestSemanticsSwitch(unittest.TestCase):
    """改语义的核心断言：**commit 指针变了不再等于需要重启**。"""

    def test_pointer_move_alone_does_not_claim_stale(self):
        # 无 boot 指纹（进程启动于本机制之前 / 非 git 环境）⇒ 必须给 None + 原因，
        # 而不是拿 sha 差去冒充"需要重启"的结论。
        self.assertIsNone(selfattest.needs_restart(boot_fp="", wt_fp="abc", head_fp="def"),
                          "没有 boot 指纹时不得声称需要重启")
        self.assertIsNotNone(selfattest.STALE_UNKNOWN_REASON)

    def test_same_content_different_pointer_is_not_stale(self):
        self.assertFalse(selfattest.needs_restart(boot_fp="abc", wt_fp="abc", head_fp="abc"))
        self.assertTrue(selfattest.running_matches_head(boot_fp="abc", head_fp="abc"))

    def test_changed_content_is_stale_even_if_pointer_same(self):
        self.assertTrue(selfattest.needs_restart(boot_fp="abc", wt_fp="xyz", head_fp="abc"),
                        "内容改了没重启 ⇒ 真需要重启（这才是该报 stale 的场合）")
        self.assertFalse(selfattest.running_matches_head(boot_fp="abc", head_fp="def"),
                         "boot 内容与 HEAD 内容不同 ⇒ 不得自称跑的是 HEAD")

    def test_unreadable_file_changes_fingerprint(self):
        """读不到的文件记 "unreadable" ⇒ 宁可指纹变化可见，也不静默当成"没改"。"""
        self.assertNotEqual(selfattest.content_fp({"src/a.py": "unreadable"}),
                            selfattest.content_fp({"src/a.py": BLOB_A}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
