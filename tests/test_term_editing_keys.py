"""L0 hermetic：终端页「焦点不在时编辑键丢失」的静态护栏（v0.13.99）。

【要挡的缺陷】用户 2026-10-09 报障：「嵌入式终端会话文字输入框无法正常使用键盘的
上下左右光标键；输入框只是不需要鼠标焦点，但键盘的全部原生功能都是需要的」。

【根因（先取证后改，见 static/hub/06-manager-tasks.js 同名注释块）】
  ① v0.13.83 为解决「输入框与内容显示抢鼠标焦点致滚轮不能向上翻」而钉的
     `textarea.style.pointerEvents='none'` **没有**打断键盘 —— 真机实测点终端后
     焦点照样落在 `.xterm-helper-textarea`，方向键 `^[[A/B/C/D` 全到 pty。
     **滚轮与键盘是两条独立通路。**
  ② 真缺口在焦点离开终端之后：字符接力器原先只放行 `e.key.length === 1`，
     于是方向键 + Enter/Backspace/Tab/Home/End/PageUp/Delete **全部消失**
     （pty 收 0 份，浏览器也没拿它做别的 ⇒ 键彻底消失）。

【为什么这个缺陷需要静态闸门，而不是只靠 L2 探针】
  它的失效形态是**静默的**：页面完全正常、控制台无异常，只是那几类键不响应。
  L2 探针要真浏览器 + 真会话才跑得起来；L0 静态判据能在每次提交时把
  「映射表被谁删了一个键」当场抓红。本仓已因「删挂载点留写端」踩过同型
  （test_header_footer_trim.py 的 P1-18 注释）。

【判据设计的三条纪律】
  ① **不许只判「有映射表」** —— 那会假绿：表在但少一个键，照样红给用户看。
     故逐键断言表里有、且值非空。
  ② **不许判「旧代码已删」** —— 判据是**行为契约**（该键必须被投递），
     不是源码形态。换成任何等价实现都应该绿。
  ③ **滚轮路径必须仍绿**：本仓 v0.13.83 的 pointerEvents 那行是用户明确要保留的
     （滚轮向上翻），所以单独钉一条正向断言防止「修键盘顺手把滚轮修没了」。
"""
import re
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "static" / "hub" / "06-manager-tasks.js"
#: v0.13.83 的 pointerEvents 与滚轮看门狗在 **03**（ensureTerm / termMouseResetBind），
#: 键接力在 **06**。第一版闸门只读 06 ⇒ 两条滚轮断言恒红（假红，不是缺陷）。
#: 教训同本仓 test_hublog.py：判据引用的文件必须对着真实归属核一遍。
_CARDS = _REPO / "static" / "hub" / "03-agents-cards.js"
_NAV = _REPO / "static" / "hub" / "02-nav-and-poll.js"
_TPL = _REPO / "templates" / "index.html"
_RAW = _SRC.read_text(encoding="utf-8")
_CARDS_RAW = _CARDS.read_text(encoding="utf-8")
_NAV_RAW = _NAV.read_text(encoding="utf-8")
_HTML_RAW = _TPL.read_text(encoding="utf-8")

# 剥注释（与本仓 test_header_footer_trim.py / test_narrow_first_paint.py 同口径）
_SRC_BODY = re.sub(r"/\*.*?\*/", "", _RAW, flags=re.S)
_CARDS_BODY = re.sub(r"/\*.*?\*/", "", _CARDS_RAW, flags=re.S)
_NAV_BODY = re.sub(r"/\*.*?\*/", "", _NAV_RAW, flags=re.S)
_HTML_BODY = re.sub(r"<!--.*?-->", "", re.sub(r"/\*.*?\*/", "", _HTML_RAW, flags=re.S))

#: 必须被接力的编辑键。少任何一个都对应一类用户会立刻察觉的「键盘失灵」。
REQUIRED_EDIT_KEYS = (
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
    "Home", "End", "PageUp", "PageDown",
    "Insert", "Delete",
    "Enter", "Tab", "Backspace",
)

#: 刻意不接力的键：它们是**浏览器**语义或已由别处认领，放行才是对的。
MUST_NOT_RELAY = (
    "Escape",      # 归 pty（bash/vim），且本文件上方的 Esc 分支明确让给它
    "F5", "F12",   # 浏览器保留
    "CapsLock", "ContextMenu",
)


def _mapping_body() -> str:
    m = re.search(r"TERM_EDIT_KEYS\s*=\s*\{(.*?)\}\s*;", _SRC_BODY, re.S)
    assert m, "static/hub/06-manager-tasks.js 里找不到 TERM_EDIT_KEYS 映射表"
    return m.group(1)


