"""回归测：终端会话回收与心跳语义（P0-1 / P0-2 / P0-3）。

三条都来自 2026-09-29 全量审计，且都是「实弹证伪过代理结论」的确认项。
本测不用 mock 糊弄：会话用**真 pty.fork + 真子进程**（跑 sleep），
回收用**真 _reap()**，探活用**真 waitpid**。

  ① P0-1 心跳不得续命 TTL
     前端每 15s 发 {"type":"hb"}（static/hub/02-nav-and-poll.js:206）。旧实现
     `ws.receive()` 后无条件 `sess.last_io = time.time()` ⇒ 45min 空闲 TTL 对任何
     还开着的终端永不触发。判据：喂 hb 帧 last_io 不动；喂 resize/输入帧才动。

  ② P0-2 子进程自行退出必须被回收
     旧实现里「PTY 读端可读」与「对端退出」在 fd 层同签名，且没有观看者时
     add_reader 回调根本不会把 alive 置 False ⇒ 会话永远占着 MAX_SESSIONS(8)
     的名额，用户表现为「开过几个终端后再也开不出新的」。判据：子进程退出后
     _reap() 必须把它从 _sessions 摘掉。

  ③ P0-3 os.write 的 EAGAIN 不得当致命错
     BlockingIOError **是** OSError 子类 ⇒ 旧 `except OSError: break` 把正常的
     写背压当成致命错误，break 掉整个收包循环 ⇒ 用户表现为「敲键盘偶尔掉线」。
     判据：_WRITE_RETRY_ERRNOS 含 EAGAIN/EINTR，但不含 EIO/EBADF。

跑法：cd ~/agent-hub-wt-01a0ec28 && venv/bin/python -m unittest tests.test_term_reap_and_hb -v
"""
import errno
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("TERM_TOKEN", "unit-test-token-not-production")

import term  # noqa: E402


class _FakeSess:
    """只带 _touch 真正读到的字段。last_io 必须是真数字，别的时间基准由用例自己控。"""

    def __init__(self, last_io: float = 1000.0):
        self.id = "sess-hb"
        self.last_io = last_io


class TestHeartbeatDoesNotExtendTTL(unittest.TestCase):
    """P0-1：链路保活 ≠ 用户还在用。"""

    def test_hb_frame_does_not_touch_last_io(self):
        s = _FakeSess(1000.0)
        term._touch(s, '{"type":"hb"}')
        self.assertEqual(s.last_io, 1000.0, "心跳帧续了命 ⇒ 45min TTL 形同虚设")

    def test_ping_pong_ack_also_ignored(self):
        for t in ("ping", "pong", "ack"):
            s = _FakeSess(1000.0)
            term._touch(s, '{"type":"%s"}' % t)
            self.assertEqual(s.last_io, 1000.0, f"{t} 帧续了命")

    def test_resize_touches(self):
        """拖窗口是真交互 —— 必须续命，否则「开着 vim 读代码」会被 TTL 杀掉。"""
        s = _FakeSess(1000.0)
        term._touch(s, '{"type":"resize","rows":40,"cols":120}')
        self.assertGreater(s.last_io, 1000.0)

    def test_keystroke_text_touches(self):
        s = _FakeSess(1000.0)
        term._touch(s, "hello")
        self.assertGreater(s.last_io, 1000.0)

    def test_binary_frame_touches(self):
        """xterm 输入走 data 通道（二进制帧）⇒ 一定是交互。"""
        s = _FakeSess(1000.0)
        term._touch_bytes(s)
        self.assertGreater(s.last_io, 1000.0)

    def test_malformed_json_still_touches(self):
        """前端版本错配时格式怪，不该因此把用户的会话判成空闲。"""
        s = _FakeSess(1000.0)
        term._touch(s, '{"type":"input","data":"esc')
        self.assertGreater(s.last_io, 1000.0)

    def test_blank_frame_noop(self):
        """纯空白帧不是交互（可能是探针/keepalive 的空包）。"""
        s = _FakeSess(1000.0)
        term._touch(s, "   ")
        self.assertEqual(s.last_io, 1000.0, "空白帧续了命")

    def test_unknown_json_frame_conservatively_touches(self):
        """未知帧型**保守续命**（fail-safe），不因客户端升级加了新帧就把会话判空闲。

        前端实测只发两种 JSON 帧：{"type":"resize"} 与 {"type":"hb"}
        （static/hub/03-agents-cards.js:14 / :61）。白名单之外的帧一律当交互，
        宁可 TTL 晚触发，也不要在新版客户端上误杀正在用的终端。
        """
        s = _FakeSess(1000.0)
        term._touch(s, "{}")
        self.assertGreater(s.last_io, 1000.0)
        s2 = _FakeSess(1000.0)
        term._touch(s2, '{"type":"未来才有的新帧型","payload":1}')
        self.assertGreater(s2.last_io, 1000.0)


class TestWriteErrnoClassification(unittest.TestCase):
    """P0-3：写背压要重试，pty 真死才收口。"""

    def test_retry_set_contains_egain_eintr(self):
        self.assertIn(errno.EAGAIN, term._WRITE_RETRY_ERRNOS)
        self.assertIn(errno.EINTR, term._WRITE_RETRY_ERRNOS)

    def test_retry_set_excludes_terminal_errnos(self):
        """EIO/EBADF 是「pty 对端已关」，必须断开而不是重试。"""
        for e in (errno.EIO, errno.EBADF, errno.EPIPE):
            self.assertNotIn(e, term._WRITE_RETRY_ERRNOS,
                             f"errno {e} 被当成可重试 ⇒ 真死连接会被无限重试")


