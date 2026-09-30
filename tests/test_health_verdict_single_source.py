"""L0 hermetic：Agent 异常判据必须全站唯一（P1-20，PT-20260930-01）。

病根不是"数字对不上"，是**四处各判各的**，且其中一处取了一个永远不取该值的字段：

  顶栏可用数   a.attested === true
  卡片标签     a.verdict === 'usable'
  异常计数     a.status === 'error'      ← vitals 从不把 status 打成 'error'
  导航排序     NAV_RANK[a.status]

于是异常计数**恒为 0**，且是结构性 0（不是"真没异常"）。用户看到的是
"顶栏 10 个可用 / 列表 4 个标着别的东西 / 异常永远是 0"，无从判断哪个是真的。

本测试锁两件事：
  ① agentHealth() / countBadAgents() 是唯一判据入口，异常计数不许再散落 filter
  ② 排序的异常档确实接到了 agentHealth（否则异常项仍埋在列表中间）
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "static" / "hub" / "01-core-boot.js"
NAV = REPO / "static" / "hub" / "05-chat-and-history.js"


def _src(p: Path) -> str:
    s = p.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)      # 去块注释
    s = re.sub(r"//[^\n]*", "", s)                   # 去行注释
    return s


class TestSingleVerdictSource(unittest.TestCase):
    def test_agent_health_defined_once(self):
        s = _src(CORE)
        self.assertEqual(s.count("function agentHealth("), 1,
                         "agentHealth 必须只有一处定义，否则又变成多套口径")

    def test_count_helper_defined_once(self):
        s = _src(CORE)
        self.assertEqual(s.count("function countBadAgents("), 1)

    def test_broken_is_the_bad_verdict(self):
        """只有实测起不来（broken）才算异常；账号受限/未运行都不是「异常」。"""
        s = _src(CORE)
        seg = s.split("function agentHealth(", 1)[1].split("\n}", 1)[0]
        # 断言语义而非字面量：函数体先取出 v = a.verdict，再以 'broken' 为坏档
        self.assertRegex(seg, r"= a\.verdict;",
                         "agentHealth 应取 a.verdict 作为唯一裁决字段")
        self.assertRegex(seg, r"===\s*['\"]broken['\"]",
                         "异常档必须是 verdict==='broken'")
        # 不得把 blocked_by_account 之类也算成异常：那是账号条件，不是服务故障
        for wrong in ("blocked_by_account", "rate_limited", "stopped"):
            self.assertNotIn(wrong, seg, f"{wrong} 不该被算作「异常」")

    def test_non_agent_kinds_excluded(self):
        """网关/服务/工具/记忆的 status 来自 systemd+docker，与 vitals 结论不同源。"""
        s = _src(CORE)
        seg = s.split("function agentHealth(", 1)[1].split("\n}", 1)[0]
        self.assertRegex(seg, r"kind\s*!==\s*['\"]agent['\"]",
                         "非 agent 类型必须显式排除，否则服务没起会被算成 agent 异常")


class TestNoStrayStatusErrorCounting(unittest.TestCase):
    """异常计数不许再直接数 status==='error'。"""

    def test_core_has_no_status_error_count(self):
        s = _src(CORE)
        self.assertNotRegex(
            s, r"filter\([^)]*\.status\s*===\s*['\"]error['\"]",
            "仍有地方按 status==='error' 数异常（该字段恒不取此值 ⇒ 结构性 0）")

    def test_both_surfaces_use_the_helper(self):
        s = _src(CORE)
        self.assertGreaterEqual(s.count("countBadAgents("), 3,
                                "定义 1 + 顶栏异常块 + 首页摘要，两处调用都要在")


class TestNavRankUsesVerdict(unittest.TestCase):
    def test_nav_rank_bad_bucket_uses_agent_health(self):
        s = _src(NAV)
        seg = s.split("function navRank(", 1)[1].split("\n}", 1)[0]
        self.assertIn("agentHealth", seg,
                      "navRank 的异常档没接 agentHealth ⇒ 启动异常的 Agent 仍按 running 排中间")

    def test_nav_rank_no_dead_error_bucket(self):
        s = _src(NAV)
        self.assertNotRegex(s, r"NAV_RANK\s*=\s*\{[^}]*\berror\s*:",
                            "NAV_RANK.error 档永远命中不到（vitals 不产出该 status）")

    def test_agent_health_resolvable_from_nav_shard(self):
        """navRank 在 05 分片，agentHealth 在 01 分片；拼接序 01 在前 ⇒ 可直接引用。
        判据是拼接产物里真的能解析到，不是靠 import。"""
        boot = (REPO / "static" / "hub.js").read_text(encoding="utf-8")
        i_def = boot.find("function agentHealth(")
        i_use = boot.find("function navRank(")
        self.assertNotEqual(i_def, -1, "拼接产物里找不到 agentHealth 定义")
        self.assertNotEqual(i_use, -1, "拼接产物里找不到 navRank")
        self.assertLess(i_def, i_use, "agentHealth 必须定义在 navRank 之前（否则 TDZ）")


if __name__ == "__main__":
    unittest.main()
