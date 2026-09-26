#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L0 hermetic：memfed 联邦源适配器（v0.13.26 批1）。

分层口径（tests/tiers.py）：零宿主依赖——夹具目录/夹具库全在 tmp、不起服务、不打网络、
不 import src.main。**不允许 SKIP**。

为什么这张闸门必须存在（红向都能确定性造）：
1. **FTS5 MATCH 注入**：用户词裸传进 MATCH 会当表达式解析（语法错=假降级、拆词=命中膨胀）
   ⇒ `_fts_term` 短语包裹必须钉死。
2. **rg 行解析**：`path:line:content` 里 Windows 风盘符/冒号会造解析歧义，漏一条=丢一条溯源。
3. **脱敏出口**：rg 匹配行与 claude-mem facts 都可能带 `token=…`——少一道 mask 就是一条
   绕过 writeauth/scrub 的凭据外流路（本工作区已有三次外流前例，见 test_asset_audit.py 头注）。
4. **错误隔离**：一路源炸（夹具路径不存在/子进程超时）必须 ok=False 逐路报告，
   不得拖垮其余路——联邦的意义就是「各路死活自己说清楚」。
"""
import asyncio
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

import memfed  # noqa: E402


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="l0memfed-"))
        self._saved_targets = dict(memfed._RG_TARGETS)
        self._saved_db = memfed._CLAUDE_MEM_DB
        self._saved_cache = dict(memfed._probe_cache)
        memfed._probe_cache.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        memfed._RG_TARGETS = self._saved_targets
        memfed._CLAUDE_MEM_DB = self._saved_db
        memfed._probe_cache.clear()
        memfed._probe_cache.update(self._saved_cache)
        shutil.rmtree(self.tmp, ignore_errors=True)


# ── 注册表 ────────────────────────────────────────────────────────────

class TestRegistry(_TmpCase):
    def test_enabled_sources_present(self):
        ids = memfed.enabled_ids()
        for expect in ("claude_mem", "pi_sessions", "codex_sessions",
                       "workspace_files", "archived_sessions"):
            self.assertIn(expect, ids)

    def test_opencode_registered_but_disabled(self):
        """登记但不启用：judged 不合格的源要「可见可裁」，不是悄悄消失。"""
        src = memfed.REGISTRY["opencode_sessions"]
        self.assertFalse(src.enabled)
        self.assertEqual(src.kind, "disabled")
        self.assertNotIn("opencode_sessions", memfed.enabled_ids())

    def test_registry_fields_complete(self):
        """「整理分类」面板按注册表渲染——字段缺一个就是前端一块空。"""
        for sid, src in memfed.REGISTRY.items():
            with self.subTest(sid=sid):
                self.assertTrue(src.id and src.label and src.kind and src.desc)
                self.assertIsInstance(src.weight, float)
                self.assertGreaterEqual(src.timeout_s, 0.5)

    def test_weights_ordered_by_authority(self):
        """RRF 量纲约束：结构化观察(0.8) > 工作区成文(0.7) > 会话原始(0.5) > 归档(0.4)。"""
        w = {s.id: s.weight for s in memfed.REGISTRY.values()}
        self.assertGreater(w["claude_mem"], w["workspace_files"])
        self.assertGreater(w["workspace_files"], w["pi_sessions"])
        self.assertGreaterEqual(w["pi_sessions"], w["archived_sessions"])


# ── FTS 短语安全化 ────────────────────────────────────────────────────

class TestFtsTerm(unittest.TestCase):
    def test_plain_word(self):
        self.assertEqual(memfed._fts_term("CCR"), '"CCR"')

    def test_operators_neutralized(self):
        """`a-b OR c` 裸传会被 FTS5 当布尔表达式；包裹后必须是纯字面短语。
        连字符保留：FTS5 unicode61 分词器把它拆为相邻短语，与字面包含等价且无语法风险。"""
        self.assertEqual(memfed._fts_term("a-b OR c"), '"a-b OR c"')
        self.assertTrue(memfed._fts_term("a OR b").startswith('"'))
        self.assertTrue(memfed._fts_term("a OR b").endswith('"'))

    def test_inner_quotes_stripped(self):
        self.assertNotIn('""', memfed._fts_term('he said "hi"'))

    def test_empty_returns_harmless(self):
        self.assertEqual(memfed._fts_term('   '), '""')


# ── sqlite_fts 源（claude-mem 夹具） ───────────────────────────────────

def _make_claude_mem_db(path: pathlib.Path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE observations(id INTEGER PRIMARY KEY, title TEXT, "
                "subtitle TEXT, facts TEXT, type TEXT, created_at TEXT)")
    con.execute("CREATE VIRTUAL TABLE observations_fts USING fts5(ot)")
    con.execute("CREATE TABLE session_summaries(id INTEGER PRIMARY KEY, request TEXT, "
                "investigated TEXT, learned TEXT, created_at TEXT)")
    con.execute("CREATE VIRTUAL TABLE session_summaries_fts USING fts5(st)")
    # rowid 手工对齐夹具（生产库是 external content 同步，对本查询 JOIN 语义一致）
    con.execute("INSERT INTO observations(id,title,subtitle,facts,type,created_at) "
                "VALUES (1,'CCR 修复','探针 200','ccr_web_token=deadbeef01 修复记录',"
                "'discovery','2026-09-20T00:00:00Z')")
    con.execute("INSERT INTO observations_fts(rowid,ot) VALUES (1,'CCR 修复 探针 修复记录')")
    con.execute("INSERT INTO session_summaries(id,request,investigated,learned,created_at) "
                "VALUES (1,'修 jcode', '查了日志', '学到了', '2026-09-21T00:00:00Z')")
    con.execute("INSERT INTO session_summaries_fts(rowid,st) VALUES (1,'修 jcode 查了日志 学到了')")
    con.commit()
    con.close()


class TestClaudeMem(_TmpCase):
    def setUp(self):
        super().setUp()
        self.dbpath = self.tmp / "claude-mem.db"
        _make_claude_mem_db(self.dbpath)
        memfed._CLAUDE_MEM_DB = self.dbpath

    def test_search_hits_and_shape(self):
        r = memfed._search_claude_mem("CCR", 10)
        self.assertTrue(r["ok"])
        self.assertGreaterEqual(r["count"], 1)
        it = r["items"][0]
        self.assertEqual(it["source"], "claude_mem")
        self.assertTrue(it["id"].startswith(("obs:", "sum:")))
        self.assertIn("category", it)

    def test_summary_table_also_searched(self):
        r = memfed._search_claude_mem("jcode", 10)
        self.assertTrue(r["ok"])
        self.assertTrue(any(i["id"].startswith("sum:") for i in r["items"]))

    def test_credentials_masked_in_content(self):
        """claude-mem facts 里带 `ccr_web_token=…` 必须被 mask（含 KV 形）。"""
        r = memfed._search_claude_mem("CCR", 10)
        blob = " ".join(i["content"] for i in r["items"])
        self.assertNotIn("deadbeef01", blob)

    def test_missing_db_reports_not_ok(self):
        memfed._CLAUDE_MEM_DB = self.tmp / "nope.db"
        r = memfed._search_claude_mem("x", 5)
        self.assertFalse(r["ok"])
        self.assertIn("不存在", r["error"])

    def test_no_hit_is_ok_with_zero(self):
        r = memfed._search_claude_mem("zzz不存在的词", 5)
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 0)


# ── rg_text 源 ────────────────────────────────────────────────────────

class TestRgText(_TmpCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "sessions"
        self.root.mkdir()
        (self.root / "a.jsonl").write_text(
            '{"type":"message","content":"修 CCR 端口 3456 ?ccr_web_token=leak123"}\n'
            '其他行\n', encoding="utf-8")
        (self.root / "b.jsonl").write_text('完全无关的会话\n', encoding="utf-8")
        memfed._RG_TARGETS["pi_sessions"] = ([str(self.root)], "*.jsonl")

    def test_file_level_hit_with_lineno_id(self):
        r = memfed._search_rg_text("pi_sessions", "CCR", 10, 2.0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 1)
        it = r["items"][0]
        self.assertIn("a.jsonl", it["title"])
        self.assertIn(":", it["id"])           # path:line 溯源

    def test_token_masked_in_snippet(self):
        r = memfed._search_rg_text("pi_sessions", "CCR", 10, 2.0)
        blob = " ".join(i["content"] for i in r["items"])
        self.assertNotIn("leak123", blob)

    def test_snippet_truncated(self):
        long_line = "长词" + "x" * 5000
        (self.root / "c.jsonl").write_text(long_line + "\n", encoding="utf-8")
        r = memfed._search_rg_text("pi_sessions", "长词", 10, 2.0)
        for it in r["items"]:
            self.assertLessEqual(len(it["content"]), memfed.CONTENT_SNIPPET)

    def test_missing_root_not_ok(self):
        memfed._RG_TARGETS["pi_sessions"] = ([str(self.tmp / "gone")], "*.jsonl")
        r = memfed._search_rg_text("pi_sessions", "x", 5, 1.0)
        self.assertFalse(r["ok"])

    def test_regex_metachar_query_is_literal(self):
        """查询词带 `.` `(` 不当正则解析（-F 字面），且不炸 rc。"""
        r = memfed._search_rg_text("pi_sessions", "type\":\"message", 10, 2.0)
        self.assertTrue(r["ok"])

    def test_category_by_source_suffix(self):
        r = memfed._search_rg_text("pi_sessions", "CCR", 10, 2.0)
        self.assertEqual(r["items"][0]["category"], "session")


# ── 并发检索 / 错误隔离 ───────────────────────────────────────────────

class TestSearchFed(_TmpCase):
    def setUp(self):
        super().setUp()
        memfed._CLAUDE_MEM_DB = self.tmp / "nope.db"        # 让 claude_mem 炸
        dead = self.tmp / "dead"
        memfed._RG_TARGETS["pi_sessions"] = ([str(dead)], "*.jsonl")
        memfed._RG_TARGETS["codex_sessions"] = ([str(dead)], "*.jsonl")
        live = self.tmp / "live"
        live.mkdir()
        (live / "m.jsonl").write_text("命中关键词 CCR\n", encoding="utf-8")
        memfed._RG_TARGETS["workspace_files"] = ([str(live)], "*.md")
        memfed._RG_TARGETS["archived_sessions"] = ([str(live)], None)

    def test_one_dead_route_does_not_kill_others(self):
        want = {"claude_mem", "pi_sessions", "codex_sessions",
                "workspace_files", "archived_sessions"}
        out = asyncio.run(memfed.search_fed("CCR", 10, want))
        self.assertEqual(len(out), 5)
        self.assertFalse(out["claude_mem"]["ok"])           # db 缺失 → ok=False 有 error
        self.assertFalse(out["pi_sessions"]["ok"])
        # live 根下是 .jsonl：workspace_files(glob *.md) 查不到 → ok=True count=0；
        # archived(无 glob) 查得到 → ok=True count>0。两种「无命中/有命中」都必须 ok。
        self.assertTrue(out["workspace_files"]["ok"])
        self.assertTrue(out["archived_sessions"]["ok"])
        self.assertGreater(out["archived_sessions"]["count"], 0)

    def test_disabled_source_skipped(self):
        out = asyncio.run(memfed.search_fed("x", 5, {"opencode_sessions"}))
        self.assertEqual(out, {})

    def test_empty_want_empty_result(self):
        self.assertEqual(asyncio.run(memfed.search_fed("x", 5, set())), {})


# ── memory.py 白名单动态扩容（不 import src.main） ─────────────────────

class TestWhitelistIntegration(_TmpCase):
    def test_memory_whitelist_includes_fed_ids(self):
        import memory  # noqa: E402 —— 只拿常量，不 init db
        for sid in memfed.enabled_ids():
            self.assertIn(sid, memory.SOURCE_WHITELIST)
        self.assertIn("local", memory.SOURCE_WHITELIST)
        self.assertIn("tdai", memory.SOURCE_WHITELIST)

    def test_split_sources_accepts_fed_and_rejects_typo(self):
        from fastapi import HTTPException  # noqa: E402
        import memory  # noqa: E402
        got = memory._split_sources("local,claude_mem,pi_sessions")
        self.assertEqual(got, {"local", "claude_mem", "pi_sessions"})
        with self.assertRaises(HTTPException) as cm:
            memory._split_sources("local,bogus_src")
        self.assertEqual(cm.exception.status_code, 400)


# ── probe ─────────────────────────────────────────────────────────────

class TestProbe(_TmpCase):
    def setUp(self):
        super().setUp()
        memfed._CLAUDE_MEM_DB = self.tmp / "nope.db"
        live = self.tmp / "live"
        live.mkdir()
        (live / "x.md").write_text("a\n", encoding="utf-8")
        (live / "y.md").write_text("b\n", encoding="utf-8")
        memfed._RG_TARGETS["workspace_files"] = ([str(live)], "*.md")

    def test_probe_counts_files(self):
        r = memfed._probe_one("workspace_files")
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 2)

    def test_probe_dead_db_not_ok(self):
        r = memfed._probe_one("claude_mem")
        self.assertFalse(r["ok"])

    def test_fedsources_endpoint_lists_all_with_probe(self):
        from fastapi.testclient import TestClient  # noqa: E402
        from memfed import router  # noqa: E402
        # TestClient(router)：只挂 memfed 路由，不 import src.main（不起 vitals）
        app = __import__("fastapi").FastAPI()
        app.include_router(router)
        with TestClient(app) as c:
            resp = c.get("/api/memory/fedsources")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], len(memfed.REGISTRY))
        ids = {s["id"] for s in body["sources"]}
        self.assertIn("opencode_sessions", ids)
        self.assertIn("probe", body["sources"][0])
        # probe 结果落了缓存：同请求周期内不再重复探测
        self.assertTrue(any(k in memfed._probe_cache for k in memfed.REGISTRY))


if __name__ == "__main__":
    unittest.main(verbosity=2)
