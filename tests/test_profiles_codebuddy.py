"""L0：Agents 菜单里的 CodeBuddy Code 卡（`codebuddy --serve` 原生遥控界面 :35431）。

为什么钉这三条（2026-09-27 用户要求「35431 端口的 codebuddy 也要加进 agents，用原生界面」）：
1. **入口必须是原生界面**：.codebuddy 的 `--serve` 自带 Web 遥控端，卡片因此按服务型 Agent
   画像走（`ui` 字符串 ⇒ 「嵌入会话 / 新窗口」），而不是 hub 的 pty 终端。
   一旦有人顺手给它加 `terminal`，就会顶掉 embed 的默认形态（工作台形态优先级
   `embed > term > chat`，见 test_entity_mode_source_of_truth），用户要的原生界面就没了。
2. **卡片能否出现取决于形态判定**：服务型（`无 cli/terminal` + `有 port`）才能被 vitals
   判成 web-service，以「自有端口应答」为存在证据；写成 CLI 型会因 `which codebuddy`
   落空（该二进制只在 WorkBuddy 包内，不在 PATH 上）而被判 not_installed ⇒ 卡片出不来。
3. **loopback 地址必须原样留给前端**：`01-core-boot.js:lanUrl()` 负责把 127.0.0.1 换成
   当前访问主机名；画像里若直接写死 LAN IP，手机/局域网/Tailscale 三个 origin 就互相失效。

本文件属 L0 hermetic：只读画像表与判定函数，**零网络**（不探测 35431 是否真活着）。
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import profiles  # noqa: E402

PORT = 35431


def _card() -> dict:
    for p in profiles.all_profiles(include_blocked=True):
        if p.get("id") == "codebuddy":
            return p
    raise AssertionError("profiles 里没有 codebuddy 卡")


class TestCodebuddyNativeCard(unittest.TestCase):
    """① 卡片存在且是服务型 Agent（原生界面形态）"""

    def test_card_is_service_shaped(self):
        c = _card()
        self.assertEqual(c["kind"], "agent")
        self.assertEqual(c["port"], PORT)
        self.assertEqual(c["ui"], "http://127.0.0.1:%d" % PORT)
        # 无 cli / 无 terminal ⇒ vitals 按 web-service 判定（存在证据=自有端口应答）
        self.assertIsNone(c.get("cli"))
        self.assertIsNone(c.get("terminal"))

    def test_ui_is_loopback_for_frontend_rewrite(self):
        """UI 必须写 loopback，由前端按访问主机名改写（LAN IP 写死会跨网失效）"""
        c = _card()
        self.assertTrue(re.match(r"^http://127\.0\.0\.1:\d+$", c["ui"]), c["ui"])

    def test_entries_are_native_ui_only(self):
        c = _card()
        types = [e["type"] for e in profiles.entries_for(c, "running")]
        self.assertIn("embed", types)
        self.assertIn("open", types)
        self.assertNotIn("term", types)      # 原生界面优先，别被终端入口顶掉
        self.assertNotIn("chat", types)
        self.assertFalse(c.get("frame_deny"))  # :35431 无 X-Frame-Options，可安全嵌入

    def test_detect_matches_serve_cmdline(self):
        """`node /opt/WorkBuddy/.../codebuddy --serve --port 35431 --host 0.0.0.0`"""
        procs = [{"comm": "node",
                  "cmdline": ("node /opt/WorkBuddy/resources/app.asar.unpacked/"
                              "cli/bin/codebuddy --serve --port 35431 --host 0.0.0.0")}]
        self.assertEqual(profiles.detect_status(_card(), procs, set(), {}), "running")


if __name__ == "__main__":
    unittest.main()
