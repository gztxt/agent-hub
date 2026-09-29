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
        """② TTL 只在「没有观看者」时判：有观看者说明有人在用，不动。"""
        sess = self._spawn_quick(30)
        term._sessions[sess.id] = sess
        sess.last_io = time.time() - (term.IDLE_TTL_S + 60)
        term._reap()
        self.assertTrue(sess.hub_killed, "无观看者且超 TTL 却不杀")
        self.assertIn(sess.id, term._sessions, "刚 kill 不该立刻摘表（等 reap 确认）")

    def test_idle_with_viewer_is_still_reaped(self):
        """TTL 的语义是**空闲**而非「离线」：窗口开着 45 分钟没敲键盘就该收。

        这条不是我的设计而是既有 tests/test_term_reaper.py::test_idle_expired_gets_killed
        钉死的口径（它用不具 viewers 字段的轻量 stub，断言只看 last_io）。
        保留这条断言是为了防止未来有人「顺手优化」成有观看者就不杀 ——
        那会让一个开着不动的终端永远占着 MAX_SESSIONS 的名额（P0-2 同型故障）。
        """
        sess = self._spawn_quick(30)
        term._sessions[sess.id] = sess
        sess.viewers["v1"] = None            # 有观看者（队列内容无关）
        sess.last_io = time.time() - (term.IDLE_TTL_S + 60)
        term._reap()
        self.assertTrue(sess.hub_killed, "超 TTL 却不收 ⇒ 开着不动的终端永久占配额")
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
