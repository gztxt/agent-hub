"""子进程零输出即死时的感知延迟（PT-20260929-02 补做，2026-10-08）。

## 为什么这个文件存在（背景）

台账 `PT-20260929-02` 记「✅ 闭合」，修复提交 `055e1b7` 写在未合并的
`wt/01a0ed3a` 分支上。2026-10-08 处置worktree 时逐条取证，发现：

- master 的 `src/term.py` **没有** `_child_reap_loop()`、没有 `_REAP_CHILD_POLL_S`；
- `os._exit(127)` 吞死因的代码也还在（`__init__` 里的 exec 失败分支）。
⇒ **台账 closed，而修复从未进 master。**

## 本文件测的是什么（缺口的确切形状）

机制其实**大部分已在**，不是「没有探活」：

- `Session.poll_exited()` 用 `waitpid(WNOHANG)` 独立探活，不依赖有没有观看者；
- `_reap()` 遍历**全部** `_sessions`（不只是 `_dying`），对每个调 `poll_exited()`；
- `reap_loop()` 是startup 里`_spawn` 的独立后台任务（`src/main.py`）。

真正的缺口只有一个：**`REAP_INTERVAL_S = 60`**。
子进程零输出即死时（PTY master 只产生 POLLHUP，`add_reader` 只监 EPOLLIN
不触发回调 ⇒ `on_readable` 不跑 ⇒ 没人置 `alive=False`），
要等**最长 60 秒**后`_reap()` 那一轮才发现。这 60 秒内：

- `pump()` 的 `timeout=2.0` 分支只判 `if not sess.alive: break`，而 `alive`
  只由 `_cleanup()` 置 ⇒ 也不break ⇒ 一直等；
- 前端看到的���是**永久空白**（ring 空、无 `[process exited]` 提示），
  且 `GET /api/term/sessions` 里该 sid 的 `exit_reason` 为空。

这正是 09-30 报障的现象。所以修法不是重写 reap 机制，而是给「零输出即死」
这条**唯一无事件可监听的路径**加一条 2s 快速通道。

## 判据为什么这样写（不测「我看了截图」）

时间类判据必须**断言量**而非「等一下看着对」。这里用两个可断言的量：

1. `REAP_INTERVAL_S > 快速通道间隔` —— 结构判据，证明快速通道不是摆设；
2. 快速通道**真的**在≤ 阈值内把 alive 置 False 并回填 exit_status
   —— 行为判据，用真实 `pty.fork` 起一个「零输出即死」的子进程
   （`sh -c 'exit 7'`，无任何输出）。

**红向自证**：`_fast_reap_enabled` 的实现若改成恒返回 False，
本文件必须转红（否则就是「测试替被测方实现了一遍」= 假绿，
判例见 `agent-knowledge/` 里「假绿是怎么造出来的」）。
"""
import os
import sys
import time
import unittest
from pathlib import Path

# 与tests/test_term_reap_and_hb.py 等同仓测试同构：src/ 入path 后裸 import。
# （不写 `from src import term` —— src/term.py 内部是 `import db` 这类平铺导入，
#    按包导入会得到 "No module named 'db'"。）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import term  # noqa: E402


# 快速通道必须比常规 reap 快一个数量级，否则等于没修。
_FAST_RATIO = 10.0
# 行为判据的阈值：2s 通道 + 调度余量。放宽到 6s 以免 CI 抖动误报，
# 但仍远小于常规 60s —— 若退回常规 reap，本用例会红。
_BEHAVIOR_DEADLINE_S = 6.0


