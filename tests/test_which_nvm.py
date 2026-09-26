"""L0：`profiles.which()` 必须在 **systemd 服务进程的 PATH** 下也解析得到 npm 全局 CLI。

为什么钉这条（2026-09-27 实测，不是假想）：hub 跑在 systemd 用户单元里，PATH 是
`/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`，**没有 nvm**。
`pi`（v0.85.1）只装在 `~/.nvm/versions/node/v24.18.0/bin`，于是生产进程里
`which('pi')` 落空 ⇒ vitals 判 not_installed ⇒ 整张卡被拦 ⇒ 两项目页候选框里没有它。
而开发者的交互 shell 里有 nvm，`which pi` 一路绿灯——**同一个函数两种结论**，
只看 shell 会永远查不出这个坑。

本文件属 L0 hermetic：不 import `src.main`、不起服务、零网络；只临时改写自身进程的 PATH。
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import profiles  # noqa: E402

# hub.service 的真实 PATH（`/proc/<pid>/environ` 实测），不是猜的
SYSTEMD_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class TestWhichUnderServicePath(unittest.TestCase):
    @unittest.skipUnless((Path.home() / ".nvm").is_dir(), "本机无 ~/.nvm（假 HOME 环境跳过）")
    def setUp(self):
        self._saved = os.environ["PATH"]
        os.environ["PATH"] = SYSTEMD_PATH

    def tearDown(self):
        os.environ["PATH"] = self._saved

    def test_nvm_global_cli_still_resolves(self):
        got = profiles.which("pi")
        self.assertIsNotNone(got, "服务 PATH 下 which('pi') 落空 ⇒ 卡片会被 vitals 拦掉")
        self.assertTrue(got.startswith(str(Path.home() / ".nvm")), got)

    def test_absolute_path_always_resolves(self):
        """codebuddy 的终端 cmd 是绝对路径——不依赖 PATH，服务环境下也必须解析得通"""
        self.assertEqual(profiles.which(profiles.CODEBUDDY_CLI), profiles.CODEBUDDY_CLI)

    def test_nvm_bins_are_declared(self):
        self.assertTrue(any(p.name == "bin" and ".nvm" in str(p)
                            for p in profiles._nvm_bins()), profiles._nvm_bins())


if __name__ == "__main__":
    unittest.main()
