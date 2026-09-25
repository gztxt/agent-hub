"""回归测：终端进程退出必须说清「为什么没了」，不能只丢一句「[会话结束]」。

缺陷出处（2026-09-25 实测排查）：agent-hub 菜单点 OpenCode 秒退，终端上只有
  「[会话结束]」四个字。真因是 bun/JSC 在 swap 耗尽时主动 abort：
    ASSERTION FAILED: MemoryExhaustion → __builtin_trap() → ud2 → SIGILL
  内核记 `trap invalid opcode`，但 hub 把 waitpid 的退出状态直接丢了
  （src/term.py 原 `_cleanup()` 里写作 `pid, _st = os.waitpid(...)`，`_st` 从未使用）
  ⇒ 用户与排查者都看不到任何线索，只能靠 dmesg + objdump + ulimit -v 三步反推。

本测钉死三件事：
  ① describe_exit() 把各退出形态解成人话，**内存嫌疑信号必须点名**；
  ② SIGKILL/SIGTERM 是歧义信号（OOM-killer vs hub 自己发的），
     hub_killed=True 时**必须**说成"hub 主动终止"，绝不把用户关会话渲染成内存不足；
  ③ 无信息时返回空串 —— 宁可不说，不可编造原因。

全纯函数，不 fork pty、不起服务、不 import src.main。
跑法：cd ~/agent-hub && venv/bin/python -m unittest tests.test_term_exit_reason -v
"""
import os
import signal
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("TERM_TOKEN", "unit-test-token-not-production")

import term  # noqa: E402


def _sig_status(sig: int) -> int:
    """造一个 WIFSIGNALED 为真的 wait status（信号号占低 7 位）。"""
    return sig & 0x7F


def _exit_status(code: int) -> int:
    """造一个 WIFEXITED 为真的 wait status（退出码占 8~15 位）。"""
    return (code & 0xFF) << 8


class TestDescribeExitSignals(unittest.TestCase):
    def test_sigill_is_flagged_as_memory_suspect(self):
        """SIGILL = 本案真凶（bun/JSC 的 ud2 主动 abort），必须点名内存嫌疑。"""
        out = term.describe_exit(_sig_status(signal.SIGILL))
        self.assertIn("SIGILL", out)
        self.assertIn("疑似内存不足", out)

    def test_other_memory_suspect_signals_flagged(self):
        for sig in (signal.SIGSEGV, signal.SIGBUS, signal.SIGABRT, signal.SIGKILL):
            with self.subTest(sig=sig.name):
                self.assertIn("疑似内存不足", term.describe_exit(_sig_status(sig)),
                              f"{sig.name} 属内存嫌疑信号，必须给出提示")

    def test_non_memory_signal_not_flagged(self):
        """SIGTERM/SIGHUP 等不是内存问题 —— 不许乱贴「内存不足」标签（宁缺勿滥）。"""
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGPIPE):
            with self.subTest(sig=sig.name):
                out = term.describe_exit(_sig_status(sig))
                self.assertNotIn("内存", out, f"{sig.name} 不该被说成内存问题")
                self.assertIn(sig.name, out)

    def test_signal_number_always_present(self):
        """信号名解析不出来也要留数字，不能变成空话。"""
        out = term.describe_exit(_sig_status(64))   # 非常规信号号
        self.assertIn("64", out)


class TestHubKilledDisambiguation(unittest.TestCase):
    """SIGKILL/SIGTERM 既可能是内核 OOM-killer，也可能是 hub 自己发的（点 × / TTL /
    服务退出，见 kill() 与 kill_all()）。把"用户主动关会话"报成"内存不足"是误导。"""

    def test_hub_killed_sigkill_says_hub(self):
        out = term.describe_exit(_sig_status(signal.SIGKILL), hub_killed=True)
        self.assertIn("hub", out)
        self.assertNotIn("内存", out, "hub 自己发的 SIGKILL 不许说成内存不足")

    def test_hub_killed_sigterm_says_hub(self):
        out = term.describe_exit(_sig_status(signal.SIGTERM), hub_killed=True)
        self.assertIn("hub", out)
        self.assertNotIn("内存", out)

    def test_external_sigkill_still_suspects_memory(self):
        """不是 hub 发的 SIGKILL（如 OOM-killer）⇒ 保留内存嫌疑提示。"""
        self.assertIn("疑似内存不足",
                      term.describe_exit(_sig_status(signal.SIGKILL), hub_killed=False))

    def test_hub_killed_does_not_mask_real_crash(self):
        """hub_killed 只解释歧义信号；SIGILL 这类自崩溃照旧点名内存。"""
        out = term.describe_exit(_sig_status(signal.SIGILL), hub_killed=True)
        self.assertIn("疑似内存不足", out)


class TestDescribeExitNormal(unittest.TestCase):
    def test_clean_exit(self):
        self.assertEqual("正常退出", term.describe_exit(_exit_status(0)))

    def test_nonzero_exit_code_reported(self):
        for code in (1, 2, 127, 137):
            with self.subTest(code=code):
                self.assertIn(str(code), term.describe_exit(_exit_status(code)))

    def test_127_command_not_found_distinguishable(self):
        """127 是 pty 子进程 execvpe 失败的特征码（见 Session.__init__ 的 os._exit(127)），
        必须能与信号崩溃区分开 —— 那是"命令找不到"，不是"内存不足"。"""
        out = term.describe_exit(_exit_status(127))
        self.assertIn("127", out)
        self.assertNotIn("内存", out)
        self.assertNotIn("信号", out)


