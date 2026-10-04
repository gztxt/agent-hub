"""窄屏**首帧**几何闸门（v0.13.74）。

【为什么要有这个文件】2026-10-04 真渲染实测发现生产 v0.13.73 有这样一条：
用户在窄屏看到的第一眼，是一块 **236px 白板盖住 60.5%(390px)~73.8%(320px)** 的视口，
正文被从中间切断 —— 形态与 09-23「整页被白板糊住」完全一致。
根因不是某条规则写错，而是**判据只覆盖了「稳定后」**：
`tests/verify_narrow_default_iconbar.py` 断言的是 initSidebar 跑完之后的 collapsed=true，
而**从导航到 initSidebar 之间那段（实测 ~1.2s）没人管**。

【本闸门的口径】不跑浏览器（那会变成 L1），只断言**首帧机制本身**成立：

1. 打标记的脚本在 `<head>` 里，且**在 `<aside>` 之前**；
2. `<head>` 脚本只读视口、只挂 `<html>` 的类，不碰 `#sidebar`、不碰 localStorage；
3. CSS 里存在 `html.narrow-rail .sidebar:not(.collapsed)` 且它在 `max-width:767px` 块内；
4. 窄屏抽屉规则 `:not(.collapsed)` **仍在**（真展开那一格没被改）；
5. 断点值 `767px` 在模板窄屏块里只出现一处（分档偏好不变量③）。

【为什么第 1 条要卡「在 <head>」】第一版把脚本放在 `<aside>` 的首个子节点，
320px 生效但 **390px 仍闪** —— 浏览器可以在解析到 `<aside>` 开标签后、
跑完该脚本之前完成首次绘制（实测两档结果不一致 = 竞态）。
移进 `<head>` 后 body 还不存在，没有任何东西可绘制 ⇒ 竞态从根上不存在。
这条断言就是防止有人「优化」回 body 里。

真渲染判据（首帧 ≤60px、位置非 fixed、视口中心归内容）由
`tests/probe_narrow_first_paint.py` 跑，见其 docstring。
"""
import re
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
HTML = (_REPO / "templates" / "index.html").read_text(encoding="utf-8")

#: 断点唯一取值（分档偏好不变量③：与 static/hub/01-core-boot.js 的 matchMedia 同值）
BREAKPOINT = 767          # int，别带单位：带 px 会在 int() 上炸（本文件第一版就是这么炸的）

#: 注释会被正则扫到而**不会被执行**——第一版闸门就是因此假红的两次：
#: 「不碰 localStorage」这句话本身写在 <head> 的注释里，于是 head 里有 localStorage；
#: 「不得另写 innerWidth<768」这句话本身也含 innerWidth。凡是**扫源码文本**的断言，
#: 都必须先剥注释，否则它在给自己抓自己。
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _code() -> str:
    """剥掉 HTML 注释后的模板正文（仅本闸门用于扫源码文本）。"""
    return _HTML_COMMENT.sub("", HTML)


