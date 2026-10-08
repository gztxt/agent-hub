#!/usr/bin/env python3
"""L0 hermetic 测试：D2 技能相关性检索（`src/skill_relevance.py` + `src/jev_client.py`）。

分层口径（见 `tests/README.md`）：
- 本文件属 **L0**：不发任何网络请求、不读任何真实技能目录、不 import `src.main`、**不允许 SKIP**。
  `jev_client` 的测试全部停在「发请求之前」的那一步（缺 key / 已熔断 / 缓存命中），
  或直接测纯函数（`_build_payload` / `_parse` / `_record_failure`）——
  真发请求意味着花钱、依赖外网、且失败会变成偶发红。
- 端点级用例用 tmp 技能根 + 只挂 `skill.router` 的裸 app（照 `tests/test_kb_federation.py`
  的做法），因此仍然不碰真盘。

为什么这层值得单独存在：本仓已经吃过分词的亏（`kb.py:20` 记着「unicode61 下中文查询
『端口』只召回 2 条」），而**分词器的错误不会抛异常、只会安静地少召回**。
所以「中文能不能切出词」「名字命中是否真的压过描述命中」必须钉成断言，而不是靠肉眼看结果。
"""
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

import jev_client                                          # noqa: E402
import skill                                               # noqa: E402
import skill_relevance as sr                               # noqa: E402
from fastapi import FastAPI                                # noqa: E402
from fastapi.testclient import TestClient                  # noqa: E402


def _skill(name, desc):
    return {"name": name, "description": desc, "route": "t", "routes": ["t"]}


CORPUS = [
    _skill("agent-dispatch", "判断任务该派给谁、子代理与外部 CLI 的取舍"),
    _skill("unified-memory", "统一记忆、跨 agent 记忆召回、知识库检索"),
    _skill("systematic-debugging", "遇到 bug 先定位根因再改，禁止猜"),
    _skill("hallmark", "反 AI 味设计，页面审计与重做"),
]


class TestTokenize(unittest.TestCase):
    def test_empty_and_none_yield_nothing(self):
        self.assertEqual(sr.tokenize(""), [])
        self.assertEqual(sr.tokenize(None), [])

    def test_latin_lowercased_and_split(self):
        self.assertEqual(sorted(sr.tokenize("Agent-Dispatch SKILL")),
                         ["agent", "agent-dispatch", "dispatch", "skill"])

    def test_hyphenated_names_also_emit_their_parts(self):
        """连字符命名必须额外拆出子词，否则 `agent-dispatch` 对查询里的 `dispatch`
        永远不命中（真实盘实测：name命中恒为空）。"""
        toks = sr.tokenize("book-to-skill")
        self.assertIn("book-to-skill", toks)   # 整名也在
        for part in ("book", "to", "skill"):   # 子词也在
            self.assertIn(part, toks)

    def test_single_letter_parts_are_dropped(self):
        """`a-b` 的 `b` 长度为 1，丢掉，否则噪声词变多。"""
        self.assertNotIn("b", sr.tokenize("a-b"))

    def test_cjk_emits_bigrams_only(self):
        """只出二字：同时出单字会在真实盘上把高频字（能/不/登）累加成噪声，
        详见 `skill_relevance.tokenize` docstring 里记的那次假证据。"""
        self.assertEqual(set(sr.tokenize("技能")), {"技能"})

    def test_single_cjk_char_yields_itself(self):
        self.assertEqual(sr.tokenize("端"), ["端"])

    def test_mixed_script_keeps_both(self):
        toks = sr.tokenize("用 jev 精排")
        self.assertIn("jev", toks)
        self.assertIn("精排", toks)


