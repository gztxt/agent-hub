#!/usr/bin/env python3
"""L0：`staticguard.cache_policy` 的纯函数判据（不需要服务，也不需要 import src.main）。

为什么单独测：09-23 事故的根治口径是「URL 的 ?v= **等于文件内容哈希**才许 immutable」。
两个方向都必须钉住 ——
  许：提手正确 ⇒ immutable、不发校验器（否则端侧还能靠 304 拿旧体）
  拒：提手写错或为空 ⇒ 退回 revalidate（no-cache + ETag）
第二个方向是**安全阀**：若"带 ?v= 就 immutable"，那么人忘了改提手就会把端侧永久钉在
旧体上，比这次的故障更糟。红基线用"提手故意写错"与"提手为空"两种输入。
"""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import staticguard as g          # noqa: E402


class TestCachePolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.f = Path(self.tmp.name) / "hub.js"
        self.f.write_bytes(b"console.log(1);\n" * 200)
        self.tok = hashlib.md5(self.f.read_bytes(), usedforsecurity=False).hexdigest()[:8]

    def tearDown(self):
        self.tmp.cleanup()

    def test_content_token_matches_build(self):
        """content_token 必须与构建脚本写进模板的算法一致（md5 前 8 位）。"""
        self.assertEqual(g.content_token(self.f), self.tok)
        self.assertEqual(len(self.tok), 8)

    def test_correct_token_gets_immutable(self):
        pol, want = g.cache_policy(self.tok, self.f)
        self.assertEqual(pol, "immutable")
        self.assertEqual(want, self.tok)

    def test_wrong_token_falls_back_to_revalidate(self):
        """★安全阀：提手写错绝不许 immutable（红基线①）。"""
        pol, want = g.cache_policy("20260923a", self.f)
        self.assertEqual(pol, "revalidate")
        self.assertEqual(want, self.tok)          # 仍回真值，供 X-Asset-Token 暴露漂移

    def test_empty_token_falls_back(self):
        """红基线②：老端侧/无提手请求必须走可复验路径。"""
        self.assertEqual(g.cache_policy("", self.f)[0], "revalidate")

    def test_token_follows_content_change(self):
        """内容一改提手必改 ⇒ immutable 下不存在"同 URL 换体"。"""
        before = g.content_token(self.f)
        self.f.write_bytes(b"console.log(2);\n" * 200)
        self.assertNotEqual(g.content_token(self.f), before)
        self.assertEqual(g.cache_policy(before, self.f)[0], "revalidate")

    def test_hubjs_template_token_is_current(self):
        """产物与模板不许漂（这条把 L0 与真实文件绑住，防"脚本改了但没重跑构建"）。"""
        root = Path(__file__).resolve().parents[1]
        hub = root / "static" / "hub.js"
        s = (root / "templates" / "index.html").read_text(encoding="utf-8")
        want = g.content_token(hub)
        self.assertIn("/static/hub.js?v=" + want, s,
                      "模板里的 hub.js 提手不是当前内容哈希 ⇒ 请跑 scripts/build_hubjs.sh")


if __name__ == "__main__":
    unittest.main(verbosity=2)