class EditingKeyRelayTest(unittest.TestCase):
    """编辑键必须全部映射，且都映射成非空的标准序列。"""

    def test_mapping_table_exists(self):
        self.assertIn("TERM_EDIT_KEYS", _SRC_BODY,
                      "06-manager-tasks.js 缺 TERM_EDIT_KEYS —— "
                      "焦点不在终端时编辑键无处可去（v0.13.99 修的就是这个）")

    def test_every_required_key_is_mapped(self):
        """逐键断言（不判「表非空」——那会假绿）。"""
        body = _mapping_body()
        missing = [k for k in REQUIRED_EDIT_KEYS
                   if not re.search(r"\b%s\s*:" % re.escape(k), body)]
        self.assertEqual([], missing,
                         "这些编辑键没进 TERM_EDIT_KEYS ⇒ 焦点不在终端时按它们"
                         "毫无反应（用户报障原话「键盘的全部原生功能都是需要的」）：%s" % missing)

    def test_mapped_values_are_escape_sequences(self):
        """每个映射值必须以 ESC 开头（控制序列），Enter/Tab/Backspace 除外。

        为什么单独判：写成可见字符（如把 Backspace 映成 'B'）时表是「齐」的，
        但 pty 收到的是字面量 —— 症状是「按了没反应」而非「按了多了个字」，
        现场更难判断，故在 L0 就钉住「必须是控制序列」。
        """
        body = _mapping_body()
        pairs = re.findall(r"(\w+)\s*:\s*'((?:\\x[0-9a-fA-F]{2}|\\r|\\t|\\')*)'", body)
        plain_ok = {"Enter": "\\r", "Tab": "\\t", "Backspace": "\\x7f"}
        bad = []
        got = dict(pairs)
        for k in REQUIRED_EDIT_KEYS:
            if k not in got:
                continue                      # 缺失由上一条判据负责
            v = got[k]
            if k in plain_ok:
                if v != plain_ok[k]:
                    bad.append("%s=%r（应为 %r）" % (k, v, plain_ok[k]))
            elif not v.startswith("\\x1b"):
                bad.append("%s=%r 不是 ESC 序列" % (k, v))
        self.assertEqual([], bad, "映射值形状不对：%s" % bad)

    def test_arrow_keys_use_csi_letter_form(self):
        """四个方向键必须是 CSI 字母形态（ESC [ A/B/C/D）。

        刻意不接受 SS3（ESC O X）：本函数是**焦点不在**时的兜底通路，
        此时 xterm 的 applicationCursorKeysMode 未必与应用约定一致，
        CSI 字母形态是 readline 系（claude/codex）两档都收的那个。
        """
        body = _mapping_body()
        want = {"ArrowUp": "\\x1b[A", "ArrowDown": "\\x1b[B",
                "ArrowRight": "\\x1b[C", "ArrowLeft": "\\x1b[D"}
        for k, w in want.items():
            m = re.search(r"\b%s\s*:\s*'([^']*)'" % re.escape(k), body)
            self.assertIsNotNone(m, "映射表里没有 %s" % k)
            self.assertEqual(w, m.group(1), "%s 的映射值变了（应为 %r）" % (k, w))

    def test_reserved_keys_not_relayed(self):
        """刻意放行的键不得被塞进表里。

        Escape 尤其要紧：本文件上方还有一个 Esc 分支（抽屉/面板/搜索框优先），
        若这里也映射它，两处会打架 —— 终端里的 Esc 必须留给 pty。
        """
        body = _mapping_body()
        bad = [k for k in MUST_NOT_RELAY
               if re.search(r"\b%s\s*:" % re.escape(k), body)]
        self.assertEqual([], bad,
                         "这些键属于浏览器语义或已被别处认领，不该进 TERM_EDIT_KEYS：%s" % bad)

    def test_esc_still_delegated_to_pty(self):
        """ESC 分支必须继续把终端内的 Esc 让给 pty（本表刻意不含 Escape 的原因）。"""
        self.assertRegex(
            _SRC_BODY, r"closest\('\.xterm'\)\)\s*return;",
            "找不到「终端内 Esc 归 pty」那条分支 —— 若它被删，"
            "vim/bash 的 Esc 会被本文件上面的面板关闭逻辑抢走")


