"""L0 · 终端焦点与全局快捷键的**结构性**护栏（纯文本断言，零宿主依赖）。

为什么需要静态护栏：这两条修复的失效方式很安静 —— 后人只要在别处补一句
`term.focus()`，或者把 `/` 分支挪到 `if (editing) return` 之前，
手机就又开始弹软键盘、终端里又开始吞 `/`，而 node --check 与单元测试全都不会红。
所以这里守的是"**位置**"，不是"存在"：

  1. `term.focus()` 全仓只能出现在 `if (termFocusWanted(opts))` 之后同一行；
  2. 七个 termConnect 入口里，恰好 5 个带 `{ user: true }`（用户主动：
     startAgent / termNew / termResume / lpStart(本机项目页, v0.13.30) / 芯片包装器
     termOpenChip），2 个刻意不带（自动挂载 / 退避重连）—— 数量与身份都钉住；
  3. 全局 keydown 里 `if (editing) return` 必须出现在 `/` 与 Ctrl+K 分支之前；
  4. 内联 onclick 走的是带 user:true 的包装器 termOpenChip，不是裸 termConnect。

行为层（真 DOM 里 keyTargetIsEditing 认不认得 contenteditable / .xterm 子孙）
在 chromium 探针 tests/verify_key_focus_guard.py 里取证，两层互补。
"""
import re
import unittest
from pathlib import Path

HUB = Path(__file__).resolve().parents[1] / "static" / "hub.js"
SRC = HUB.read_text(encoding="utf-8")


def _lines(pattern):
    return [(n, l.strip()) for n, l in enumerate(SRC.splitlines(), 1) if re.search(pattern, l)]


class TestFocusPolicy(unittest.TestCase):
    def test_only_guarded_term_focus(self):
        """所有 term.focus() 必须被 termFocusWanted 罩着，且在同一行。"""
        hits = _lines(r"(?<!\w)term\.focus\(\)")
        self.assertGreaterEqual(len(hits), 1, "一个 term.focus() 都没有＝功能被删了")
        for n, line in hits:
            with self.subTest(line=n):
                self.assertIn("termFocusWanted(opts)", line,
                              f"第 {n} 行有无守卫的 term.focus()：{line[:80]}")

    def test_predicate_semantics(self):
        """termFocusWanted 只认 opts.user，别的入参（含 reconnect）一律不抢焦点。"""
        m = re.search(r"function termFocusWanted\(opts\) \{ return (!+|\S+?)\(opts && opts\.user\);", SRC)
        self.assertIsNotNone(m, "termFocusWanted 实现被改写，请同步本护栏")
        self.assertNotIn("reconnect", SRC.split("function termFocusWanted")[1].split("\n")[0],
                         "重连不该参与焦点判断")

    def test_user_entry_points_count(self):
        """5 个用户主动入口带 user:true；2 个自动路径不带。钉数量也钉身份。

        v0.13.30 起 user:true 的直接调用点为 4 处（startAgent / termNew /
        termResume / lpStart）+ 芯片包装器 termOpenChip；lpStart 是本机项目页
        的「新建会话」按钮——用户主动点击，必须抢焦点（否则新起的会话黑屏无焦点）。"""
        # 包装器 termOpenChip 的定义行本身也是 `termConnect(sid, agent, { user: true })`：
        # 它是字面上的第 5 处匹配，但不是调用点。第一版就在这条上红，别把测试 bug 记成产品 bug。
        user = [l for _, l in _lines(r"termConnect\([^)]*\{ user: true \}\)") if "function " not in l]
        self.assertEqual(len(user), 4, f"直接调用点应恰好 4 处带 user:true，实得 {len(user)}")
        chip = _lines(r"function termOpenChip\(sid, agent\) \{ termConnect\(sid, agent, \{ user: true \}\); \}")
        self.assertEqual(len(chip), 1, "芯片包装器丢了（内联 onclick 会退化成无守卫直连）")
        # 自动路径：重连 + 自动挂载，必须**不**带 user
        auto = [l for _, l in _lines(r"termConnect\(")
                if ("{ reconnect: true }" in l or re.search(r"termConnect\(last, chatPick\);", l))]
        self.assertEqual(len(auto), 2, f"自动路径应恰好 2 处且不带 user:true，实得 {auto}")
        for l in auto:
            self.assertNotIn("user: true", l, f"自动挂载/重连被加了 user:true＝手机又开始弹键盘：{l[:80]}")

    def test_inline_onclick_uses_wrapper(self):
        """内联 onclick 只能走 termOpenChip，不许出现裸 termConnect。"""
        oc = _lines(r"onclick=\\?\"?termConnect\(")
        self.assertEqual(len(oc), 0, f"内联 onclick 直连 termConnect 会绕过 user 标记：{oc}")
        self.assertEqual(len(_lines(r"onclick=.*termOpenChip\(")), 1, "芯片 onclick 应指向 termOpenChip")


class TestKeyGuard(unittest.TestCase):
    def setUp(self):
        i = SRC.find("document.addEventListener('keydown'")
        self.assertGreater(i, 0, "全局 keydown 处理器找不到了")
        # 只取这个监听器自己的函数体（到下一个顶层 `});` 为止）
        tail = SRC[i:]
        end = tail.find("\n});")
        self.assertGreater(end, 0, "监听器收尾形状变了，请同步本护栏")
        self.body = tail[:end]

    def test_predicate_defined(self):
        self.assertIn("function keyTargetIsEditing(e)", SRC, "守卫谓词被删")
        for pat in (r"closest\('\.xterm'\)", r"'INPUT'", r"'TEXTAREA'", r"'SELECT'",
                    r"isContentEditable"):
            self.assertRegex(SRC, pat, f"keyTargetIsEditing 少了 {pat} 这一类输入位的识别")

    def test_editing_return_before_shortcuts(self):
        """`if (editing) return` 必须在 / 与 Ctrl+K 分支之前，否则守卫形同虚设。"""
        # str.find 吃的是字面量，喂正则进去只会永远找不到（第一版就在这条上红）：
        # 要比"位置先后"就统一用 re.search 比 span。
        gate = re.search(r"if \(editing\) return", self.body)
        self.assertIsNotNone(gate, "编辑器守卫没接进监听器")
        for name, pat in (("Ctrl+K", r"e\.key === 'k' \|\| e\.key === 'K'"),
                          ("/ 聚焦搜索", r"e\.key === '/'")):
            j = re.search(pat, self.body)
            self.assertIsNotNone(j, f"{name} 分支不见了")
            self.assertLess(gate.end(), j.start(), f"{name} 分支排在守卫之前＝仍然劫持终端按键")

    def test_escape_defers_to_pty_except_search_box(self):
        """Esc 在终端里必须原样给 pty（vim 要用），只有搜索框自己认领清空。"""
        self.assertRegex(self.body, r"if \(editing && !\(e\.target && e\.target\.id === 'navSearch'\)\) return",
                         "Esc 分支不再区分输入位＝vim 的 Esc 会被界面吃掉")


if __name__ == "__main__":
    unittest.main(verbosity=2)
