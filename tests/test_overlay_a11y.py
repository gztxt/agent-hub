"""L0 hermetic：抽屉的键盘/无障碍契约（P1-21，PT-20260930-01）。

改前的实测缺口（不是"锦上添花"，三条都会真出问题）：
  ① Esc 关不干净 —— 全局 Esc 出口只调 closeDetail()，skillDocDrawer 根本没接，
     技能正文抽屉键盘关不掉（手机上还能点遮罩，键盘用户直接卡死在里面）；
  ② 焦点在抽屉内的输入位时 Esc **整体失效** —— editing 早退排在整个 Esc 分支最前，
     而详情抽屉里恰好有云CLI 项目搜索框；改前只能靠点遮罩逃出去；
  ③ 抽屉打开后焦点仍留在被遮住的页面上，Tab 会跑到看不见的元素上（读屏串页）。

手机无 ESC 这条 09-23 已有（遮罩点击 = 唯一逃生路径），本批不重复造。

本测试锁：两个抽屉都在 Esc 出口里、不存在"只关一个"的写法；inert + Tab 循环
两档焦点陷阱都在；aria-modal 随开关同步；模板两个抽屉都有 tabindex 兜底落点。
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "static" / "hub" / "01-core-boot.js"
KEYS = REPO / "static" / "hub" / "06-manager-tasks.js"
TPL = REPO / "templates" / "index.html"
DRAWERS = ("detailDrawer", "skillDocDrawer")


def _src(p: Path) -> str:
    s = p.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


class TestEscapeClosesEveryDrawer(unittest.TestCase):
    def setUp(self):
        self.core = _src(CORE)
        self.keys = _src(KEYS)

    def test_escape_outlet_uses_close_drawers_not_single_drawer(self):
        seg = self.keys.split("e.key === 'Escape'", 1)[1].split("if (editing) return", 1)[0]
        self.assertIn("closeDrawers", seg,
                      "Esc 出口没有走 closeDrawers：只关一个抽屉，另一个键盘关不掉")
        self.assertNotRegex(seg, r"closeDetail\(\);",
                            "Esc 分支里出现裸 closeDetail()：会漏掉 skillDocDrawer")

    def test_escape_defers_to_terminal_first(self):
        """终端里的 Esc 仍归 pty，且**排在抽屉判定之前**（终端开着浮层时也不能被抢）。"""
        seg = self.keys.split("e.key === 'Escape'", 1)[1].split("if (editing) return", 1)[0]
        i_term = seg.find("closest('.xterm')")
        i_drawer = seg.find("overlayAnyOpen")
        self.assertNotEqual(i_term, -1, "Esc 分支没有让终端先拿（vim 的 Esc 会被界面吃掉）")
        self.assertNotEqual(i_drawer, -1, "Esc 分支没有抽屉判定")
        self.assertLess(i_term, i_drawer, "终端让行必须排在抽屉判定之前")

    def test_escape_before_editing_guard(self):
        """editing 早退必须在抽屉判定之后 —— 否则抽屉里的输入位按 Esc 没反应。"""
        i_editing = self.keys.find("if (editing) return", self.keys.find("e.key === 'Escape'"))
        i_drawer = self.keys.find("overlayAnyOpen", self.keys.find("e.key === 'Escape'"))
        self.assertNotEqual(i_editing, -1)
        self.assertNotEqual(i_drawer, -1)
        self.assertLess(i_drawer, i_editing, "editing 早退排在了抽屉判定之前＝抽屉内 Esc 失效")


class TestFocusTrap(unittest.TestCase):
    def setUp(self):
        self.core = _src(CORE)

    def test_inert_used(self):
        self.assertRegex(self.core, r"\.inert\s*=", "抽屉开启时没有对正文 inert，Tab 能跑到遮住的元素上")

    def test_inert_is_written_bidirectionally(self):
        """inert 必须**每次都显式赋值**，不能只在"开着"时置 true。

        实测过的坑（真渲染探针 O8a 抓到的，不是推演）：只写
            if (openId && openId !== id) el.inert = true;
        抽屉全关后 openId 为 undefined ⇒ 走不进任何分支 ⇒ detailDrawer.inert
        永久卡在 true。此后该抽屉里任何元素都收不到焦点（焦点掉 BODY），
        且**再打开时整个抽屉点不动** —— 键盘用户彻底卡死在一个"看得见开着的抽屉"里。
        闸门锁的是"有 else 复位"，不是锁写法。
        """
        seg = self.core.split("function syncOverlayA11y(", 1)[1].split("\n}", 1)[0]
        # 遍历 OVERLAY_IDS 实现（不写死字面 id），这里只锁"双向赋值"这一条语义
        self.assertRegex(seg, r"OVERLAY_IDS\.forEach", "inert 复位没覆盖全部抽屉")
        self.assertRegex(seg, r"\.inert\s*=\s*!!",
                         "inert 没有双向显式赋值：抽屉关闭后 inert 残留 true（抽屉点不动）")
        self.assertNotRegex(seg, r"if \([^)]*openId[^)]*\)\s*el\.inert\s*=\s*true",
                            "inert 只在开时置 true ＝ 关闭后永久残留")

    def test_inert_target_is_the_page_wrapper(self):
        self.assertIn("mainWrap", self.core, "inert 落点应锁定正文容器 #mainWrap，而不是 body（会连抽屉一起禁）")

    def test_tab_cycles_inside_open_drawer(self):
        """inert 之外的显式兜底：Safari 与老 Chromium 对 inert 支持不齐。"""
        self.assertRegex(self.core, r"e\.key\s*!==\s*'Tab'\s*\)\s*return",
                         "没有 Tab 循环兜底（Safari/老 Chromium 无 inert 时抽屉会漏焦点）")
        self.assertRegex(self.core, r"shiftKey", "Tab 循环没有处理 Shift+Tab 反向")

    def test_focus_lands_inside_drawer_on_open(self):
        seg = self.core.split("function openOverlay(", 1)[1].split("\n}", 1)[0]
        self.assertRegex(seg, r"\.focus\(", "抽屉打开时焦点没进去（键盘用户要 Tab 一圈找路）")

    def test_focus_returns_to_opener_on_close(self):
        self.assertIn("rememberOverlayOpener", self.core, "没有记录开启者，关闭后焦点掉回 body 顶")
        seg = self.core.split("function closeDrawers(", 1)[1].split("\n}", 1)[0]
        self.assertRegex(seg, r"overlayOpener\s*&&[^;]*\.focus\(",
                         "关闭抽屉没把焦点还给开启者")

    def test_opener_not_clobbered_when_switching_drawers(self):
        """openOverlay 先记开启者再 closeDrawers —— 顺序反了会把焦点记到刚开的抽屉上。"""
        seg = self.core.split("function openOverlay(", 1)[1].split("\n}", 1)[0]
        self.assertLess(seg.find("rememberOverlayOpener"), seg.find("closeDrawers"),
                        "先 closeDrawers 再记开启者＝焦点归属记错抽屉")

    def test_focus_not_stolen_if_closed_immediately(self):
        seg = self.core.split("function openOverlay(", 1)[1].split("\n}", 1)[0]
        self.assertRegex(seg, r"classList\.contains\('on'\)\)\s*return",
                         "延时聚焦没检查抽屉是否已关：开完立刻关会把焦点抢回抽屉")


class TestAriaContract(unittest.TestCase):
    def setUp(self):
        self.core = _src(CORE)
        self.tpl = TPL.read_text(encoding="utf-8")

    def test_aria_modal_toggled(self):
        self.assertRegex(self.core, r"setAttribute\('aria-modal'",
                         "aria-modal 从不随开关同步（读屏不知道这是模态）")

    def test_aria_hidden_on_inactive_drawers(self):
        self.assertRegex(self.core, r"setAttribute\('aria-hidden'",
                         "未开启的抽屉没标 aria-hidden，读屏会连隐藏内容一起念")

    def test_both_drawers_have_role_dialog(self):
        for d in DRAWERS:
            self.assertRegex(self.tpl, rf'id="{d}"[^>]*role="dialog"', f"{d} 缺 role=dialog")

    def test_both_drawers_have_tabindex_fallback(self):
        for d in DRAWERS:
            seg = re.search(rf'<aside id="{d}"[^>]*>', self.tpl).group(0)
            self.assertIn('tabindex="-1"', seg, f"{d} 缺 tabindex 兜底落点（无子元素时焦点无处可落）")

    def test_main_wrapper_exists_in_template(self):
        self.assertRegex(self.tpl, r'<main id="mainWrap"', "模板里没有 #mainWrap，前端 inert 会退化成 body（连抽屉一起禁）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
