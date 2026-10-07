#!/usr/bin/env python3
"""L0 hermetic：嵌入式终端「选区抗回装 + 右键菜单」（v0.13.92，PT-20261008-03）。

锁形状，防后人把本次修复「顺手」改回去：

  A   `termMouseArm()` 必须在 `term.hasSelection()` 时**早退**：xterm 的
      CoreMouseService 协议一进鼠标跟踪就 `SelectionService.disable()`
      （实现是 `clearSelection()`）⇒ 有选区还回装 = 把刚拖出的选区当场抹掉。
  B   回装时机在 `termWheelNow()`（不是 mouseup）：live 分支里**仅当无选区**才
      `termMouseArm()`，且随后 `return`（保证本格滚轮不被复位）。
  C   右键菜单三件套（Open/Close/Bind）存在；`#termCtx` 的 `add('on')` 全仓只有
      termCtxOpen 一处（浮层唯一入口）；ensureTerm 里确实调了 termCtxBind()；
      `contextmenu` 上挂了 preventDefault（盖掉 canvas 的图片菜单）。
  D   HTML 里 #termCtx 与三个按钮 id 齐全，且有角色/aria（role=menu / menuitem）。
  E   红基线自证：v0.13.91 的 `termMouseArm` 里**没有** hasSelection 判据，
      本闸门在旧字节上必须判红（从 git 取，不靠记忆）。

L0 铁律：不 import src.main、不联网、不起服务；读分片源码文本 + git 对象。
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _js_min import strip_comments          # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PRE_SHA = "c01cfc8"   # v0.13.91：本修复的上一个版本，红基线从这里取


def _git(rev, path):
    r = subprocess.run(["git", "-C", str(REPO), "show", "%s:%s" % (rev, path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError("取不到 %s:%s —— 红基线取不到，本闸门会退化成自证" % (rev[:7], path))
    return r.stdout


class TestTermSelectPersist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s02 = strip_comments((REPO / "static/hub/02-nav-and-poll.js").read_text(encoding="utf-8"))
        cls.s03 = strip_comments((REPO / "static/hub/03-agents-cards.js").read_text(encoding="utf-8"))
        cls.tpl = (REPO / "templates/index.html").read_text(encoding="utf-8")

    def test_A_arm_skips_when_selection_exists(self):
        i = self.s02.index("function termMouseArm(")
        body = self.s02[i:self.s02.index("\n}\n", i)]
        self.assertRegex(body, r"if\s*\(\s*term\.hasSelection\(\)\s*\)\s*return;",
                         "termMouseArm 必须「有选区就不回装」——否则 xterm 的 "
                         "SelectionService.disable() 会 clearSelection()，用户拖完一松手选区就没了")

    def test_A2_swallow_app_mouse_decset_while_selection(self):
        """有选区时 app 的鼠标跟踪 DECSET 必须被吞 —— 否则它一重断言就把选区清掉。"""
        i = self.s03.index("function termAltScreenBlock(")
        body = self.s03[i:self.s03.index("\n}\n", i)]
        self.assertRegex(
            body,
            r"TERM_MOUSE_MODES\.has\(String\(x\)\)\)\s*&&\s*term\.hasSelection\(\)",
            "有选区时必须吞掉 app 的 ?100x h：xterm 一进跟踪态就 SelectionService.disable() "
            "（clearSelection()）⇒ agent 流式输出时永远选不中")

    def test_B_wheel_rearms_only_without_selection(self):
        i = self.s03.index("function termWheelNow(")
        body = self.s03[i:self.s03.index("\n}\n", i)]
        self.assertRegex(body, r"if\s*\(\s*!term\.hasSelection\(\)\s*\)\s*termMouseArm\(\);",
                         "回装时机在 wheel：仅当无选区时调 termMouseArm()")
        self.assertRegex(body, r"return;",
                         "live 分支该 return（本格滚轮不复位）")
        # 旧形状（live 直接 return，不回装）出现即红：那会让「松手后滚轮恢复应用内滚动」断在半路。
        self.assertNotRegex(body, r"if\s*\(\s*termMouseLive\s*\)\s*return;\s*\n\s*termMouseResetNow\(\);",
                            "回到 v0.13.84 的「live 直接 return」= 选区清了也不回装")

    def test_C_ctx_menu_single_entry_and_wired(self):
        for fn in ("termCtxOpen", "termCtxClose", "termCtxBind"):
            self.assertIn("function %s(" % fn, self.s03, "缺右键菜单函数 %s" % fn)
        self.assertIn("termCtxBind();", self.s03, "ensureTerm 里必须真的挂上右键菜单")
        # 唯一入口：整个 termCtx* 区块里 add('on') 只允许一处
        a = self.s03.index("function termCtxClose(")
        b = self.s03.index("function termFindOpen(", a)
        self.assertEqual(self.s03[a:b].count("classList.add('on')"), 1,
                         "浮层纪律：开启必须只有 termCtxOpen 一个入口（AGENTS 4.2 ①）")
        self.assertIn("classList.remove('on')", self.s03)
        # 右键必须 preventDefault，否则浏览器照样弹「图片另存为」
        i = self.s03.index("el.addEventListener('contextmenu'")
        seg = self.s03[i:i + 400]
        self.assertIn("preventDefault()", seg, "contextmenu 必须被接管，盖掉 canvas 的图片菜单")

    def test_D_html_menu_present(self):
        for eid in ("termCtx", "termCtxCopy", "termCtxPaste", "termCtxSelAll"):
            self.assertIn('id="%s"' % eid, self.tpl, "缺 #%s" % eid)
        i = self.tpl.index('id="termCtx"')
        seg = self.tpl[i - 60:i + 600]
        self.assertIn('role="menu"', seg, "菜单要有 role=menu")
        self.assertGreaterEqual(seg.count('role="menuitem"'), 3, "三项都要 role=menuitem")

    def test_F_new_session_clears_stale_selection(self):
        """换会话必须清掉上一会话遗留的选区：否则新会话来声明鼠标模式时 termMouseArm 早退
        （「有选区」），滚轮上报通道建不起来（实测 opencode A1 rep=0 转红）。"""
        i = self.s03.index("if (!keepScreen) {")
        seg = self.s03[i:i + 500]
        self.assertIn("term.clear()", seg)
        self.assertIn("term.clearSelection()", seg,
                      "term.clear() 不清选区（vendor 实证）⇒ 换会话必须显式 clearSelection()")

    def test_E_red_baseline_is_caught(self):
        old = strip_comments(_git(PRE_SHA, "static/hub/02-nav-and-poll.js"))
        i = old.index("function termMouseArm(")
        body = old[i:old.index("\n}\n", i)]
        self.assertNotIn("hasSelection", body,
                         "红基线取错了版本？v0.13.91 的 termMouseArm 没有 hasSelection 判据")
        # 红基线里也还没有右键菜单
        old03 = _git(PRE_SHA, "static/hub/03-agents-cards.js")
        self.assertNotIn("function termCtxOpen(", old03)
        self.assertNotIn("TERM_MOUSE_MODES.has(String(x))) && term.hasSelection()", old03,
                         "红基线的 termAltScreenBlock 不该有「有选区吞鼠标 DECSET」这条")


if __name__ == "__main__":
    unittest.main()