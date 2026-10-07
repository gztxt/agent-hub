"""L0 hermetic：备用屏进入必须「吞 + 同步清屏」（v0.13.91，PT-20261008-02）。

用户报障原句：「agent-hub 的 jcode 启动嵌入式终端时会带入乱码」（2026-10-08）。
根因是 v0.13.83 只吞 `?1049h`、不切备用屏缓冲，于是 TUI 在旧画面上作画，
hub 头部与 `Connecting to server...` 残影和 TUI 首帧叠在一起（详见
`static/hub/03-agents-cards.js` 里 `termAltScreenBlock` 的 ★ 注释，以及
`tests/verify_term_altclear.py` 的真浏览器红绿对照）。

为什么这批要静态锁：真浏览器探针（L2）本事可复现，但**它不在 `run_tests.sh` 的标准层里
自动跑**；而回归最隐蔽的形态就是这个同步清屏被「顺手删掉/改成 term.write」——
那时页面仍能显示、截图也未必看得出残影，只有往旧画面上喂一帧 TUI 才现形。
静态闸门锁的是「这句还在、且是同步调用」，与 L2 行为探针互补。
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHARD = REPO / "static" / "hub" / "03-agents-cards.js"
BUILT = REPO / "static" / "hub.js"

#: 同步清屏那一声（真实现）。改法换了写法时同步更新本常量与上面的 ★ 注释。
SYNC_CLEAR = re.compile(r"term\.clear\(\)")
#: 反例：xterm 的 write 是异步队列，`term.write('\x1b[2J')` 会被排到本帧之后、
#: 把刚画好的 TUI 一起抹掉（L2 探针的 V3 档实测）。出现即判红。
ASYNC_CLEAR = re.compile(r"term\.write\(\s*['\"]\\\\x1b\[2J")

#: ★ 注释里本来就写着 `term.clear()`（解释"必须同步"），若不先剥注释，
#: 「删了真调用、只留注释」也会假绿。剥注释后再判，才是判「真代码里有这句」。
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?m)//[^\n]*")


def _strip_comments(s: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", s))


def _block_body(src: str) -> str:
    i = src.find("function termAltScreenBlock(")
    assert i >= 0, "函数 termAltScreenBlock 不在（v0.13.83 的备用屏拦截被删？）"
    end = src.find("\n}\n", i)
    assert end >= 0, "termAltScreenBlock 没有第 0 列的收尾 }"
    return src[i:end + 2]


class TestAltEnterClearsScreen(unittest.TestCase):
    def setUp(self):
        self.src = SHARD.read_text(encoding="utf-8")
        self.body = _block_body(self.src)
        self.code = _strip_comments(self.body)

    def test_block_set_intact(self):
        self.assertRegex(
            self.src, r"const TERM_ALT_BLOCKED = new Set\(\['1049', '1047', '47'\]\)",
            "备用屏拦截集合被改动 —— 1049/1047/47 三条都要在")

    def test_judgement_covers_any_param(self):
        # DECSET 可合并（`?1003;1049h`）：只认 p[0] 会漏吞 ⇒ 照旧进备用屏
        self.assertIn("vals.some", self.code,
                      "判据退回『只看 p[0]』了 —— 合并串 `?1003;1049h` 会漏吞")
        self.assertNotIn("TERM_ALT_BLOCKED.has(String(v))", self.code,
                         "退回旧写法：只按单个 v 判定，漏吞合并串")

    def test_synchronous_clear_present(self):
        self.assertRegex(self.code, SYNC_CLEAR,
                         "吞备用屏时没有同步 term.clear() —— 旧画面残影会与 TUI 叠加（本次报障）")
        self.assertNotRegex(self.code, ASYNC_CLEAR,
                            "清屏被写成异步 term.write('\\x1b[2J') —— 会连 TUI 一起抹掉，必须同步 term.clear()")

    def test_rationale_pinned(self):
        # 半年后有人想删这句，得先看到为什么：报障日期 + 症状关键词
        self.assertIn("2026-10-08", self.body, "★ 注释里的报障日期丢了")
        self.assertIn("乱码", self.body, "★ 注释里的报障原句丢了")

    def test_built_hub_js_in_sync(self):
        built = _block_body(BUILT.read_text(encoding="utf-8"))
        self.assertEqual(
            built, self.body,
            "static/hub.js 与分片不一致 —— 改了分片忘 `bash scripts/build_hubjs.sh`（本仓踩过）")


if __name__ == "__main__":
    unittest.main()