class RelayDeliveryContractTest(unittest.TestCase):
    """投递契约：命中映射表才 preventDefault —— 否则就是劫持。"""

    def test_prevent_default_comes_after_lookup(self):
        """顺序护栏：`seq` 必须**先**算出来，再 preventDefault。

        为什么是本批最容易犯的错：早前的写法是「无条件 preventDefault，
        后面才判断投不投递」。那样一旦映射表外的键（功能键等）也走到
        preventDefault，就是**货真价实的键盘劫持** —— 浏览器按键被吞、
        终端又不响应。判据用**下标先后**表达，不匹配具体写法。
        """
        m = re.search(r"const seq = .*?;?\s*\n\s*if \(seq === undefined\) return;.*?"
                      r"e\.preventDefault\(\);", _SRC_BODY, re.S)
        self.assertIsNotNone(m, "找不到「先查表再 preventDefault」的投递块")
        blk = m.group(0)
        self.assertLess(blk.index("seq"), blk.index("preventDefault"),
                        "preventDefault 必须排在映射查询之后")
        self.assertIn("if (seq === undefined) return;", blk,
                      "表外键必须原样放行（不能拦下又不投递）")

    def test_single_char_path_preserved(self):
        """单字符原样投递这条老口径不许丢（v0.13.22 空格修复的回归护栏）。"""
        m = re.search(r"const seq = \(typeof e\.key === 'string' && e\.key\.length === 1\)"
                      r"\s*\?\s*e\.key\s*:\s*TERM_EDIT_KEYS\[e\.key\]", _SRC_BODY)
        self.assertIsNotNone(m,
                             "单字符必须仍走 `e.key` 原样投递（v0.13.22 空格/字符接力的回归）")

    def test_modifier_keys_still_bail_out(self):
        """Ctrl/Cmd/Alt 组合键整体放行，不进本表。

        Ctrl+C=SIGINT、Ctrl+K=kill-line、Cmd+… 是浏览器语义 ——
        本仓 P2-11 的口径是「快捷键不得劫持输入位」，原样保留。
        """
        m = re.search(r"if \(e\.defaultPrevented \|\| e\.isComposing \|\|"
                      r" e\.ctrlKey \|\| e\.metaKey \|\| e\.altKey\) return;", _SRC_BODY)
        self.assertIsNotNone(m, "组合键放行守卫不见了 —— Ctrl/Cmd/Alt 会被误当编辑键投递")

    def test_editor_guard_still_precedes_lookup(self):
        """真输入位（搜索框/查找框/textarea）不得被投递。"""
        idx_guard = _SRC_BODY.find("if (keyTargetIsEditing(e)) return;")
        idx_seq = _SRC_BODY.find("const seq =")
        self.assertTrue(idx_guard > 0 and idx_seq > 0, "找不到输入位守卫或投递块")
        self.assertLess(idx_guard, idx_seq,
                        "输入位守卫必须排在投递之前（P2-11：不得劫持真输入位）")


class WheelPathUndisturbedTest(unittest.TestCase):
    """滚轮向上翻是用户明确要保留的功能（v0.13.83），本批不许动。"""

    def test_helper_textarea_still_pointer_events_none(self):
        """v0.13.83 那行必须还在 —— 它是滚轮能用的原因，别当成「副作用」删掉。

        ⚠ 取证结论（work/probe-arrow/probe_focus_chain.py）：这行**不影响键盘**。
        实测点终端后焦点照样落在 textarea、方向键全到 pty。
        所以修键盘的正确做法是**放宽键映射**，而不是回退这行。
        （判据读 03-agents-cards.js —— 那行在 ensureTerm() 里，不在 06。）
        """
        m = re.search(r"term\.textarea\.style\.pointerEvents\s*=\s*'none'", _CARDS_BODY)
        self.assertIsNotNone(m,
                             "03-agents-cards.js 里 helper-textarea 的 pointerEvents=none "
                             "不见了 ⇒ 滚轮向上翻会重新被输入框抢焦点（v0.13.83 的修复被回退）")

    def test_wheel_handlers_untouched(self):
        """滚轮/鼠标看门狗三个入口仍在（滚轮归浏览器的机制本体）。

        归属实测（第一版闸门把 termMouseArm 记成在 06、实际在 02，恒红一次）：
          03-agents-cards.js → termWheelNow / termMouseResetNow（看门狗本体）
          02-nav-and-poll.js   → termMouseArm（回装，写 term.write 那一路）
        """
        for fn, body in (("termWheelNow", _CARDS_BODY),
                         ("termMouseResetNow", _CARDS_BODY),
                         ("termMouseArm", _NAV_BODY)):
            self.assertIn("function %s(" % fn, body, "%s 不见了" % fn)
        m = re.search(r"addEventListener\('wheel',\s*termWheelNow,\s*"
                      r"\{\s*capture:\s*true,\s*passive:\s*true\s*\}\)", _CARDS_BODY)
        self.assertIsNotNone(m, "滚轮监听（capture+passive）被改动 —— "
                                "它是「滚轮归浏览器」的机制本体，本批不许动")


if __name__ == "__main__":
    unittest.main(verbosity=2)
