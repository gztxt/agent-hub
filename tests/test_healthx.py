"""L0：/health 的「画像最近检测时间」派生量（`src/healthx.py`）。

为什么抽成纯函数再钉闸门：项目分层铁律规定 L0 hermetic **不 import `src.main`**
（见 tests/README.md），而这两个字段原本是直接写在 /health 里的 ⇒ 只能靠 L2 live 探针验，
而 live 探针要服务在跑、会产生副作用（09-24 就发生过一次我方测试污染生产库）。
抽出来后，"三轮没扫 ⇒ stale"这类判据可以在空 HOME 下红绿对照。

要治的具体盲区（09-22 定名的「静默不可用」家族）：
  只看 `last_sweep` 会被"新一轮扫了 6 家、漏了第 7 家"骗过去 —— 那一家可以在册、
  心跳新鲜、却**从没被检测过**。所以必须同时给 `oldest_check_age_s`（最坏值）与 `unchecked`。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import healthx  # noqa: E402

NOW = 1_800_000_000.0
EVERY = 900.0


def snap(*ages):
    """按"距今多少秒被检测过"造快照。"""
    return {f"a{i}": {"checked_at": NOW - a} for i, a in enumerate(ages)}


class TestHeartbeat(unittest.TestCase):
    def test_never_swept_is_stale_not_ok(self):
        d = healthx.profiles_last_check(snap(10), 0.0, EVERY, now=NOW)
        self.assertEqual(d["state"], "never_swept")
        self.assertTrue(d["stale"])
        self.assertIsNone(d["last_sweep_age_s"])

    def test_fresh_sweep_is_ok(self):
        d = healthx.profiles_last_check(snap(10), NOW - 30, EVERY, now=NOW)
        self.assertEqual(d["state"], "ok")
        self.assertFalse(d["stale"])
        self.assertEqual(d["last_sweep_age_s"], 30.0)

    def test_stale_after_configured_rounds(self):
        """边界：正好 STALE_ROUNDS 轮不算红，超过一轮才算 —— 避免"扫描慢于周期"被误报。"""
        edge = NOW - healthx.STALE_ROUNDS * EVERY
        self.assertFalse(healthx.profiles_last_check(snap(10), edge, EVERY, now=NOW)["stale"])
        over = NOW - (healthx.STALE_ROUNDS + 1) * EVERY
        d = healthx.profiles_last_check(snap(10), over, EVERY, now=NOW)
        self.assertTrue(d["stale"])
        self.assertEqual(d["state"], "stale")

    def test_missing_sweep_every_never_falsely_stale(self):
        """sweep_every 拿不到（0/None）时不得凭 last_sweep 判红 —— 无判据就不下结论。"""
        d = healthx.profiles_last_check(snap(10), NOW - 999999, None, now=NOW)
        self.assertFalse(d["stale"])
        self.assertEqual(d["state"], "ok")


class TestWorstCaseNotAverage(unittest.TestCase):
    def test_one_unchecked_agent_is_visible(self):
        """★红对照的核心：6 家刚扫过 + 1 家从没被扫过 ⇒ 心跳仍然"新鲜"，
        但 oldest_check_age_s / unchecked 必须把那一家暴露出来。"""
        s = snap(10, 12, 14, 16, 18, 20)
        s["ghost"] = {}                                  # 在册、无 checked_at
        d = healthx.profiles_last_check(s, NOW - 5, EVERY, now=NOW)
        self.assertEqual(d["agents"], 7)
        self.assertEqual(d["checked"], 6)
        self.assertEqual(d["unchecked"], 1)
        self.assertEqual(d["newest_check_age_s"], 10.0)
        self.assertEqual(d["oldest_check_age_s"], 20.0)
        self.assertEqual(d["state"], "ok")               # 心跳本身没停

    def test_oldest_is_max_not_last(self):
        d = healthx.profiles_last_check(snap(300, 5, 900), NOW - 5, EVERY, now=NOW)
        self.assertEqual(d["oldest_check_age_s"], 900.0)


class TestRobustness(unittest.TestCase):
    def test_garbage_inputs_do_not_raise(self):
        """/health 是自检端点，情报字段绝不能把它打挂（脏数据只降级、不抛）。"""
        for s, ls, ev in [(None, None, None), ({}, None, 900),
                          ({"a": None, "b": {"checked_at": "not-a-number"}}, "NaN", 900),
                          ({"a": {"checked_at": NOW}}, NOW, 0)]:
            d = healthx.profiles_last_check(s, ls, ev, now=NOW)
            self.assertIn("state", d)
            self.assertIn("agents", d)

    def test_all_required_keys_present(self):
        d = healthx.profiles_last_check(snap(1), NOW, EVERY, now=NOW)
        for k in ("agents", "checked", "unchecked", "sweep_every_sec", "last_sweep_age_s",
                  "oldest_check_age_s", "newest_check_age_s", "stale", "state", "stale_rounds"):
            self.assertIn(k, d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