class TestFastReapInterval(unittest.TestCase):
    """结构判据：快速通道存在且显著快于常规 reap。"""

    def test_fast_channel_is_faster_than_normal_reap(self):
        self.assertTrue(
            hasattr(term, "_FAST_REAP_INTERVAL_S"),
            "term 模块缺 _FAST_REAP_INTERVAL_S —— 零输出即死的 2s 快速通道未落地",
        )
        fast = term._FAST_REAP_INTERVAL_S
        normal = term.REAP_INTERVAL_S
        self.assertGreater(
            normal, fast,
            "常规 reap 间隔(%s) 必须大于快速通道(%s)，否则快速通道没有意义"
            % (normal, fast),
        )
        self.assertGreaterEqual(
            normal / float(fast), _FAST_RATIO,
            "快速通道只比常规快 %.1f 倍（%s → %s），达不到一个数量级"
            % (normal / float(fast), normal, fast),
        )

    def test_fast_channel_can_be_disabled(self):
        """关掉快速通道必须退回常规语义（不改变既有行为，只是变慢）。"""
        # 用独立 env 读一遍，不改模块全局（避免污染其它用例）。
        old = os.environ.get("TERM_FAST_REAP_INTERVAL")
        try:
            os.environ["TERM_FAST_REAP_INTERVAL"] = "0"
            import importlib
            reloaded = importlib.reload(term)
            self.assertEqual(
                reloaded._FAST_REAP_INTERVAL_S, 0.0,
                "TERM_FAST_REAP_INTERVAL=0 必须把快速通道设为 0（=关闭）",
            )
            self.assertFalse(
                reloaded._fast_reap_enabled(),
                "间隔为 0 时 _fast_reap_enabled() 必须为 False",
            )
        finally:
            if old is None:
                os.environ.pop("TERM_FAST_REAP_INTERVAL", None)
            else:
                os.environ["TERM_FAST_REAP_INTERVAL"] = old
            importlib.reload(term)


class TestFastReapCatchesSilentExit(unittest.TestCase):
    """行为判据：真起一个零输出即死的子进程，验证快速通道及时收尸。"""

    def setUp(self):
        term._sessions.clear()
        term._dying.clear()
        self.addCleanup(term._sessions.clear)
        self.addCleanup(term._dying.clear)

    def test_silent_exit_is_detected_within_deadline(self):
        """`sh -c 'exit 7'`：零输出、立刻死。必须由快速通道发现并回填 exit_status。

        为什么这个子进程能代表报障现场：它连一个字节都不写就退出 ⇒ PTY master
        只置 POLLHUP 而不置 EPOLLIN ⇒ asyncio `add_reader` 回调不触发 ⇒
        `on_readable` 永不执行 ⇒ 没有事件可监听。这与 09-30 实测到的
        hermes「秒死、on_readable 一次不进」是同一类。
        """
        sess = term.Session("t-silent", "test", ["sh", "-c", "exit 7"], os.getcwd())
        term._sessions[sess.id] = sess
        self.addCleanup(sess._cleanup)

        # 先确认真���的「不作为」：不主动调任何 reap，光靠等。
        # 若产品本就能立即感知，下面这条断言会因t_s 立刻变死而仍成立——
        # 所以正戏是下面那条「通道开着时多久死」。
        self.assertTrue(sess.alive, "刚建会话应是活的")

        deadline = time.time() + _BEHAVIOR_DEADLINE_S
        # 模拟后台快速通道每`_FAST_REAP_INTERVAL_S` 醒一次的节律。
        # ��真实启动 asyncio 循环无关：`_reap_fast()` 是同步函数，
        # 这样测不依赖 loop，且避免「测自己实现的循环」式假绿。
        interval = getattr(term, "_FAST_REAP_INTERVAL_S", None)
        self.assertIsNotNone(
            interval, "缺 _FAST_REAP_INTERVAL_S，快速通道未落地（红向：见文件头）"
        )
        if interval <= 0:
            self.skipTest("TERM_FAST_REAP_INTERVAL=0，快速通道被显式关闭")

        while time.time() < deadline:
            if not sess.alive:
                break
            time.sleep(min(0.05, max(interval / 4.0, 0.01)))
            term._reap_fast()

        self.assertFalse(
            sess.alive,
            "零输出即死的子进程在 %.1fs 内仍未被感知 —— 快速通道未生效"
            % _BEHAVIOR_DEADLINE_S,
        )
        self.assertIsNotNone(
            sess.exit_status,
            "感知到了死亡却没回填 exit_status ⇒ 前端拿不到死因，仍是哑谜",
        )
        # exit 7 ⇒ waitpid 状态应为 7<<8。断言死因可读，而不只是「非空」。
        self.assertEqual(
            os.WEXITSTATUS(sess.exit_status), 7,
            "exit_status 应能解出退出码 7，实际 %r" % (sess.exit_status,),
        )
        # 必须**已从登记表摘掉** —— 否则 alive=False 但仍占MAX_SESSIONS(8) 的名额，
        # 用户表现是「开过几个终端之后再也开不出新的」（P0-2 同款症状）。
        self.assertEqual(
            term.alive_count(), 0,
            "会话已被感知为死，但仍在 _sessions 里占名额（alive_count 应为 0）",
        )
        self.assertNotIn(
            sess.id, term._sessions,
            "_drop() 应把会话从登记表摘除，否则死会话会占死配额",
        )


if __name__ == "__main__":
    unittest.main()
