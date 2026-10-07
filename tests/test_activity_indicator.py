"""L0 hermetic：跨 Agent 活动指示（P2-B，PT-20260930-01）。

兑现「要看谁在跑必须逐个点开」这条承诺。走计划的低成本路径：复用既有
/api/term/sessions，**不依赖服务端 pyte**（融合计划 P2-1 真值方案成本高风险大，本批不做）。

口径纪律（与 P1-20 同一个教训，判据不许散落）：
  · 「在跑」只认 sessions[].alive —— 那是真会话；
    **不用** last_seen / 进程表去猜「有没有人在用」，那是「服务活着」不是「人在用」。
  · 拉取失败保持上一次的值，不清零：指示消失比指示滞后更糟。
  · 与「可用数」刻意分开：usable = 服务在跑；在跑 N = 此刻有人在用。

本测试锁：判据唯一（不散落 alive 判断）；失败不清零；侧栏点有会话才亮；
顶栏挂载点存在；新分片已登记进红基线豁免名单。
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ACT = REPO / "static" / "hub" / "12-activity.js"
NAV = REPO / "static" / "hub" / "05-chat-and-history.js"
TPL = REPO / "templates" / "index.html"
GUARD = REPO / "tests" / "test_ls_guard.py"


def _src(p: Path) -> str:
    s = p.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


class TestActivitySingleSource(unittest.TestCase):
    def setUp(self):
        self.s = _src(ACT)

    def test_alive_is_the_only_criterion(self):
        """在跑的判据只能是**活着的**会话；不许拿 last_seen / 进程表猜。

        v0.13.90 起判据由服务端给（/api/term/activity 只回 alive 聚合，
        服务端那侧就是 `[s for s in _sessions.values() if s.alive]`），
        本仓前端不再重复做 alive 判断 —— 所以这里锁两件事：
          · 前端不吃「非存活」数据：零值条目必须被显式拒绝（防将来服务端
            多回一条 n=0 就在侧栏亮出一个假忙碌点）；
          · 服务端判据没被换掉：仍然只认 alive。
        """
        seg = self.s.split("function activityMap(", 1)[1].split("\n}", 1)[0]
        self.assertRegex(seg, r"!\(x\.n > 0\)|!x\.n",
                         "活动映射没拒绝零会话条目（会在侧栏亮出假忙碌点）")
        for wrong in ("last_seen", "psutil", "status ==="):
            self.assertNotIn(wrong, seg, f"{wrong} 不是「有人在用」的判据，别混进来")
        srv = (REPO / "src" / "term.py").read_text(encoding="utf-8")
        act = srv.split('async def term_activity(', 1)[1].split('@router.get', 1)[0]
        self.assertRegex(act, r"s\.alive",
                         "服务端聚合端点丢了 alive 判据（口径必须与 /api/term/sessions 同源）")

    def test_both_signals_come_from_one_map(self):
        """侧栏点与顶栏计数必须读同一个 ACTIVITY，不许各自去拉接口。"""
        self.assertEqual(self.s.count("await api('/api/term/activity'"), 1,
                         "活动数据只能拉一次；两处上屏共用 ACTIVITY")
        self.assertIn("activityMap(", self.s)

    def test_activity_endpoint_is_token_free(self):
        """顶栏计数是**被动展示**：绝不能为看它而索要终端口令（用户报障的根因）。

        改前这条读 lsGet('hub.term.token')，没口令就整块不显示 ——
        localStorage 按 origin 隔离 ⇒ 局域网那个源没存过口令时「会话数」直接消失，
        而「读不到」与「真的零会话」在屏幕上长得一模一样。
        """
        seg = self.s.split("async function loadActivity(", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("hub.term.token", seg,
                         "活动指示又去读终端口令了 ⇒ 无口令的源上计数会整块消失")
        self.assertNotIn("termHeaders(", seg, "活动指示不许走 termHeaders()（会弹口令框）")
        # 服务端那个端点也必须真的免 token。
        srv = (REPO / "src" / "term.py").read_text(encoding="utf-8")
        act = srv.split('async def term_activity(', 1)[1].split('@router.get', 1)[0]
        self.assertNotIn("_check_term_token", act,
                         "/api/term/activity 加了口令校验 ⇒ 顶栏计数又变成「有口令才显示」")

    def test_activity_endpoint_leaks_no_handles(self):
        """免 token 的代价必须被限制住：只给聚合计数，不给任何可操作句柄。

        /api/term/sessions 之所以要口令，是因为它带 sid（能直接拿去 DELETE）。
        这个端点一旦回 sid / cmd / cwd，就等于把控制平面的入口绕开了。
        """
        srv = (REPO / "src" / "term.py").read_text(encoding="utf-8")
        act = srv.split('async def term_activity(', 1)[1].split('@router.get', 1)[0]
        # 只查**函数体**：docstring 里为了解释「为什么不能放开口令」本来就要
        # 提到 sid/cmd/cwd 这些字段名，把文档也算成泄漏是判据写歪了。
        body = act.split('"""', 2)[2] if act.count('"""') >= 2 else act
        for leak in ("to_dict()", "s.id", '"cmd"', "'cmd'", "s.cwd", "sess.id"):
            self.assertNotIn(leak, body,
                             f"免 token 端点漏了 {leak} ⇒ 等于绕开 /api/term/sessions 的口令闸门")
        self.assertNotIn("s.id", body, "免 token 端点回了 sid（可直接拿去 DELETE）")

    def test_failure_keeps_last_value(self):
        """拉取失败不清零：指示消失比指示滞后更糟。"""
        seg = self.s.split("async function loadActivity(", 1)[1].split("\n}", 1)[0]
        catch_seg = seg.split("catch (e) {", 1)[1]
        self.assertRegex(catch_seg, r"activityFailed\s*=\s*true")
        # 清空必须**有条件**：只有「从未成功过」才允许置空，成功过就沿用旧值。
        # 锁的是这个条件，不是「不许出现 {}」——正确实现里那行本来就该在。
        m = re.search(r"(if\s*\([^)]*activityLoaded[^)]*\))\s*ACTIVITY\s*=\s*\{\}", catch_seg)
        self.assertIsNotNone(m, "失败分支无条件把 ACTIVITY 清空了：在跑数会闪成 0 再闪回")
        self.assertRegex(m.group(1), r"!activityLoaded",
                         "清空条件必须是「从未成功过」")

    def test_dot_only_when_sessions_exist(self):
        seg = self.s.split("function activityDotHtml(", 1)[1].split("\n}", 1)[0]
        self.assertRegex(seg, r"if \(!st\)\s*return\s*''",
                         "无会话时也要占位 ⇒ 零会话时侧栏与改动前不一致")


class TestActivityWiring(unittest.TestCase):
    def setUp(self):
        self.s = _src(ACT)
        self.tpl = TPL.read_text(encoding="utf-8")
        self.nav = _src(NAV)

    def test_header_slot_exists(self):
        self.assertIn('id="hBusy"', self.tpl, "顶栏没有挂载点（setBadge 家族同款死代码）")

    def test_header_shows_both_counts(self):
        """顶栏两个数都要给（用户 2026-10-07 口径）：
        「在跑 N」= 有活会话的 Agent 数；「会话 M」= 活会话总条数。"""
        seg = self.s.split("function renderActivity(", 1)[1].split("\n}", 1)[0]
        self.assertRegex(seg, r"在跑 ' \+ n",
                         "顶栏在跑数不见了")
        self.assertRegex(seg, r"会话 ' \+ sess",
                         "顶栏没给会话总条数（用户报的「会话 3」）")
        self.assertRegex(seg, r"ids\.reduce\(\(s, id\) => s \+ \(ACTIVITY\[id\]\.n \|\| 0\), 0\)",
                         "会话总数必须是各 agent 条数之和（不是 agent 个数）")

    def test_nav_row_wires_the_dot(self):
        self.assertIn("activityDotHtml", self.nav, "侧栏行没挂忙碌点")

    def test_nav_dot_uses_escapehtml(self):
        seg = self.nav.split("function navItemHtml(", 1)[1].split("\n}", 1)[0]
        self.assertIn("activityDotHtml", seg)
        fn = _src(ACT).split("function activityDotHtml(", 1)[1].split("\n}", 1)[0]
        self.assertIn("escapeHtml", fn, "忙碌点的 title/aria-label 走 innerHTML，必须转义")

    def test_reduced_motion_respected(self):
        self.assertIn("prefers-reduced-motion", self.tpl, "呼吸动画未尊重 reduced-motion")

    def test_shard_registered_in_red_baseline_exemption(self):
        """新分片必须显式登记进 SHARDS_AFTER_RED，否则红基线闸门会退化成静默 skip。"""
        g = GUARD.read_text(encoding="utf-8")
        self.assertIn('"12-activity.js"', g,
                      "12-activity.js 未登记进红基线豁免名单 ⇒ 取不到旧体会静默跳过（假绿）")

    def test_never_triggers_token_prompt(self):
        """被动展示**绝不许**逼用户交终端口令 —— v0.13.90 起连口令都不读了。

        实测的坑（真渲染探针抓的，renderer 直接挂死）：termHeaders() → termToken()
        在没有存档 token 时 prompt() 弹**原生**口令框，原生弹窗挂住 renderer ⇒
        任何 CDP 请求都超时、页面看着"卡死"。活动指示是加载即执行的被动轮询，走 termHeaders()
        等于「每个没配终端口令的访问者一进页面就被弹一次口令框」——
        为了看一个忙碌点逼人交密码。

        改前的折中是「只读 lsGet('hub.term.token')，缺则安静跳过」；那个折中本身
        就是本次报障的根因（localStorage 按 origin 隔离 ⇒ 有的源上计数整块消失，
        而"读不到"与"零会话"屏幕同形）。现在数据源是免 token 的聚合端点，
        所以口径收紧成：**既不许弹口令框，也不许读口令**。
        """
        self.assertNotIn("termHeaders(", self.s,
                         "活动指示走了 termHeaders() ⇒ 无 token 时加载即弹原生口令框")
        self.assertNotIn("hub.term.token", self.s,
                         "活动指示又去读终端口令了 ⇒ 没有口令的源上「会话数」会整块消失（本次报障根因）")

    def test_polled_independently_of_agents(self):
        """活动比实体状态变化快（秒级 vs 分钟级），必须独立轮询。"""
        self.assertRegex(self.s, r"setInterval\(loadActivity,\s*\d+\)",
                         "活动指示没有独立轮询")


if __name__ == "__main__":
    unittest.main(verbosity=2)
