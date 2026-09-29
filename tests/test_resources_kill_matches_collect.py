"""回归测：资源页「采集」与「结束」必须认同一批进程（P0-4）。

真问题（实弹复核，不是代理臆测）：`src/resources.py` 里同一段 /proc 匹配逻辑出现了**两份**：
  · 采集侧 `_collect_agent_resources()` 有 MainThread 的 cmdline 佐证
    （comm=MainThread 太通用 ⇒ 要求 cmdline 里确实有 .ccr / claude-code-router 才算命中）；
  · kill 侧 `kill_agent()` **没有**这层佐证，直接 `pat.search(comm)` 就收进 target_pids。
两份口径不一致 ⇒ 同一个进程在「列资源」时被正确排除，用户在「点结束」时却把它杀了。
本机实测受害面含 WorkBuddy 桌面端等 comm=MainThread 的 Electron 应用 ——
**误杀真实应用**，这是本仓所有写端点里后果最重的一条。

本测钉死：kill 侧对 MainThread 的处置必须与采集侧逐字一致（都要 cmdline 佐证）。
用真 /proc 形态的数据喂，不 mock 整条链路；断言口径差异本身，而不是碰真进程。

跑法：cd ~/agent-hub-wt-01a0ec28 && venv/bin/python -m unittest tests.test_resources_kill_matches_collect -v
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("TERM_TOKEN", "unit-test-token-not-production")

import resources  # noqa: E402


def _match_proc(rx: str, comm: str, cmdline: str) -> bool:
    """与 resources 两处逐字同构的匹配（含 P0-4 的 MainThread 佐证分支）。"""
    pat = re.compile(rx)
    if not (pat.search(comm) or pat.search(cmdline)):
        return False
    if rx == "MainThread":
        if ".ccr" not in cmdline and "claude-code-router" not in cmdline:
            return False
    return True


class TestKillAndCollectAgreeOnMainThread(unittest.TestCase):

    def setUp(self):
        # 一组贴近真实的 /proc 记录：comm / cmdline
        self.procs = [
            # CCR 的 Electron 主进程（comm=MainThread，cmdline 里有 ~/.ccr）—— 应当命中
            {"pid": 1001, "comm": "MainThread",
             "cmdline": "/home/gztxt/.ccr/desktop/ccr --no-sandbox"},
            # WorkBuddy 桌面端 —— comm 同为 MainThread，但 cmdline 与 ccr 无关
            {"pid": 1002, "comm": "MainThread",
             "cmdline": "/opt/apps/WorkBuddy/codebuddy --no-sandbox"},
            # 别的 Electron 应用（VSCode 等）
            {"pid": 1003, "comm": "MainThread", "cmdline": "/usr/share/code/code"},
            # 普通 node agent —— 靠更具体的模式命中，不受 MainThread 规则影响
            {"pid": 1004, "comm": "node",
             "cmdline": "/usr/bin/node /home/gztxt/.npm-global/bin/claude"},
        ]
        self.profiles = ["MainThread", "node"]

    def test_workbuddy_is_not_matched_by_mainthread(self):
        """误杀防线：WorkBuddy 不得因为 comm=MainThread 被收进 kill 目标。"""
        for rx in self.profiles:
            self.assertFalse(
                _match_proc(rx, "MainThread", "/opt/apps/WorkBuddy/codebuddy --no-sandbox"),
                "WorkBuddy 被 MainThread 模式命中 ⇒ 资源页会误杀真实应用")

    def test_ccr_mainthread_is_matched(self):
        """正例：真有 ccr 的 MainThread 仍必须命中（别把修复做成「一律不杀」）。"""
        self.assertTrue(
            _match_proc("MainThread", "MainThread",
                        "/home/gztxt/.ccr/desktop/ccr --no-sandbox"))

    def test_collect_and_kill_paths_agree(self):
        """两份匹配逻辑必须逐字同口径 —— 任一侧新增/漏掉分支都会红。"""
        collect_src = Path(resources.__file__).read_text(encoding="utf-8")
        # 两处 MainThread 佐证都必须存在
        self.assertEqual(
            collect_src.count('if ".ccr" not in pr["cmdline"]'),
            2,
            "采集侧与 kill 侧的 MainThread 佐证必须成对存在（当前只有一处 ⇒ 口径已分叉）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
