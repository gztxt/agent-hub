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

    #: 每个已 bump 的版本号 -> 该版根因注释里必须在场的关键字。
    #: 新增一行就是新增一道“半年后没人知道当时为何这么改”的闸门；删除一行等于销账。
    VERSION_ROOT_CAUSES = {
        "0.13.65": ("fed.sources=0", "skipped_budget", "全指标绿而功能层已死", "scrollSensitivity"),
        "0.13.66": ("PT-20261002-11", "find 不带 -L", "同源副本", "四态"),
        "0.13.67": ("子串", "name命中恒为空", "不改写排序", "PT-20261002-12"),
        "0.13.68": ("not-implemented", "counted", "suggested_action", "PT-20261002-13"),
        "0.13.69": ("第二个真相", "7 路发现点", "蒙对的闸门", "HUB_NARROW_MQ"),
        "0.13.70": ("退出码会让 Claude 拒绝输入", "禁改面拒绝", "报冲突不覆盖", "窄授权不得读成宽授权"),
        # 0.13.71 两条根因。关键字取自 main.py 版本注释里**实际写下的字句**，
        # 不是事后编的摘要——该闸门的作用是「注释掉了根因就红」，所以关键字必须可寻址。
        "0.13.71": ("归档根与发现点是两张表", "架空 `EXCLUDED_DIRS`", "crawl1ai", "≈近似"),
        "0.13.72": ("模糊匹配不再是「技能中心专属」", "恒真的 L0 用例", "想写也写不成", "那个 6 是我数错的"),
        "0.13.73": ("同一台机器两套口径", "MCP 门面 `hubmcp.py`", "两侧一起错", "不改排序"),
        "0.13.74": ("窄屏第一眼是一块白板", "首帧", "为什么必须在 <head>", "夹具必须判别"),
        "0.13.75": ("每个字一行", "修的是那一个选择器", "判不了像素", "短页面底部有留白"),
        "0.13.76": ("抽屉永远打不开", "点哪都点不到", "从来没点过开", "只验一个时刻"),
        "0.13.77": ("按不可信输入处理", "存储型 XSS", "顺序不可颠倒", "**规则里的**类名不存在"),
        "0.13.78": ("形状完全一样", "既没转义、也没过", "HTML 正文", "JS 字面量"),
        # 0.13.79：全指标绿而闸门说谎的那一条 —— FORCE_COLOR 让 node 把数字染成
        # ANSI，22 条用例假红，而「全绿/全红」本身就是本轮一切判据的前提。
        # 关键字取自 src/main.py 的 VERSION 注释里实际写下的字句。
        "0.13.79": ("FORCE_COLOR", "假红", "逐版根因已于 2026-10-05 归档",
                    "逐字搬运", "只缓存成功路径"),
        # 0.13.80：CloudCLI 独立子菜单拆出，claude 卡不再挂 :3010 宿主。
        # 根因：cloudcli 宿主嵌在 claude 卡里时，点 Claude Code 菜单行会进
        # embed（cloudcli 原生界面），终端页对 cloudcli 用户不可达；同时
        # cloudcli 原生界面没有独立子菜单入口，项目直达只能走 claude 详情抽屉。
        # 拆开后：claude=纯终端+对话，cloudcli=独立卡+embed原生界面+项目直达面板。
        "0.13.80": ("cloudcli", "独立子菜单", "embedBody", "renderCloudcliProjects"),
        # 0.13.81：终端鼠标跟踪看门狗。TUI 开 1002/1003 后滚轮/拖选被吞，
        # 异常退出不发 ?1003l 时 mouseTrackingMode 卡 any，只有重连才复位。
        # 修复：capture 阶段 wheel/mousedown，同步切 activeProtocol=NONE。
        "0.13.81": ("鼠标跟踪", "1003", "看门狗", "activeProtocol", "TERM_MOUSE_OFF"),
        # 0.13.82：备用屏（?1049）跨会话污染。codex 的 TUI 开机发 ?1049h 进备用屏，
        # 而 xterm.js 的备用屏按设计没有 scrollback；整页共用一个 xterm 实例、切会话的
        # term.clear() 又不退出备用屏（只有 term.reset() 会）⇒ 一次 codex 把整页拖进去，
        # 之后所有 agent（含 cursor Agent）都滚不动。cloudcli 正常因它是普通 shell→TTY。
        # 修复：前端 TERM_STATE_RESET 在换会话那一帧写 + codex 启动带 --no-alt-screen
        # + 工具栏「缓冲区状态字 / 退出备用屏」。
        # ⚠ 与 0.13.81 是**两条不同的腿**：那版治鼠标跟踪态，从没量过 buffer.active.type。
        "0.13.82": ("备用屏", "?1049h", "TERM_STATE_RESET", "--no-alt-screen", "term.reset()"),
        # 0.13.83：用户裁定「只用主屏」+「输入控件关闭鼠标」+「cursor 没有会话记录」。三条：
        #   ① 备用屏改从**解析层**禁掉（DECSET 1049/1047/47 注册成吞掉，返回 true 即拦下，
        #      activateAltBuffer 不执行），v0.13.82 的 TERM_ALT_OFF/TERM_STATE_RESET、
        #      状态字 #termBufChip 与按钮 #termAltOut 全删（备用屏不会发生 ⇒ 死码）。
        #   ② 终端页唯一能吃鼠标的输入控件（查找框 #termFindInput）加 pointer-events:none，
        #      鼠标事件透传给终端 ⇒ 滚轮只滚 scrollback，不再两边抢焦点。鼠标上报转发**不动**。
        #   ③ cursor 补 SESSION_STORES + TERM_HIST_AGENTS：此前 CLI_ALIASES 有、这张表没有
        #      ⇒ 前端根本不发历史请求。适配器以 chats/meta.json 为主表、转录只供标题。
        # ⚠ 与 0.13.82 的关系：那版是「补写退出序列 + 手动逃生」，这版是**禁止进入**，
        #   方向相反 —— 所以 0.13.82 的 TERM_ALT_OFF 在代码里已不存在，只留在注释与 CHANGELOG。
        "0.13.83": ("termAltScreenBlock", "pointer-events", "cursor_json",
                    "#termFindInput", "hasConversation"),
        # 0.13.84：同一套嵌入式终端「别的 agent 好了、claude/opencode 还不行」。机制分层：
        #   全屏 TUI 的历史不在 scrollback（主屏一屏高、viewportY 恒 0），向上翻只能把
        #   滚轮 SGR 上报喂回 pty 由 app 自滚。want（app 意图）与 xterm 临时态分家、
        #   wheel 分层、mouseup 回装、非 shell 回放信任 + per-sid 缓存、4410 清零防死轮。
        "0.13.84": ("termMouseWant", "termMouseArm", "4410", "死轮", "应用内滚动"),
    }
    CURRENT_VERSION = "0.13.84"

    #: 2026-10-05 起，历史版本的根因**逐字归档在 CHANGELOG.md**（main.py 的
    #: VERSION 注释块从 1417 行缩到 1047 行，只留当前版 + 一行指针）。
    #: 本闸门的「不许随 bump 蒸发」意图**不变**，只是把「在哪」从 main.py 放宽到
    #: 「main.py 或 CHANGELOG 任一处」—— 判据是**内容还在**，不是**文件没变**。
    #: ⚠ MAIN_PY 是**已读出的文本**不是 Path（见 :31），所以路径要从 REPO 拼。
    CHANGELOG_MD = REPO / "CHANGELOG.md"

    def test_version_comment_records_root_cause(self):
        # 版本钉随版本号走：它是一道**随行闸门**，逼迫 bump 的人回头看根因注释还在不在，
        # 而不是让上一版的注释默默过期。本闸门只跟当前版本号走，不承担跨版本归档职责。
        self.assertIn('VERSION = "%s"' % self.CURRENT_VERSION, MAIN_PY)
        seg = MAIN_PY[MAIN_PY.index('VERSION = "%s"' % self.CURRENT_VERSION):][:2400]
        for kw in self.VERSION_ROOT_CAUSES[self.CURRENT_VERSION]:
            self.assertIn(kw, seg,
                          "%s 的版本注释里丢了 %s —— 半年后没人知道当时为何这么改"
                          % (self.CURRENT_VERSION, kw))

    def test_earlier_version_root_causes_survive(self):
        """更早批次的根因关键字必须**仍在**（main.py 或 CHANGELOG）——版本钉只跟当前版走，
        但注释不许随 bump 蒸发，否则 0.13.65「全指标绿而功能层已死」那条教训就没了。

        2026-10-05：历史块搬进 CHANGELOG.md（`scripts/extract_changelog.py` 逐字搬运，
        368 行），main.py 只留指针。原判据只查 MAIN_PY ⇒ 归档后 22 条关键字判红。
        这是**闸门与新约定冲突**，不是代码坏了；改判据而不是把归档退回去——
        因为「两份各自会漂的副本」正是本仓反复吃亏的东西（CHANGELOG 与 README
        的测试计数就漂过一轮）。
        """
        try:
            changelog = self.CHANGELOG_MD.read_text(encoding="utf-8")
        except OSError:
            changelog = ""
        # ⚠ MAIN_PY 已是**文本**（见 :31），别再 .read_text() —— 那是本条的第二版。
        where = MAIN_PY + "\n" + changelog
        missing = []
        for ver, kws in self.VERSION_ROOT_CAUSES.items():
            if ver == self.CURRENT_VERSION:
                continue
            for kw in kws:
                if kw not in where:
                    missing.append("%s: %s" % (ver, kw))
        self.assertEqual([], missing,
                         "这些版本的根因关键字从 main.py 与 CHANGELOG.md 双双消失了：%s"
                         % missing)


if __name__ == "__main__":
    unittest.main()