class TestRank(unittest.TestCase):
    def test_name_match_outranks_description_only_match(self):
        """名字命中必须压过描述命中 —— 这是 W_NAME 存在的全部理由。

        用自造语料而不是本仓真实技能：真实描述改个措辞就会假红，而这里要证明的是
        **打分公式的性质**，不是某条描述的写法。
        词用 `zephyr` 这种只在名字里出现、不会同时出现在对手描述里的词——
        上一版用 `dispatch-tool` 时，对手的描述里也含这个词，两边切出的 token 完全相同，
        测的就不是字段权重而是巧合的字段长度。
        （再往前一版拿真实语料断言“派发”，在同时出单字时是靠单字「派」才绿的，
        属于因错误原因通过。）
        """
        corpus = [
            _skill("zephyr", "与目标词无关的填充文字"),
            _skill("other", "zephyr 这个词只出现在描述里"),
        ]
        res = sr.rank("zephyr", corpus, n=2)
        self.assertEqual(res["items"][0]["item"]["name"], "zephyr")
        self.assertEqual(res["items"][0]["matched_in"], ["zephyr"])

    def test_empty_query_reports_why_not_silent_empty(self):
        res = sr.rank("!!!", CORPUS, n=3)
        self.assertEqual(res["items"], [])
        self.assertIn("切词后为空", res["why"])

    def test_empty_corpus_reports_why(self):
        res = sr.rank("技能", [], n=3)
        self.assertEqual(res["items"], [])
        self.assertIn("语料为空", res["why"])

    def test_no_match_reports_why_distinct_from_empty_corpus(self):
        res = sr.rank("zzzznotaskillname", CORPUS, n=3)
        self.assertEqual(res["items"], [])
        self.assertIn("没有词命中", res["why"])

    def test_short_query_flagged_when_single_token(self):
        self.assertTrue(sr.rank("端", CORPUS, n=3)["short_query"])
        self.assertFalse(sr.rank("记忆召回", CORPUS, n=3)["short_query"])

    def test_matched_terms_are_reported_for_explainability(self):
        res = sr.rank("记忆召回", CORPUS, n=2)
        row = res["items"][0]
        self.assertTrue(row["matched"])
        self.assertIn("unified-memory", [r["item"]["name"] for r in res["items"]])

    def test_n_limits_result_count(self):
        self.assertLessEqual(len(sr.rank("技能 记忆 派发 设计", CORPUS, n=2)["items"]), 2)

    def test_build_index_is_pure_and_reusable(self):
        docs, idf, avg = sr.build_index(CORPUS)
        self.assertEqual(len(docs), len(CORPUS))
        self.assertIn("unified-memory", idf)          # 名称切出的 token 进 IDF
        self.assertGreater(avg["name"], 0)


class TestJevPayload(unittest.TestCase):
    """契约照抄 `技术文档/scripts/jev.py`，改错了在这里就会红。"""

    def setUp(self):
        jev_client.reset_circuit()

    def test_payload_top_level_shape(self):
        p = jev_client._build_payload("排障", CORPUS[:2])
        self.assertEqual(set(p), {"state", "model", "questions"})
        self.assertEqual(p["model"], jev_client.JEV_MODEL)

    def test_one_question_per_candidate_keyed_s0_upwards(self):
        p = jev_client._build_payload("排障", CORPUS[:3])
        self.assertEqual(sorted(p["questions"]), ["s0", "s1", "s2"])
        for q in p["questions"].values():
            self.assertEqual(q["type"], "score")

    def test_score_criteria_must_be_a_list_not_a_dict(self):
        """实测：criteria 传 dict 会被 422 拒（`Input should be a valid list`）。"""
        q = jev_client._build_payload("x", CORPUS[:1])["questions"]["s0"]
        self.assertIsInstance(q["criteria"], list)
        self.assertEqual(q["criteria"], jev_client.SCALE)

    def test_instructions_carry_full_meaning(self):
        """题面必须自带任务描述与候选名，否则模型无从判断（题面不写代词指谁）。"""
        q = jev_client._build_payload("排查内存泄漏", CORPUS[:1])["questions"]["s0"]
        self.assertIn("排查内存泄漏", q["instructions"])
        self.assertIn(CORPUS[0]["name"], q["instructions"])


