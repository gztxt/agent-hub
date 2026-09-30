"""L0 hermetic：资源页 CSS 变量/类名不得再漂移（PT-20260930-01 P1-14/15/16）。

起因（2026-09-30 实测）：11-resources.js 引用了三个**在 :root 里定义数为 0** 的变量
（--primary / --card-bg / --panel-bg），外加一个**不存在的类** .btn.xs（只有 .sm）。
后果不是"样式不统一"这么轻：var() 解析失败回退到初始值 ⇒ 徽章底色透明、
卡片底色透明、按钮没有任何样式 —— 资源页看起来像没做完的半成品。

这类漂移的特点是**静默且不可见**：语法合法、页能开、控制台零报错，
只有真渲染截图或真用鼠标点才发现。所以判据必须落在测试层，不能靠人眼。
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RES_JS = REPO / "static" / "hub" / "11-resources.js"
TPL = REPO / "templates" / "index.html"


def _defined_tokens() -> set:
    tpl = TPL.read_text(encoding="utf-8")
    m = re.search(r":root\s*\{(.*?)\n\s*\}", tpl, re.S)
    body = m.group(1) if m else tpl
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", body))


def _strip_comments_and_strings(src: str) -> str:
    """去掉 /* */ 与 // 注释和字符串字面量：注释里为解释成因引用的旧写法不是活代码。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    src = re.sub(r"'[^'\n]*'", "''", src)
    src = re.sub(r'"[^"\n]*"', '""', src)
    return src


class TestResourcePageTokensResolve(unittest.TestCase):
    def test_every_var_used_by_resources_page_is_defined(self):
        src = _strip_comments_and_strings(RES_JS.read_text(encoding="utf-8"))
        used = set(re.findall(r"var\((--[a-z0-9-]+)\)", src))
        defined = _defined_tokens()
        missing = sorted(used - defined)
        self.assertEqual(missing, [],
                         f"资源页引用了 :root 里不存在的变量 {missing} ⇒ var() 解析失败，"
                         f"底色回退透明。已定义的有：{sorted(defined)[:8]}…")

    def test_no_literal_colors_in_inline_style(self):
        """DESIGN.md 硬约束 1：禁止字面色。资源页曾有 #6b7280 / #8b5cf6 / #fff。"""
        src = _strip_comments_and_strings(RES_JS.read_text(encoding="utf-8"))
        lits = re.findall(r"#[0-9a-fA-F]{3,8}\b", src)
        self.assertEqual(lits, [], f"资源页仍有字面色 {lits}（须收敛到 :root 语义 token）")


class TestResourcePageClassesExist(unittest.TestCase):
    def test_btn_variant_exists(self):
        """.btn.xs 从未存在（本仓只有 .sm/.danger/.ghost/.on）⇒ 按钮无任何样式。"""
        alljs = "\n".join(p.read_text(encoding="utf-8") for p in (REPO / "static" / "hub").glob("*.js"))
        alljs += TPL.read_text(encoding="utf-8")
        for cls in set(re.findall(r'class="btn ([a-z]+)', RES_JS.read_text(encoding="utf-8"))):
            if cls in ("xs",):
                self.fail(f".btn.{cls} 不存在，全仓只有 .btn.sm/.danger/.ghost/.on")

    def test_res_cmd_class_has_style_rule(self):
        src = RES_JS.read_text(encoding="utf-8")
        if "res-cmd" in src:
            self.assertIn(".res-cmd {", src,
                          "res-cmd 类被用到却没有对应样式规则（宽度分档失效）")


class TestResourcePageBreakpointSingleSource(unittest.TestCase):
    """分档偏好不变量第 3 条：断点只允许一处定义，JS 与 CSS 同值。"""

    def test_media_query_matches_hub_narrow_mq(self):
        core = (REPO / "static" / "hub" / "01-core-boot.js").read_text(encoding="utf-8")
        m = re.search(r"matchMedia\('\(max-width:\s*(\d+)px\)'\)", core)
        self.assertIsNotNone(m, "01-core-boot.js 的 HUB_NARROW_MQ 找不到了")
        truth = m.group(1)
        res = RES_JS.read_text(encoding="utf-8")
        for q in set(re.findall(r"@media \(max-width:\s*(\d+)px\)", res)):
            self.assertEqual(q, truth,
                             f"资源页断点 {q}px 与 HUB_NARROW_MQ {truth}px 不同源 ⇒ 中间宽度两套真相打架")

    def test_no_js_side_innerwidth_breakpoint(self):
        """禁止 JS 另写一份 innerWidth < N：两套真相在中间宽度必然打架。"""
        src = _strip_comments_and_strings(RES_JS.read_text(encoding="utf-8"))
        self.assertNotRegex(src, r"innerWidth\s*<\s*\d+",
                            "JS 侧出现 innerWidth 断点，必须走 HUB_NARROW_MQ")


class TestResourcePageNarrowNoHardWidth(unittest.TestCase):
    def test_cmdline_cell_not_hardcoded_400px(self):
        """写死 max-width:400px 在 390px 视口必横向溢出 ⇒ 手机上按钮点不到。"""
        src = RES_JS.read_text(encoding="utf-8")
        for m in re.finditer(r"<td[^>]*style=\"([^\"]*)\"", src):
            if "max-width:400px" in m.group(1).replace(" ", ""):
                self.fail("命令行 <td> 仍写死 max-width:400px（窄屏溢出根因）")


if __name__ == "__main__":
    unittest.main()