class TestReapReclaimsExitedProcess(unittest.TestCase):
    """P0-2：子进程自己退了，会话必须被摘掉，不能白占 MAX_SESSIONS 名额。"""

    def setUp(self):
        term._sessions.clear()
        self.addCleanup(term._sessions.clear)

    def _spawn_quick(self, seconds=0.0):
        """真 pty.fork 出一个短命子进程（不 mock waitpid）。"""
        sess = term.Session("t-quick", "test", ["sleep", str(seconds)], os.getcwd())
        return sess

    def test_exited_child_is_dropped_from_registry(self):
        sess = self._spawn_quick(0.0)
        term._sessions[sess.id] = sess
        self.assertIn(sess.id, term._sessions)
        # 等子进程真死
        for _ in range(200):
            if sess.poll_exited():
                break
            time.sleep(0.01)
        self.assertTrue(sess.poll_exited(), "waitpid 说子进程还活着")
        term._reap()
        self.assertNotIn(sess.id, term._sessions,
                         "已退出的会话仍留在登记表 ⇒ 永久占用 MAX_SESSIONS 配额")
        self.assertFalse(sess.alive)

    def test_live_child_is_kept(self):
        """活着的会话不能被误回收（这是 P0-2 修复最危险的回归方向）。"""
        sess = self._spawn_quick(30)
        term._sessions[sess.id] = sess
        term._reap()
        self.assertIn(sess.id, term._sessions, "活会话被误回收了")
        self.assertTrue(sess.alive)
        sess.kill()

    def test_idle_with_no_viewer_gets_killed(self):
        """② 无人观看 + pty 完全静默 + 超 TTL ⇒ 必须杀（配额要能释放）。

        v0.13.58 档二：判据从「客户端静默」改成「无生命迹象」，所以这条比原版多一个
        前提——last_activity 也要超 TTL。只把 last_io 推后而 pty 仍在产出时**不该**杀，
        那正是跨客户端连续性的正身（见 tests/test_term_ttl_activity.py）。
        """
        sess = self._spawn_quick(30)
        term._sessions[sess.id] = sess
        stale = time.time() - (term.IDLE_TTL_S + 60)
        sess.last_io = stale
        sess.last_activity = stale          # pty 也没产出 ⇒ 真无生命迹象
        term._reap()
        self.assertTrue(sess.hub_killed, "无观看者 + 无产出 + 超 TTL 却不杀")
        self.assertIn(sess.id, term._sessions, "刚 kill 不该立刻摘表（等 reap 确认）")

    def test_watcher_blocks_reap_even_when_client_silent(self):
        """v0.13.58 档二（推翻本文件原 test_idle_with_viewer_is_still_reaped 的口径）。

        原断言是「开着不动 45 分钟也该收」，理由是怕占满 MAX_SESSIONS 名额。
        但用户 09-29 明确要求「任务状态跨客户端连续」，而实弹证据显示旧口径会在
        agent 仍在产出时把会话杀掉（换端重连直接 4410）⇒ 腰斩正在跑的任务。
        现在的取舍：**有人在看 ⇒ 绝不因静默被杀**；防配额占死改由另外三道闸承担 ——
        进程退出即摘表（test_exited_child_is_dropped_from_registry）、
        MAX_SESSIONS 满则拒开新会话、以及无人观看时的 TTL 回收（本类另一条）。
        代价（知情接受）：一个开着不动也不退出的终端会一直占名额，只能由用户点 × 结束。
        """
        sess = self._spawn_quick(30)
        term._sessions[sess.id] = sess
        sess.viewers["v1"] = None            # 有观看者（队列内容无关）
        stale = time.time() - (term.IDLE_TTL_S + 60)
        sess.last_io = stale
        sess.last_activity = stale           # 就算 pty 也静默，只要有人在看就不杀
        term._reap()
        self.assertFalse(sess.hub_killed,
                         "有人看着却被回收 ⇒ 用户在眼前的任务被静默腰斩（档二语义）")
        self.assertTrue(sess.alive)
        sess.kill()

    def test_alive_count_matches_registry(self):
        """alive_count 与登记表口径一致（P0-2 修完后别再出现两套数）。"""
        sess = self._spawn_quick(30)
        term._sessions[sess.id] = sess
        self.assertEqual(term.alive_count(), 1)
        sess.kill()
        term._reap()
        for _ in range(200):
            if sess.poll_exited():
                break
            time.sleep(0.01)
        term._reap()
        self.assertEqual(term.alive_count(), 0)


class TestDyingRegistryReclaimed(unittest.TestCase):
    """显式 kill 后的会话，资源必须有人兜底收（不依赖 call_later 一定跑得起来）。"""

    def test_reap_dying_cleans_up(self):
        term._sessions.clear()
        term._dying.clear()
        self.addCleanup(term._dying.clear)
        sess = term.Session("t-dying", "test", ["sleep", "0"], os.getcwd())
        sess.kill()
        term._dying[id(sess)] = sess
        for _ in range(300):
            term._reap_dying()
            if id(sess) not in term._dying:
                break
            time.sleep(0.01)
        self.assertNotIn(id(sess), term._dying, "已杀会话永远留在 _dying ⇒ fd 泄漏")
        self.assertFalse(sess.alive)


if __name__ == "__main__":
    unittest.main(verbosity=2)
