"""L0 · hub.js 拆分必须**逐字等价**（零行为变更的可证明形式）。
   拆的是源码组织，产物形态不变：模板 /static/hub.js、缓存键、前端探针读的文件都不动
   ⇒ 手机端零风险、不需要重启。任何人只改了 part 或只改了 hub.js，这里立刻红。"""
import hashlib, pathlib, unittest
REPO = pathlib.Path(__file__).resolve().parents[1]
PARTS = sorted((REPO / "static" / "hub").glob("[0-9][0-9]-*.js"))


class T(unittest.TestCase):
    def test_parts_exist(self):
        self.assertGreaterEqual(len(PARTS), 4, "part 少于 4 个说明拆分没落地")

    def test_concat_equals_served_file(self):
        cat = "".join(p.read_text(encoding="utf-8") for p in PARTS)
        served = (REPO / "static" / "hub.js").read_text(encoding="utf-8")
        self.assertEqual(hashlib.md5(cat.encode()).hexdigest(),
                         hashlib.md5(served.encode()).hexdigest(),
                         "拼接 != static/hub.js —— 请跑 scripts/build_hubjs.sh 重新生成")

    def test_no_part_is_empty_or_reordered(self):
        for p in PARTS:
            self.assertTrue(p.read_text(encoding="utf-8").strip(), f"{p.name} 是空的")
            self.assertRegex(p.name, r"^\d\d-[a-z0-9-]+\.js$", f"{p.name} 命名不合规范（前缀序号是顺序的唯一依据）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