class TestJevParse(unittest.TestCase):
    def _resp(self, names, scores):
        return {"answers": {"s%d" % i: {"type": "score", "score": s, "confidence": 0.3}
                            for i, (n, s) in enumerate(zip(names, scores))}}

    def test_maps_by_key_not_by_order(self):
        """服务端没承诺返回顺序；按顺序取会静默串位。"""
        cands = [_skill("a", ""), _skill("b", "")]
        out = jev_client._parse(self._resp(["a", "b"], [0.1, 0.9]), cands)
        self.assertAlmostEqual(out["a"]["score"], 0.1)
        self.assertAlmostEqual(out["b"]["score"], 0.9)

    def test_confidence_and_legend_are_carried_through(self):
        payload = {"answers": {"s0": {"type": "score", "score": 0.5, "confidence": 0.31,
                                      "legend": {"0": "高度相关", "1": "不相关"}}}}
        out = jev_client._parse(payload, [_skill("a", "")])
        self.assertAlmostEqual(out["a"]["confidence"], 0.31)
        self.assertIn("legend", out["a"])

    def test_skips_malformed_entries_but_keeps_good_ones(self):
        payload = {"answers": {"s0": {"type": "score", "score": 0.7},
                               "s1": {"type": "choice", "choice": "a"},
                               "s2": {"type": "score", "score": "not-a-number"}}}
        out = jev_client._parse(payload, [_skill("a", ""), _skill("b", ""), _skill("c", "")])
        self.assertEqual(list(out), ["a"])

    def test_missing_answers_dict_raises(self):
        with self.assertRaises(jev_client.JevUnavailable):
            jev_client._parse({"foo": 1}, [_skill("a", "")])

    def test_no_usable_score_raises_rather_than_returning_empty(self):
        """返回 {} 会让调用方分不清「都不相关」与「jev 挂了」——那是要消灭的形态。"""
        with self.assertRaises(jev_client.JevUnavailable):
            jev_client._parse({"answers": {"s0": {"type": "score", "score": None}}},
                              [_skill("a", "")])


class TestJevCircuitAndCache(unittest.TestCase):
    def setUp(self):
        jev_client.reset_circuit()
        self._saved = os.environ.get("TYPESAFE_API_KEY")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("TYPESAFE_API_KEY", None)
        else:
            os.environ["TYPESAFE_API_KEY"] = self._saved

    def test_status_reports_disabled_without_key_and_names_the_variable(self):
        os.environ.pop("TYPESAFE_API_KEY", None)
        st = jev_client.status()
        self.assertFalse(st["ok"])
        self.assertFalse(st["enabled"])
        self.assertIn("TYPESAFE_API_KEY", st["why"])

    def test_status_ok_when_key_present_and_not_down(self):
        os.environ["TYPESAFE_API_KEY"] = "fake-key-for-l0"
        self.assertTrue(jev_client.status()["ok"])

    def test_circuit_opens_after_max_consec_fail(self):
        # 2026-10-08 修：本例原先隐式依赖**外部**环境已有 TYPESAFE_API_KEY 才能走到
        # 「已熔断」分支（同组其余 4 例都显式设了假 key，只有这例漏了）。
        # 后果：无 key 的 shell 里 status()["why"] 恒返回「未设置环境变量」那条早退分支，
        # 断言 `boom` 必假红——而 L0 号称hermetic，正是要消除这种对宿主环境的隐性依赖。
        os.environ["TYPESAFE_API_KEY"] = "fake-key-for-l0"
        for _ in range(jev_client.MAX_CONSEC_FAIL):
            jev_client._record_failure("boom")
        self.assertTrue(jev_client._S["down"])
        self.assertIn("boom", jev_client.status()["why"])

    def test_success_resets_the_failure_counter(self):
        jev_client._record_failure("boom")
        jev_client._record_failure("boom")   # 已达阈值、已熔断
        jev_client._S["down"] = False        # 人工重新合闸，模拟“尚未熔断时又成功了一次”
        jev_client._record_success()
        self.assertEqual(jev_client._S["consec_fail"], 0)
        self.assertIsNone(jev_client._S["last_error"])

    def test_no_key_short_circuits_before_any_request(self):
        import asyncio
        os.environ.pop("TYPESAFE_API_KEY", None)
        with self.assertRaises(jev_client.JevUnavailable):
            asyncio.run(jev_client.score_candidates("q", [_skill("a", "")]))

    def test_open_circuit_short_circuits_before_any_request(self):
        import asyncio
        os.environ["TYPESAFE_API_KEY"] = "fake-key-for-l0"
        for _ in range(jev_client.MAX_CONSEC_FAIL):
            jev_client._record_failure("boom")
        with self.assertRaises(jev_client.JevUnavailable):
            asyncio.run(jev_client.score_candidates("q", [_skill("a", "")]))

    def test_cache_hit_returns_without_network(self):
        import asyncio
        os.environ["TYPESAFE_API_KEY"] = "fake-key-for-l0"
        cands = [_skill("a", "")]
        key = jev_client._cache_key("q", ["a"])
        jev_client._S["cache"][key] = (1e18, {"a": {"score": 0.42, "confidence": 0.3,
                                                    "legend": None}})
        out = asyncio.run(jev_client.score_candidates("q", cands))
        self.assertTrue(out["cached"])
        self.assertAlmostEqual(out["a"]["score"], 0.42)


