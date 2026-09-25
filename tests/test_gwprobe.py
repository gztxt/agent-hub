"""L0：上游网关体检（`src/gwprobe.py`）—— /health 的 `ccr_gateway` 情报源。

为什么值得单独钉闸门（2026-09-25 实测驱动，不是假想）：
  · 本机三次同源事故都是**模型 ID 失效而 /health 全绿**：09-06 `minimax-m3:free` HTTP 400、
    09-19 `'ultra'` 无效、09-23 `qwen3.8-flash` 缺 provider 前缀（有效 ID 是 `alibaba/qwen3.8-flash`）。
  · 09-23 台账把 M1 记成"阻塞：拿不到 CCR 在线清单（401）"。09-25 用 hub 自己的
    `MANAGER_LLM_API_KEY` 打 `http://127.0.0.1:3456/v1/models` 实测 **200 / 14 个 ID / 1.8ms**
    ⇒ 阻塞解除，清单进 /health 常驻可观测。
  · 同时**改判一条错账**：PT-20260923-05 说 vitals 的 L4 探针模型 `agnes/agnes-2.0-flash`
    是"CCR 免费池成员"。实测 14 个 ID 里带 free 的 5 个全是 `openrouter/*:free`，
    agnes 三个档（2.0-flash / 2.5-flash / 2.5-pro）**都不在免费池** ⇒ 该前提不成立，
    M1 的真实价值改成"上游改名/下架可观测"，下面 `test_free_pool_membership_is_from_real_list`
    把这次实测的清单钉成断言（谁再凭记忆改判就会红）。

本文件属 L0 hermetic：**零网络**（`gwprobe.fetch` 被替换）、不 import `src.main`、不读真盘。
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import gwprobe  # noqa: E402

# ★以下 14 个 ID 是 2026-09-25 08:5x 实测 `GET http://127.0.0.1:3456/v1/models`（HTTP 200）的
#   原样返回，**不是凭记忆写的**。改动前请重新实测（军规第 2 条：取值一律实测取证）。
REAL_IDS = [
    "alibaba/qwen3.7-max", "alibaba/qwen3.8-max", "alibaba/qwen3.8-flash",
    "openrouter/poolside/laguna-xs-2.1:free", "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/poolside/laguna-s-2.1:free", "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
    "openrouter/openrouter/free",
    "deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash-vision-exp",
    "agnes/agnes-2.5-flash", "agnes/agnes-2.5-pro", "agnes/agnes-2.0-flash",
]
FAKE_KEY = "FAKE-KEY-VALUE-0123456789abcdef"


def _fake_fetch_factory(ids=None, code=200, exc=None):
    def _f(url, key, timeout=None):
        if exc:
            raise exc
        return code, list(REAL_IDS if ids is None else ids)
    return _f


class TestUrlNormalization(unittest.TestCase):
    """base 的三种写法都要归一到 .../v1/models —— 09-07 那次事故正是路径拼错 + 端口写错混在一起。"""

    def test_bare_host_gets_v1_models(self):
        self.assertEqual(gwprobe.models_url("http://127.0.0.1:3456"), "http://127.0.0.1:3456/v1/models")

    def test_with_v1_suffix(self):
        self.assertEqual(gwprobe.models_url("http://127.0.0.1:3456/v1"), "http://127.0.0.1:3456/v1/models")

    def test_already_models(self):
        self.assertEqual(gwprobe.models_url("http://127.0.0.1:3456/v1/models"), "http://127.0.0.1:3456/v1/models")

    def test_trailing_slash_tolerated(self):
        self.assertEqual(gwprobe.models_url("http://127.0.0.1:3456/v1/"), "http://127.0.0.1:3456/v1/models")

    def test_empty_base_is_none_not_crash(self):
        self.assertIsNone(gwprobe.models_url(""))


class TestEndpointLabel(unittest.TestCase):
    """/health 是**未鉴权**端点 ⇒ 端点标签里绝不能带 path/query（凭据常以 ?token= 形态出现）。"""

    def test_strips_path_and_query(self):
        got = gwprobe.endpoint_label("http://127.0.0.1:3456/v1?token=SUPERSECRETVALUE123")
        self.assertEqual(got, "http://127.0.0.1:3456")
        self.assertNotIn("SUPERSECRETVALUE123", got)

    def test_empty(self):
        self.assertEqual(gwprobe.endpoint_label(""), "")


class TestFreePoolJudgement(unittest.TestCase):
    def test_free_shapes(self):
        for i in ("openrouter/openrouter/free", "openrouter/poolside/laguna-s-2.1:free"):
            self.assertTrue(gwprobe.is_free(i), i)

    def test_non_free(self):
        for i in ("agnes/agnes-2.0-flash", "alibaba/qwen3.8-flash", "deepseek/deepseek-v4-pro"):
            self.assertFalse(gwprobe.is_free(i), i)

    def test_free_pool_membership_is_from_real_list(self):
        """★改判钉子：免费池成员 = 5 个且全是 openrouter/*；agnes 三档一个都不在里面。

        PT-20260923-05 曾断言 `agnes/agnes-2.0-flash` 是免费池成员（据此立了 M1"换掉它"）。
        实测清单否证了该断言 ⇒ 这里把它钉死，防止旧结论被再次当成前提复用。
        """
        free = [i for i in REAL_IDS if gwprobe.is_free(i)]
        self.assertEqual(len(free), 5)
        self.assertTrue(all(i.startswith("openrouter/") for i in free), free)
        self.assertNotIn("agnes/agnes-2.0-flash", free)
        self.assertEqual(len(REAL_IDS), 14)


class TestProbeSemantics(unittest.TestCase):
    def setUp(self):
        gwprobe.reset_cache_for_test()
        gwprobe.configure("http://127.0.0.1:3456/v1", FAKE_KEY,
                          watch=["alibaba/qwen3.8-flash", "agnes/agnes-2.0-flash"])
        self._orig_fetch = gwprobe.fetch

    def tearDown(self):
        gwprobe.fetch = self._orig_fetch
        gwprobe.reset_cache_for_test()

    def test_ok_probe_reports_counts_and_watch(self):
        gwprobe.fetch = _fake_fetch_factory()
        d = gwprobe.probe_now()
        self.assertEqual(d["state"], "ok")
        self.assertEqual(d["http"], 200)
        self.assertEqual(d["models_count"], 14)
        self.assertEqual(d["free_count"], 5)
        self.assertEqual(d["watch"], {"alibaba/qwen3.8-flash": True, "agnes/agnes-2.0-flash": True})
        self.assertIsInstance(d["probe_ms"], float)

    def test_watch_detects_rename_not_stuck_true(self):
        """红对照：上游把 ID 改名后，watch 必须翻成 False —— 证明该判据不是恒真。"""
        renamed = [i for i in REAL_IDS if i != "agnes/agnes-2.0-flash"] + ["agnes/agnes-9.9-flash"]
        gwprobe.fetch = _fake_fetch_factory(ids=renamed)
        d = gwprobe.probe_now()
        self.assertEqual(d["state"], "ok")
        self.assertFalse(d["watch"]["agnes/agnes-2.0-flash"])
        self.assertTrue(d["watch"]["alibaba/qwen3.8-flash"])

    def test_http_error_is_error_state_with_reason(self):
        gwprobe.fetch = _fake_fetch_factory(code=401)
        d = gwprobe.probe_now()
        self.assertEqual(d["state"], "error")
        self.assertEqual(d["error"], "http_401")

    def test_exception_is_captured_not_raised(self):
        gwprobe.fetch = _fake_fetch_factory(exc=OSError("connection refused"))
        d = gwprobe.probe_now()
        self.assertEqual(d["state"], "error")
        self.assertIn("OSError", d["error"])
        self.assertIn("connection refused", d["error"])

    def test_empty_list_is_not_ok(self):
        """"200 但清单为空"必须区别于 ok —— 否则网关半死会被读成全绿。"""
        gwprobe.fetch = _fake_fetch_factory(ids=[])
        d = gwprobe.probe_now()
        self.assertEqual(d["state"], "empty")
        self.assertEqual(d["error"], "empty_model_list")

    def test_no_base_url_is_error(self):
        gwprobe.configure("", FAKE_KEY)
        d = gwprobe.probe_now()
        self.assertEqual(d["state"], "error")
        self.assertEqual(d["error"], "no_base_url")


class TestStatusIsNonBlocking(unittest.TestCase):
    """/health 被前端轮询 ⇒ status() 必须纯读缓存，绝不当场发网络。"""

    def setUp(self):
        gwprobe.reset_cache_for_test()
        gwprobe.configure("http://127.0.0.1:3456/v1", FAKE_KEY, watch=["agnes/agnes-2.0-flash"])
        self.scheduled = []
        self._orig_sched = gwprobe._schedule_probe
        self._orig_fetch = gwprobe.fetch
        gwprobe._schedule_probe = lambda: self.scheduled.append(1) or False

    def tearDown(self):
        gwprobe._schedule_probe = self._orig_sched
        gwprobe.fetch = self._orig_fetch
        gwprobe.reset_cache_for_test()

    def test_cold_cache_is_unknown_and_schedules(self):
        d = gwprobe.status()
        self.assertEqual(d["state"], "unknown")
        self.assertTrue(d["stale"])
        self.assertIsNone(d["age_s"])
        self.assertEqual(len(self.scheduled), 1, "冷缓存必须触发一次后台探测")

    def test_warm_cache_does_not_reschedule(self):
        gwprobe.fetch = _fake_fetch_factory()
        gwprobe.probe_now()
        self.scheduled.clear()
        d = gwprobe.status()
        self.assertEqual(d["state"], "ok")
        self.assertFalse(d["stale"])
        self.assertEqual(self.scheduled, [])
        self.assertIsNotNone(d["age_s"])

    def test_credential_value_never_appears_in_output(self):
        """★凭据泄漏闸门：配置里的 key 值不得出现在 status() 的任何字段（含 watch/models）。"""
        gwprobe.fetch = _fake_fetch_factory()
        gwprobe.probe_now()
        blob = json.dumps(gwprobe.status(), ensure_ascii=False)
        self.assertNotIn(FAKE_KEY, blob)
        self.assertIn('"key_present": true', blob)
        self.assertIn('"key_len": %d' % len(FAKE_KEY), blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
