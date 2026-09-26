"""L0：`term.child_env()` 必须把 nvm node bin 前置进子进程 PATH。

钉这条的原因（2026-09-27 实测，不是假想）：用户选 pi 起会话 ⇒ 终端一屏 JS 堆栈，
真因是 `pi` 的 shebang 为 `#!/usr/bin/env node`，而 hub 服务进程 PATH 无 nvm ⇒
内核把系统 node **v20.20.2** 交给 pi v0.85.1，后者 bundle 用了 `node:fs` 的
`globSync`（Node 22+）⇒ `SyntaxError: The requested module 'node:fs' does not
provide an export named 'globSync'`。

`profiles.which()` 的 nvm 兜底只保证 hub 找得到 pi 这个**文件**，管不到 pi 起来
之后自己再找**解释器**——所以必须在子进程环境层面补，两件事缺一不可。

本文件属 L0 hermetic：不真 fork pty、不起服务、零网络；用假 nvm 目录断言 PATH 顺序。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import profiles                                       # noqa: E402
import term                                           # noqa: E402


class TestChildEnvPrependsNvm(unittest.TestCase):
    def setUp(self):
        self.fake = ["/home/someone/.nvm/versions/node/v24.18.0/bin",
                     "/home/someone/.nvm/versions/node/v22.9.0/bin"]
        self.patcher = mock.patch.object(profiles, "_nvm_bins",
                                         return_value=[Path(p) for p in self.fake])
        self.patcher.start()
        self._saved_path = os.environ["PATH"]
        os.environ["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    def tearDown(self):
        self.patcher.stop()
        os.environ["PATH"] = self._saved_path

    def test_nvm_dirs_come_first(self):
        env = term.child_env()
        self.assertEqual(env["PATH"].split(os.pathsep)[:2], self.fake)
        # 系统目录仍在，只是排在后面 —— 不是替换 PATH
        self.assertIn("/usr/bin", env["PATH"])

    def test_newer_node_wins(self):
        self.assertEqual(term.child_env()["PATH"].split(os.pathsep)[0], self.fake[0])

    def test_term_and_colorterm(self):
        env = term.child_env()
        self.assertEqual(env["TERM"], "xterm-256color")
        self.assertEqual(env["COLORTERM"], "truecolor")

    def test_no_nvm_means_untouched_path(self):
        """没有 nvm 的机器上不得凭空造目录（PATH 里不能出现空串段）"""
        self.patcher.stop()
        try:
            with mock.patch.object(profiles, "_nvm_bins", return_value=[]):
                env = term.child_env()
            self.assertEqual(env["PATH"], os.environ["PATH"])
            self.assertEqual(env["PATH"].split(os.pathsep)[0], "/usr/local/sbin")
        finally:
            self.patcher.start()

    def test_base_env_not_mutated(self):
        term.child_env()
        self.assertEqual(os.environ["PATH"],
                         "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")


if __name__ == "__main__":
    unittest.main()
