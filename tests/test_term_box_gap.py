"""终端框四周留白收窄 60%（v0.13.97，用户 2026-10-09「右边终端框上下左右边距缩小 60%」）。

【为什么要有这个文件】这条需求的口语是「边距」，而实测证明**终端框四周的留白
只有一个来源**：`main` 的 padding。逐层分解（1440×900 真渲染，见
`tests/probe_term_box_gap.py` / `tests/probe_term_shot.py`）：

    上(main内) 13px = main padding-top 12 + .chat-main border-top 1
    左(main内) 17px = main padding-left 16 + .chat-main border-left 1
    下(main内) 13px / 右(main内) 17px   同上
    .chat-grid padding 四向 0、gap 16px —— 但它**已是单列**（.chat-side display:none
      且 #page-chat 宽屏规则覆盖成minmax(0,1fr)）⇒ gap 不产生任何列间距
    #termEl / .term-body padding 四向0，且被上一轮定案钉死（FitAddon 按#termEl
      内容盒算行数，父层多 1px 就多算一行、底部被裁）

所以「缩边距」只有一条合法改法：收窄 `main` 的 padding。但**不能直接改main 的基础值**
—— 那是全站正文留白，遥测/记忆/资产等每一页都会跟着变，而用户只点了终端框。
故走 `main:has(> #page-chat.on)`，只在本页生效。

【本闸门卡的五条，都是「会静默失效」的那类】
 1 收窄规则存在且**带 `:has(> #page-chat.on)` 限定**。
   少了限定 ⇒ 全站每一页都被改小，而用户只要求终端框（静默的越界改法）。
 2 比例是 `.4`（= 缩小 60%）且用 calc 挂在 `--main-pad-*` 上。
   写成裸 px 则窄屏档不会同比缩小（窄屏基础值是 8px，不是 12/16）。
 3 `--main-pad-*` 是**唯一真相源**：main 基础规则与窄屏档都只赋值对变量，
   不得再出现第二处 `main { padding: … }` 字面量（否则两处字面量各改各的，必漂）。
 4 **FitAddon 铁律仍在**：#termEl / .term-body 的 padding 必须四向 0。
   这一条是本次改动最容易误伤的地方——「终端框边距」很容易被理解成
   「给黑底加内衬」，而那会让终端底部被裁掉一行。
 5 断点值 767px 仍只有一处（分档偏好不变量③，本改动不得顺手新增分档）。
"""
import re
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
HTML = (_REPO / "templates" / "index.html").read_text(encoding="utf-8")

BREAKPOINT = 767

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _code() -> str:
    """剥掉 HTML 与 CSS/JS 注释后的模板正文。

    必须剥：本文件自己在注释里写了 `.chat-grid padding 四向 0`、`padding: …`、
    「缩边距」等**反例字面量**，不剥就是在给自己抓自己（test_narrow_first_paint.py
    已因此假红过两次，见其docstring）。
    """
    return _CSS_COMMENT.sub("", _HTML_COMMENT.sub("", HTML))


def _rules(selector: str):
    """抽出模板里所有 `selector { … }` 的声明体（按出现顺序）。"""
    return re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", _code())


def _decl(body: str, prop: str):
    m = re.search(re.escape(prop) + r"\s*:\s*([^;]+);", body)
    return m.group(1).strip() if m else None


