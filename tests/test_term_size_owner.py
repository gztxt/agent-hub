"""回归测：终端尺寸所有权（B2 / paseo 融合）——后台那一端不许偷走 PTY 尺寸。

借鉴出处（paseo v0.10.0 实证）：
  packages/server/src/terminal/terminal-size-ownership.ts（全文 38 行）
  docs/terminal-performance.md:31 原话：lets an owning pane follow splits and
    keyboard insets without allowing an idle phone or browser to steal the PTY size.

要防的真问题：桌面正开着 vim（120×40），手机端同一会话的页面在后台被 ResizeObserver
或 visibilitychange 唤醒发来 80×24 ⇒ 桌面 vim 被压扁。用户看到的是
「我什么都没做，终端自己乱了」，且极难归因。

本测钉死五件事：
  ① 非所有者的 update 被静默忽略（尺寸不变）；
  ② claim 无条件夺权，且**同尺寸也要转移所有权**（paseo 测试同名用例）；
  ③ 老客户端没有 intent 字段 ⇒ 缺省按 claim（否则前后端版本错配会让尺寸永远改不动）；
  ④ 尺寸未变不下 ioctl（等价于 paseo 的服务端短路）；
  ⑤ 非法/越界行列只忽略、绝不抛 —— 高频几何事件炸掉 WS 收包循环
     ⇒ 「拖一下窗口」变成「终端断开」。

用 openpty 拿真 fd（不 fork 进程）：ioctl 走的是真实路径，不是 mock 出来的假绿。
跑法：cd ~/agent-hub && venv/bin/python -m unittest tests.test_term_size_owner -v
"""
import os
import pty
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("TERM_TOKEN", "unit-test-token-not-production")

import term  # noqa: E402


class _FakeSess:
    """只要 _apply_size 真正用到的那几个字段；fd 用真 pty slave。"""

    def __init__(self, fd):
        self.id = "sess-test"
        self.rows = 24
        self.cols = 80
        self.fd = fd


class _SizeOwnerTest(unittest.TestCase):
    def setUp(self):
        self.mfd, self.sfd = pty.openpty()
        self.addCleanup(self._close)
        self.sess = _FakeSess(self.sfd)
        term._size_owner.pop(self.sess.id, None)   # 用例之间不串味

    def _close(self):
        for fd in (self.mfd, self.sfd):
            try:
                os.close(fd)
            except OSError:
                pass

    def _apply(self, vid, rows, cols, intent):
        return term._apply_size(self.sess, vid, rows, cols, intent)


class TestUpdate(_SizeOwnerTest):
    def test_non_owner_update_is_ignored(self):
        """这就是要防的那件事：后台端发尺寸，PTY 尺寸纹丝不动。"""
        self.assertTrue(self._apply("desk", 40, 120, "claim"))
        self.assertEqual((self.sess.rows, self.sess.cols), (40, 120))
        self.assertFalse(self._apply("phone", 24, 80, "update"), "非所有者的 update 不该生效")
        self.assertEqual((self.sess.rows, self.sess.cols), (40, 120), "桌面尺寸被偷走了")

    def test_owner_update_works(self):
        self._apply("desk", 40, 120, "claim")
        self.assertTrue(self._apply("desk", 50, 132, "update"), "所有者的 update 应该生效")
        self.assertEqual((self.sess.rows, self.sess.cols), (50, 132))

    def test_update_without_any_owner_is_ignored(self):
        """没人 claim 过 ⇒ update 一律无效（避免任何闲杂连接改尺寸）。"""
        self.assertFalse(self._apply("whoever", 30, 100, "update"))
        self.assertEqual((self.sess.rows, self.sess.cols), (24, 80))


class TestClaim(_SizeOwnerTest):
    def test_claim_takes_over_even_at_same_size(self):
        """paseo 明确的反直觉细节：claim 即使尺寸相同也要转移所有权
        （terminal-size-ownership.test.ts:48-59）。少这一条，端切换后尺寸就再也改不动。"""
        self._apply("desk", 40, 120, "claim")
        self.assertFalse(self._apply("phone", 40, 120, "claim"), "尺寸相同 ⇒ 不下 ioctl")
        self.assertEqual(term._size_owner[self.sess.id], "phone", "同尺寸 claim 也必须转移所有权")
        self.assertTrue(self._apply("phone", 30, 90, "update"), "夺权后 update 应当生效")

    def test_claim_from_other_client_steals_ownership(self):
        self._apply("desk", 40, 120, "claim")
        self._apply("phone", 30, 90, "claim")
        self.assertFalse(self._apply("desk", 40, 120, "update"), "旧所有者已失去所有权")


class TestCompatAndRobustness(_SizeOwnerTest):
    def test_missing_intent_defaults_to_claim(self):
        """老客户端（无 intent 字段）必须照旧工作 ⇒ 缺省 claim。"""
        self.assertEqual(term._norm_intent(None), "claim")
        self.assertEqual(term._norm_intent(""), "claim")
        self.assertTrue(self._apply("legacy", 40, 120, term._norm_intent(None)))
        self.assertEqual(term._size_owner[self.sess.id], "legacy")

    def test_unknown_intent_is_conservative(self):
        """看不懂的意图按 update 处理（要所有权），绝不能旁路所有权检查。"""
        for raw in ("CLAIMX", "steal", "0", 123):
            with self.subTest(raw=raw):
                self.assertEqual(term._norm_intent(raw), "update")
        self._apply("desk", 40, 120, "claim")
        self.assertFalse(self._apply("phone", 24, 80, term._norm_intent("nonsense")),
                         "未知意图竟然能改尺寸 ⇒ 所有权检查被旁路")
        self.assertEqual((self.sess.rows, self.sess.cols), (40, 120))

    def test_same_size_is_a_noop(self):
        self.assertFalse(self._apply("a", 24, 80, "claim"), "尺寸未变不该打扰 pty")
        self.assertEqual(term._size_owner[self.sess.id], "a")

    def test_unparsable_values_are_ignored_not_raised(self):
        """解析不了的只忽略：高频几何事件炸掉 WS 收包循环
        ⇒ 「拖一下窗口」变成「终端断开」，比不改尺寸糟得多。"""
        for bad in (None, "abc", "", [], {}):
            with self.subTest(bad=bad):
                self.assertFalse(self._apply("a", bad, 100, "claim"), f"{bad!r} 竟然被接受")
                self.assertFalse(self._apply("a", 30, bad, "claim"), f"{bad!r} 竟然被接受")
        self.assertEqual((self.sess.rows, self.sess.cols), (24, 80), "脏输入把尺寸改了")

    def test_out_of_range_is_clamped_not_rejected(self):
        """越界值收敛到合法区间（而不是拒绝）：pty 尺寸是 struct.pack("HHHH")，
        直接把 99999 交给它会抛 struct.error 打断收包循环。"""
        self.assertTrue(self._apply("a", 100000, 100000, "claim"))
        self.assertLessEqual(self.sess.rows, term._ROWS_RANGE[1])
        self.assertLessEqual(self.sess.cols, term._COLS_RANGE[1])
        self._apply("a", -5, 0, "claim")
        self.assertGreaterEqual(self.sess.rows, term._ROWS_RANGE[0])
        self.assertGreaterEqual(self.sess.cols, term._COLS_RANGE[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