class TestRelevantEndpoint(unittest.TestCase):
    """端点级：tmp 技能根 + 只挂 skill.router 的裸 app ⇒ 仍然不碰真盘、不联网。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="l0rel-"))
        for s in CORPUS:
            d = self.tmp / s["name"]
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(
                "---\nname: %s\ndescription: %s\n---\n\n正文\n" % (s["name"], s["description"]),
                encoding="utf-8")
        self._dirs = dict(skill.SKILL_DIRS)
        skill.SKILL_DIRS = {"t": str(self.tmp)}
        app = FastAPI()
        app.include_router(skill.router)
        self.client = TestClient(app)
        self.addCleanup(self._restore)

    def _restore(self):
        skill.SKILL_DIRS = self._dirs
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rerank_off_returns_bm25_only_and_says_why(self):
        r = self.client.get("/api/skill/relevant", params={"q": "记忆召回", "rerank": "false"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["bm25"]["items"])
        self.assertFalse(d["jev"]["ok"])
        self.assertIn("rerank=false", d["jev"]["why"])

    def test_rows_carry_score_and_match_evidence(self):
        d = self.client.get("/api/skill/relevant",
                            params={"q": "记忆召回", "rerank": "false"}).json()
        row = d["bm25"]["items"][0]
        self.assertIn("bm25", row)
        self.assertIsInstance(row["matched"], list)
        self.assertIn("tokens_est", row)

    def test_empty_query_is_422(self):
        self.assertEqual(self.client.get("/api/skill/relevant", params={"q": ""}).status_code, 422)

    def test_unmatched_query_reports_why_and_empty_rows(self):
        d = self.client.get("/api/skill/relevant",
                            params={"q": "zzzznotaskillname", "rerank": "false"}).json()
        self.assertEqual(d["bm25"]["items"], [])
        self.assertIn("没有词命中", d["bm25"]["why"])

    def test_max_tokens_truncates_and_reports_count(self):
        d = self.client.get("/api/skill/relevant",
                            params={"q": "技能", "rerank": "false", "max_tokens": "12"}).json()
        self.assertLessEqual(d["tokens_est"], 12)
        self.assertEqual(d["max_tokens"], 12)

    def test_backends_reported_for_every_route(self):
        d = self.client.get("/api/skill/relevant",
                            params={"q": "技能", "rerank": "false"}).json()
        self.assertEqual([b["name"] for b in d["backends"]], ["t"])
        self.assertTrue(d["backends"][0]["ok"])


if __name__ == "__main__":
    unittest.main()