class NarrowFirstPaintTest(unittest.TestCase):
    def test_marker_script_is_in_head_before_the_sidebar(self):
        """打标记脚本必须在 `<head>` 内，且出现在 `<aside id="sidebar">` 之前。"""
        head_end = HTML.index("</head>")
        aside_at = HTML.index('<aside class="sidebar" id="sidebar"')
        marker = "narrow-rail"
        first_mark = HTML.index(marker)
        self.assertLess(first_mark, head_end,
                        "narrow-rail 标记不在 <head> 内 ⇒ 竞态回来了（浏览器可在脚本前完成首绘）")
        self.assertLess(first_mark, aside_at,
                        "标记出现在 <aside> 之后 ⇒ 首帧时侧栏几何已按抽屉算过了")
        # 且必须是脚本里的字符串，不是 CSS 选择器
        self.assertIn("document.documentElement.classList.add('narrow-rail')", HTML[:head_end])

    def test_marker_script_does_not_touch_storage_or_sidebar(self):
        """只挂 `<html>` 的类：不碰 `#sidebar`（此时还不存在）、不碰 localStorage。

        · 碰 `#sidebar` 会在 `<head>` 阶段拿到 null（元素还没建）；
        · 碰 localStorage 会违反分档偏好不变量②「加载不写盘」。
        """
        head = _code()[:_code().index("</head>")]
        self.assertNotIn("localStorage", head, "<head> 可执行代码里出现了 localStorage ⇒ 首屏写盘")
        self.assertNotIn("getElementById('sidebar')", head,
                         "<head> 阶段 #sidebar 还不存在，这里取到的是 null")

    def test_css_rule_exists_inside_the_narrow_media_block(self):
        """`html.narrow-rail .sidebar:not(.collapsed)` 必须落在窄屏块内。"""
        self.assertIn("html.narrow-rail .sidebar:not(.collapsed)", HTML,
                      "缺首帧图标条规则 ⇒ <head> 打的标记没人认")
        m = re.search(r"@media \(max-width: 767px\) \{(.*?)\n        \}", HTML, re.S)
        self.assertIsNotNone(m, "找不到窄屏 @media 块")
        self.assertIn("html.narrow-rail .sidebar:not(.collapsed)", m.group(1),
                      "narrow-rail 规则不在窄屏 @media 块内 ⇒ 宽屏也会被改成图标条")

    def test_real_drawer_rule_survives(self):
        """真正的「展开成抽屉」规则**必须还在** —— 首帧修复不得顺手改掉展开态。"""
        m = re.search(r"@media \(max-width: 767px\) \{(.*?)\n        \}", HTML, re.S)
        body = m.group(1)
        self.assertIn(".sidebar:not(.collapsed) { position: fixed;", body,
                      "窄屏抽屉规则被删了 ⇒ 用户点开侧栏将不再是覆盖式抽屉")
        self.assertIn("width: 236px", body, "抽屉宽度 236px 没了")

    def test_breakpoint_value_is_single_sourced(self):
        """**管辖侧栏的 @media 块有且只有一个，且它的断点是 767px**（分档偏好不变量③）。

        【为什么不扫「模板里只有一个 max-width 值」】那会假红：模板里另有
        `max-width:1000px` / `1100px` 两个与分档**无关**的布局断点，它们是合法的。
        真正要卡的第二真相源是「侧栏/窄屏档在两个不同阈值上各写一套」，
        所以只数**管辖侧栏的块**。
        """
        # 连头带体一起抓，避免在 body 里回头找头部（第一版那么干，捕获组吃到了 '767px'）。
        pairs = re.findall(r"@media \(max-width[ :]+(\d+)px\) \{(.*?)\n        \}", HTML, re.S)
        sidebar_blocks = [int(n) for n, b in pairs if ".sidebar" in b]
        self.assertTrue(sidebar_blocks,
                        "找不到管辖侧栏的 @media 块（media 头 + 含 .sidebar 的体）")
        widths = sorted(set(sidebar_blocks))
        self.assertEqual(widths, [BREAKPOINT],
                         "管辖侧栏的 @media 断点有多个取值：%s（应只有 %s）" % (widths, BREAKPOINT))

    def test_sidebar_toggle_button_still_present(self):
        """首帧默认收起之后，用户必须**有办法展开** —— 收起按钮不能被顺手藏掉。"""
        self.assertIn('id="btnSideToggle"', HTML,
                      "找不到侧栏展开按钮 ⇒ 窄屏首帧收起后就再也打不开了")

    # ── 类名存在性（v0.13.74 补）───────────────────────────────────────────
    def test_narrow_typography_selectors_exist_in_dom(self):
        """本批在窄屏块里写下的**每一个类选择器**都必须真实存在于模板 DOM 里。

        【为什么要这条】v0.13.74 第一版把窄屏字号规则写成 `.home-title` / `.home-lead` /
        `.home-stats` —— 这三个类名在模板里**根本不存在**。后果：CSS 静默不生效，
        真渲染截图里字号与密度**原封未动**，而当时**全部测试全绿**，
        因为没有任何一条断言去看它。**CSS 写错类名不报错**，只会安静地什么都不做 ——
        与本仓「文字存在 ≠ 已生效」（TDZ / vitals_loop / 跨档镜像）同一个家族，
        但这条更隐蔽：它连报错的机会都不给。

        口径：只校验**本批新写的那几条**，不做全量 CSS-DOM 交叉校验
        （那会把「有意不匹配的类名」也一并炸出来，噪声大于收益）。
        以后往窄屏块里加选择器，就往这个列表里加一条。
        """
        narrow_block = re.search(r"@media \(max-width: 767px\) \{(.*?)\n        \}", HTML, re.S).group(1)
        # 本批在窄屏块里新写的类选择器（不含 .sidebar / .header 等既有结构）
        NEW_SELECTORS = (".home-brand-txt", ".home-desc", ".home-kicker",
                         ".home-stat", ".home-wrap", ".only-wide", ".only-narrow")
        for sel in NEW_SELECTORS:
            with self.subTest(sel=sel):
                self.assertIn('class="', HTML, "")
                # 类名要在 DOM 里出现过：找 `class="..."` 里含该词的写法
                found = re.search(r'class="[^"]*\b%s\b[^"]*"' % re.escape(sel[1:]), HTML)
                self.assertIsNotNone(found,
                                     "窄屏块里写了 %s，但模板 DOM 里没有这个类 ⇒ CSS 静默不生效"
                                     % sel)
                self.assertIn(sel, narrow_block,
                              "%s 应在窄屏块内生效" % sel)


if __name__ == "__main__":
    unittest.main()