class TermBoxGapTest(unittest.TestCase):
    def test_shrink_rule_exists_and_is_scoped_to_chat_page(self):
        """收窄规则存在，且**必须**用 `:has(> #page-chat.on)` 限定在本页。"""
        bodies = _rules("main:has(> #page-chat.on)")
        self.assertTrue(bodies,
                        "找不到 main:has(> #page-chat.on) 收窄规则 ⇒ 边距没收窄。"
                        "注意不能改成裸 main{ padding } —— 那是全站留白，"
                        "遥测/记忆/资产等页会一起被改小")
        self.assertEqual(len(bodies), 1,
                         "收窄规则出现了 %d 份 ⇒ 同一判据有两份真相源" % len(bodies))

    def test_shrink_ratio_is_40_percent_of_the_token(self):
        """比例必须是 `.4`（缩小 60%），且用 calc 挂在 --main-pad-* 上。

        写成裸 px 的话窄屏档不会同比缩小（窄屏基础值 8px，与宽屏 12/16 不同）。
        """
        body = _rules("main:has(> #page-chat.on)")[0]
        for prop in ("padding", "padding-top", "padding-bottom"):
            pass
        top = _decl(body, "padding-top")
        right = _decl(body, "padding-right")
        bottom = _decl(body, "padding-bottom")
        left = _decl(body, "padding-left")
        self.assertTrue(top and right and bottom and left,
                        "收窄规则必须四向都给全，只给一部分会留下单边不一致的留白")
        for side, val in (("top", top), ("right", right),
                          ("bottom", bottom), ("left", left)):
            self.assertIn("--main-pad-", val,
                          "%s 侧写成了裸值 %r ⇒ 窄屏档不会同比缩小"
                          "（窄屏基础值 8px，与宽屏 12/16px 不同）" % (side, val))
            self.assertRegex(val, r"\*\s*\.4\b|\*\s*0\.4\b",
                             "%s 侧的比例是 %r，不是 0.4 ⇒ 不是缩小 60%%" % (side, val))

    def test_main_padding_token_is_single_sourced(self):
        """`--main-pad-*` 是唯一真相源：不得再有第二处 main 的裸 padding 字面量。"""
        bodies = _rules("main")
        # 去掉收窄那条（它用 :has，不在 _rules("main") 里），剩下的都该只赋值对变量。
        literals = []
        for b in bodies:
            for prop in ("padding", "padding-top", "padding-right",
                         "padding-bottom", "padding-left"):
                v = _decl(b, prop)
                if v and not v.startswith("var("):
                    literals.append((prop, v))
        self.assertEqual(literals, [],
                         "main 上仍有裸 padding 字面量 %s ⇒ 与 --main-pad-* 是两份真相源，"
                         "窄屏档改了变量、这里没改，两处必然漂" % literals)

    def test_token_is_defined_in_base_and_narrow_block(self):
        """`--main-pad-*` 必须**两档都有定义**：宽屏 12/16、窄屏 8/8。"""
        base = _rules("main")[0]
        self.assertEqual(_decl(base, "--main-pad-y"), "12px", "宽屏上下留白基准不是 12px")
        self.assertEqual(_decl(base, "--main-pad-x"), "16px", "宽屏左右留白基准不是 16px")
        m = re.search(r"@media \(max-width: 767px\) \{(.*?)\n        \}", HTML, re.S)
        self.assertIsNotNone(m, "找不到窄屏 @media 块")
        narrow = [b for b in _rules("main") if "--main-pad" in b]
        self.assertTrue(narrow, "窄屏档没有覆盖 --main-pad-* ⇒ 窄屏会沿用 12/16px 基准")
        # 窄屏那条必须同时带 max-width 与变量赋值
        self.assertTrue(any("--main-pad-y: 8px" in b for b in narrow),
                        "窄屏 --main-pad-y 应为 8px，实际：%s" % narrow)

    def test_fitaddon_padding_stays_zero(self):
        """**FitAddon 铁律仍在**：#termEl / .term-body 的 padding 必须四向 0。

        这是本次改动最容易误伤的一条：「终端框边距」很容易被实现成
        「给黑底加内衬」，而那会让终端底部被裁掉一行（父层多 1px ⇒ 多算一行）。
        """
        for sel in ("#termEl", ".term-body"):
            bodies = _rules(sel)
            self.assertTrue(bodies, "找不到 %s 的规则" % sel)
            for b in bodies:
                for prop in ("padding", "padding-top", "padding-right",
                             "padding-bottom", "padding-left"):
                    v = _decl(b, prop)
                    if v is None:
                        continue
                    self.assertEqual(v, "0",
                                     "%s 的 %s 变成了 %r ⇒ 踩 FitAddon 行数口径，"
                                     "终端底部会被裁掉一行" % (sel, prop, v))

    def test_breakpoint_value_is_single_sourced(self):
        """断点仍只有 767px 一个取值（分档偏好不变量③，本次不得顺手新增分档）。"""
        pairs = re.findall(r"@media \(max-width[ :]+(\d+)px\) \{(.*?)\n        \}", HTML, re.S)
        main_blocks = [int(n) for n, b in pairs if re.search(r"(^|\s)main\s*\{", b)]
        self.assertTrue(main_blocks, "找不到管辖 main 的 @media 块")
        self.assertEqual(sorted(set(main_blocks)), [BREAKPOINT],
                         "管辖 main 的 @media 断点有多个取值：%s" % sorted(set(main_blocks)))


if __name__ == "__main__":
    unittest.main()