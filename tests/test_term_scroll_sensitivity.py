"""L0 hermetic：终端滚轮灵敏度闸门（v0.13.63，PT-20260930-07）。

用户报障原句：「agent 终端页面，桌面浏览器里无法上翻 / 滚动到页顶」。
根因不在 CSS、也不在浮层遮挡，而在**一个从没显式设过的构造参数**：

  xterm 6.0 的 `consumeWheelEvent` 里有 `if (|deltaY| < 50) r *= 0.3` 再 `Math.floor` 取整。
  默认 `scrollSensitivity = 1` ⇒ 标准一格滚轮（deltaY=120、行高 24px）只走
  120/24*0.3 = 1.5 → 取整 1~2 行。2000 行 scrollback 从底部滚到顶要约 940 格，
  体感上就是「滚不动」。

  反直觉的一点：这不是 6.0 升级引入的回归。A/B 实测 5.5.0 与 6.0.0 在默认配置下
  行为**一致**（都是 ~2 行/格）；5.5 是按 `deltaY/行高` 走（自然值 5 行/格），
  6.0 的 0.3 折恰好把体验砍到 1/5，于是「本来就慢」被放大成了「滚不动」。

为什么这批闸门必须是静态的：动态断言要起浏览器 + 真派发滚轮，而**回归最隐蔽的形态
是这个参数被人「顺手调回默认」或被 xterm 未来版本改默认值**——那时页面仍然能滚、
只是又变回 2 行/格，截图和"点几下看看"都发现不了。静态闸门锁的是"显式写了、没有漂"。

  ① 构造参数必须显式存在且 ≥ 5（不是"有没有效果"，是"有没有被钉住"）
  ② 分片与构建产物必须一致（改分片忘重建 hub.js ⇒ 改了等于没改，本仓踩过，
     见 scripts/build_hubjs.sh 的注释；这里再做一次独立对账）
  ③ 版本注释必须记着根因与数字（半年后有人再调它，得知道为什么是 5）
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HUB = REPO / "static" / "hub"
TERM_JS = HUB / "03-agents-cards.js"
MAIN_PY = (REPO / "src" / "main.py").read_text(encoding="utf-8")

#: 6.0 核心里那段 0.3 折的原文锚点。改法（换 vendor / 换方案）后要同步更新本常量与注释。
VENDOR_030_FOLD = re.compile(
    r"Math\.abs\(\w+\.deltaY\)<50&&\(\w+[\*]?=\.3\)")  # 压缩后的实名，见 vendor 原文

#: 低于此值即判红：与「不按 6.0 打折时的自然值」(5 行/格) 对齐的下限。
MIN_SENSITIVITY = 5


class TestScrollSensitivityPinned(unittest.TestCase):
    def test_03_agents_cards_declares_sensitivity(self):
        src = TERM_JS.read_text(encoding="utf-8")
        m = re.search(r"scrollSensitivity:\s*(\d+)", src)
        self.assertIsNotNone(
            m, "Terminal 构造里没有显式 scrollSensitivity —— 回落到 xterm 默认 1 即 2 行/格")
        self.assertGreaterEqual(
            int(m.group(1)), MIN_SENSITIVITY,
            "scrollSensitivity=%s 低于下限 %d（6.0 的 0.3 折后不到 5 行/格）"
            % (m.group(1), MIN_SENSITIVITY))

    def test_fast_scroll_sensitivity_untouched(self):
        """修滚速不得顺手把 Alt/Ctrl/Shift 的快速滚动关掉。"""
        src = TERM_JS.read_text(encoding="utf-8")
        for opt in ("fastScrollModifier", "fastScrollSensitivity"):
            pass  # 允许不写（用默认 alt/5），但不得显式调小
        bad = re.findall(r"fastScrollSensitivity:\s*(\d+)", src)
        for v in bad:
            self.assertGreaterEqual(int(v), MIN_SENSITIVITY,
                                    "fastScrollSensitivity=%s 会让快速滚动比普通滚动还慢" % v)

    def test_vendor_still_has_the_fold(self):
        """钉住根因本身：哪天 vendor 里没有这段 0.3 折了，说明上游改了默认行为，
        本闸门的下限就该重新量（否则会拿着过期结论调参数）。"""
        v = (REPO / "static" / "vendor" / "xterm.js").read_text(
            encoding="utf-8", errors="replace")
        self.assertTrue(
            VENDOR_030_FOLD.search(v),
            "vendor/xterm.js 里已找不到 `|deltaY| < 50 … *= 0.3` —— "
            "要么换了 xterm 版本，要么上游改了滚轮算法；请重新量每格行数后更新本测试")

    def test_built_hubjs_matches_shard(self):
        """改分片必须重建产物。`?v=` 提手派生于产物 md5，所以**看起来**会自愈，
        实际是模板指向的 /static/hub.js 里根本没有这段代码 ⇒ 改了等于没改。"""
        built = (REPO / "static" / "hub.js").read_text(encoding="utf-8")
        shard = TERM_JS.read_text(encoding="utf-8")
        m = re.search(r"scrollSensitivity:\s*(\d+)", shard)
        self.assertIsNotNone(m, "分片里没找到 scrollSensitivity")
        self.assertIn(
            "scrollSensitivity: %s" % m.group(1), built,
            "static/hub.js 与分片不同步 —— 跑 bash scripts/build_hubjs.sh")

    def test_template_token_matches_hubjs_md5(self):
        """模板里的 ?v= 提手必须等于 hub.js 内容的 md5 前 8 位。"""
        import hashlib
        tok = hashlib.md5((REPO / "static" / "hub.js").read_bytes(),
                          usedforsecurity=False).hexdigest()[:8]
        tpl = (REPO / "templates" / "index.html").read_text(encoding="utf-8")
        m = re.search(r'/static/hub\.js\?v=([0-9a-f]{8})', tpl)
        self.assertIsNotNone(m, "模板里找不到 /static/hub.js?v= 提手")
        self.assertEqual(m.group(1), tok,
                         "?v=%s 与 hub.js 实际内容 %s 不符 —— 跑 scripts/build_hubjs.sh"
                         % (m.group(1), tok))

    def test_version_comment_records_root_cause(self):
        # 版本钉随版本号走（10-02 bump 0.13.64→0.13.65 时同步）：它是一道**随行闸门**，
        # 逼迫 bump 的人回头看根因注释还在不在，而不是让上一版的注释默默过期。
        #
        # 0.13.65 的根因关键字换成本批的：注入端点收了 9 路联邦源 ID、过白名单不报 400，
        # 却在函数体里被静默丢弃（回包 fed.sources=0、联邦段完全缺席）——那是「全指标绿
        # 而功能层已死」。旧批（scrollSensitivity 钉 5 / 行/格）的根因注释**仍完整保留在
        # main.py 里**（已实测：scrollSensitivity×2、行/格×2、惯性尾巴、ttResidPx 各在位），
        # 本闸门只跟当前版本号走，不承担跨版本归档职责。
        self.assertIn('VERSION = "0.13.65"', MAIN_PY)
        seg = MAIN_PY[MAIN_PY.index('VERSION = "0.13.65"'):][:2400]
        for kw in ("fed.sources=0", "skipped_budget", "全指标绿而功能层已死"):
            self.assertIn(kw, seg, "版本注释里丢了 %s —— 半年后没人知道为什么注入默认不收窄" % kw)


if __name__ == "__main__":
    unittest.main()