class TestNoFabrication(unittest.TestCase):
    """核心纪律：拿不到信息就返回空串，让调用方回落到原来的「[会话结束]」。
    绝不为了解释而编一个原因 —— 那比不解释更糟。"""

    def test_none_status_returns_empty(self):
        self.assertEqual("", term.describe_exit(None))

    def test_callers_fall_back_when_empty(self):
        """两处上屏文案在 reason 为空时必须与改动前完全一致（不追加空括号/空片段）。"""
        src = (Path(__file__).resolve().parents[1] / "src" / "term.py").read_text(encoding="utf-8")
        # EIO 分支：[会话结束] 仍是裸串，tail 为空
        self.assertIn('tail = f"\\r\\n\\x1b[90m[进程 {reason}]\\x1b[0m" if reason else ""', src)
        # pump 收尾：[process exited] 不带多余空格
        self.assertIn('tail = f" ({reason})" if reason else ""', src)


class TestStatusActuallyRecorded(unittest.TestCase):
    """回归销：`_st` 被丢弃是本缺陷的根，一旦有人改回下划线写法，本测必须红。"""

    def test_cleanup_does_not_discard_wait_status(self):
        src = (Path(__file__).resolve().parents[1] / "src" / "term.py").read_text(encoding="utf-8")
        self.assertNotIn("pid, _st = os.waitpid", src,
                         "又把 waitpid 的 status 丢了 —— 退出原因永远上不了屏")

    def test_session_records_exit_status(self):
        """_cleanup() 收到 status 后必须存到 exit_status（用桩验证，不真 fork）。"""
        s = term.Session.__new__(term.Session)   # 跳过 __init__（它会 pty.fork）
        s.pid, s.fd = 999999, -1
        s.alive, s._cleaned = True, False
        s.exit_status, s.hub_killed = None, False
        s.viewers, s.dropped = {}, {}
        import asyncio
        s.ring = bytearray()
        # remove_reader/close 都会失败但被 try 吞掉；只关心 status 有没有被记下
        with mock.patch.object(term.os, "waitpid", return_value=(999999, _sig_status(signal.SIGILL))), \
                mock.patch.object(asyncio, "get_event_loop", side_effect=RuntimeError("no loop")):
            s._cleanup()
        self.assertEqual(_sig_status(signal.SIGILL), s.exit_status)
        self.assertIn("疑似内存不足", term.describe_exit(s.exit_status, s.hub_killed))

    def test_first_status_wins(self):
        """exit_status 一旦记下就不许被后续 waitpid（多为 ChildProcessError）覆盖成 None。"""
        s = term.Session.__new__(term.Session)
        s.pid, s.fd = 999999, -1
        s.alive, s._cleaned = True, False
        s.exit_status, s.hub_killed = None, False
        s.viewers, s.dropped = {}, {}
        s.ring = bytearray()
        import asyncio
        first = _sig_status(signal.SIGSEGV)
        with mock.patch.object(term.os, "waitpid", return_value=(999999, first)), \
                mock.patch.object(asyncio, "get_event_loop", side_effect=RuntimeError("no loop")):
            s._cleanup()
        self.assertEqual(first, s.exit_status)
        # 幂等：第二次 _cleanup 直接 return，不许把已记录的 status 冲掉
        s._cleaned = False
        with mock.patch.object(term.os, "waitpid", side_effect=ChildProcessError):
            s._cleanup()
        self.assertEqual(first, s.exit_status, "退出状态被覆盖了 —— 面因丢失")

    def test_to_dict_exposes_exit_reason(self):
        s = term.Session.__new__(term.Session)
        s.id, s.agent_id, s.cmd, s.cwd = "sid1", "opencode", ["opencode"], "/tmp"
        s.alive, s.created, s.resume_of = False, 0.0, ""
        s.last_io, s.hub_killed = 0.0, False
        s.exit_status = _sig_status(signal.SIGILL)
        d = s.to_dict()
        self.assertIn("exit_reason", d, "API 没透出 exit_reason ⇒ 前端/排查仍看不到面因")
        self.assertIn("SIGILL", d["exit_reason"])


class TestKillPathsSetFlag(unittest.TestCase):
    """kill() 与 kill_all() 是 hub 唯一两处主动发信号的地方，都必须置 hub_killed。"""

    def test_kill_sets_hub_killed(self):
        s = term.Session.__new__(term.Session)
        s.pid, s.hub_killed = 999999, False
        with mock.patch.object(s, "_signal_group"):
            s.kill()
        self.assertTrue(s.hub_killed, "kill() 没置 hub_killed ⇒ 用户关会话会被报成内存不足")

    def test_kill_all_sets_hub_killed(self):
        s = term.Session.__new__(term.Session)
        s.pid, s.hub_killed = 999999, False
        with mock.patch.object(term, "_sessions", {"a": s}), \
                mock.patch.object(s, "_signal_group"):
            term.kill_all()
        self.assertTrue(s.hub_killed, "kill_all() 没置 hub_killed ⇒ 服务重启会被报成内存不足")


if __name__ == "__main__":
    unittest.main(verbosity=2)
