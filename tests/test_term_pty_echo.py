"""pty 建会话即关 ECHO —— 治「终端查询的 xterm 作答被 tty 回显成乱码」（PT-20261008-12）。

## 为什么这个文件存在

`cursor-agent` 这类 Node/Ink CLI **先发终端查询、之后才进 raw 模式**：开机发
`\\x1b]11;?\\x07`（问背景色）→ 浏览器 xterm 自动作答 → 答案灌回 pty 时 pty 的
`ECHO` 还开着 ⇒ 被 tty 原样回显成 `^[]11;rgb:…^\\`，即用户报的「启动带入乱码字符」。
修法：`Session.__init__` 里 `pty.fork()` 之后**清掉 ECHO 系标志**（只清 ECHO、
不整体 raw：交互式 shell / 全屏 TUI 各自会重设 termios）。

判例：`agent-knowledge/96-cursor启动乱码-OSC11作答被pty回显.md`。

## 判据为什么这样写

1. **结构判据**：建会话后 pty 的 `ECHO` 位必须已清 —— 直接读真 termios，不靠推断。
2. **行为判据**：往 pty 注入一段「终端作答」，输出里**不许**出现它 —— 这才是用户看到的量。
3. **阳性对照（同文件自带红向）**：把 ECHO 手动开回去，同一注入**必须**被回显
   ⇒ 证明第 2 条断言真能发现回显，而不是恒真的假绿（删掉修复代码本文件必红）。

多 agent 普适性（pi/jcode/codex/hermes/grok/qodercli/opencode/cursor 八个 agent 的
首个查询都落在 ECHO 窗口内）由 `work/probe-agent-echo-window.py` 实测，不进本文件
（依赖具体 CLI 安装，不宜做 L0）。
"""
import os
import select
import sys
import termios
import time
import unittest
import uuid
from pathlib import Path

# 与 tests/test_term_fast_reap.py 同构：src/ 入 path 后裸 import（term.py 内部是平铺导入）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import term  # noqa: E402

_MARK = b"\x1b]11;rgb:0abc/0def/0123\x07"
_MARK_TAIL = b"rgb:0abc"


class TestPtyEchoSuppressed(unittest.TestCase):
    def _mk(self):
        # sh -c 'sleep N'：非交互 shell，自己**不重设** termios ⇒ 恰好暴露 pty 建会话时的初值。
        s = term.Session("t-echo-" + uuid.uuid4().hex[:8], "test",
                         ["sh", "-c", "sleep 5"], os.getcwd())
        self.addCleanup(s._cleanup)
        return s

    @staticmethod
    def _drain(fd, secs):
        out = bytearray()
        t0 = time.time()
        while time.time() - t0 < secs:
            r, _, _ = select.select([fd], [], [], 0.05)
            if not r:
                continue
            try:
                d = os.read(fd, 65536)
            except (BlockingIOError, OSError):
                break
            if not d:
                break
            out.extend(d)
        return bytes(out)

    def test_echo_bit_cleared_on_create(self):
        s = self._mk()
        attr = termios.tcgetattr(s.fd)
        self.assertFalse(
            attr[3] & termios.ECHO,
            "建会话后 pty 的 ECHO 必须已清（PT-20261008-12）——否则 xterm 的终端查询作答"
            "会被 tty 回显成乱码",
        )

    def test_injected_terminal_answer_is_not_echoed(self):
        s = self._mk()
        self._drain(s.fd, 0.3)          # 等 fork 落定
        os.write(s.fd, _MARK)           # 模拟浏览器把 OSC11 作答灌回 pty
        out = self._drain(s.fd, 0.7)
        self.assertNotIn(
            _MARK_TAIL, out,
            "注入的终端作答出现在输出里 = 被 tty 回显 = 用户看到的乱码回归",
        )

    def test_red_control_noise_when_echo_forced_on(self):
        """阳性对照：把 ECHO 手动开回去，同一注入**必须**被回显。

        这条不是产品行为，是**判据自证** —— 没有它，上面那条 assertNotIn 可能是
        「因为探针压根读不到东西」而恒真（假绿）。
        """
        s = self._mk()
        attr = termios.tcgetattr(s.fd)
        attr[3] |= termios.ECHO
        termios.tcsetattr(s.fd, termios.TCSANOW, attr)
        self._drain(s.fd, 0.3)
        os.write(s.fd, _MARK)
        out = self._drain(s.fd, 0.7)
        self.assertIn(
            _MARK_TAIL, out,
            "阳性对照失败：开 ECHO 后注入却没被回显 ⇒ 上一条判据测不出回显，是假绿",
        )


if __name__ == "__main__":
    unittest.main()