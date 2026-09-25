#!/usr/bin/env python3
"""L0 hermetic：资产变更审计（asset_audit）的表、写口径与覆盖面护栏。

分层口径（tests/tiers.py）：零宿主依赖 —— db 指向 tmp 文件库、不起服务、不打网络、
不 import src.main、**不允许 SKIP**。

为什么这张表值得单独一层闸门（三条都能确定性造红向）：
1. **审计表一旦可被 UPDATE/DELETE 就不再是审计** ⇒ 静态钉死写口径只有 INSERT。
2. **审计行会成为下一次会话导出的正文** ⇒ detail 落库前必须过脱敏，否则凭据二次外流
   （本工作区已有三次外流前例：备份镜像 82 个活凭据、wiki/log.md 历史含 CCR token、外发净仓 3 份抄真 token）。
3. **漏一条写点就等于没做**（与 writeauth.py 的"34 条写路由集中一处"同一口径）⇒ 用 AST 扫覆盖面。
"""
import ast
import asyncio
import json
import pathlib
import shutil
import sys
import tempfile
import types
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

import db  # noqa: E402


class _DbCase(unittest.TestCase):
    """把 db 换到 tmp 文件库；cleanup 必须**关掉再还原**全局连接（照抄 tests/test_mcp_registry.py）。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="l0audit-"))
        self._saved_conn = db._conn
        db.init_db(self.tmp / "t.db")
        self.addCleanup(self._restore)

    def _restore(self):
        try:
            db._conn.close()
        except Exception:  # noqa: BLE001
            pass
        db._conn = self._saved_conn
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSchema(_DbCase):
    def test_table_and_indexes_created(self):
        rows = db.query("SELECT name FROM sqlite_master WHERE name IN "
                        "('asset_audit','idx_audit_asset','idx_audit_created')")
        self.assertEqual(sorted(r["name"] for r in rows),
                         ["asset_audit", "idx_audit_asset", "idx_audit_created"])

    def test_columns_are_exact(self):
        cols = [r["name"] for r in db.query("PRAGMA table_info(asset_audit)")]
        self.assertEqual(cols, ["id", "asset_type", "asset_slug", "action",
                                "actor", "detail", "created_at"])


class TestWritePath(_DbCase):
    def test_helper_is_append_only(self):
        """★ 红向钉子：写口径里出现 UPDATE/DELETE 即判红（审计表可改 = 不是审计）。"""
        src = (_REPO / "src" / "db.py").read_text(encoding="utf-8")
        i = src.index("def log_asset_event(")
        j = src.index("\ndef ", i + 1)
        body = src[i:j]
        self.assertIn("INSERT INTO asset_audit", body)
        self.assertNotIn("UPDATE asset_audit", body)
        self.assertNotIn("DELETE FROM asset_audit", body)

    def test_row_is_written_and_readable(self):
        db.log_asset_event("mcp_server", "abc123", "create", "user:term-token",
                           {"name": "demo", "transport": "stdio"})
        rows = db.query("SELECT * FROM asset_audit")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["asset_type"], r["asset_slug"], r["action"], r["actor"]),
                         ("mcp_server", "abc123", "create", "user:term-token"))
        self.assertEqual(json.loads(r["detail"])["name"], "demo")
        self.assertTrue(r["created_at"])

    def test_detail_redacts_credentials(self):
        """凭据形态进 detail 必须被打码（复用 sessions_export 的同一套 pattern，含嵌套层）。"""
        db.log_asset_event("setting", "term-token", "update", "user:hub-passcode",
                           {"raw": "sk-ABCDEFGHIJKLMNOP1234567890",
                            "nested": {"k": "ghp_" + "A" * 36}})
        d = db.query("SELECT detail FROM asset_audit")[0]["detail"]
        self.assertNotIn("sk-ABCDEFGHIJKLMNOP1234567890", d)
        self.assertNotIn("ghp_" + "A" * 36, d)
        self.assertIn("<REDACTED", d)

    def test_unknown_action_is_flagged_not_rewritten_not_dropped(self):
        """不在枚举里的 action：**不静默丢弃、不静默改写**，只打标记。
        丢事件比记错更贵（审计的价值在完整），改写则毁掉取证原文。"""
        db.log_asset_event("mcp_acl", "7", "frobnicate", "system")
        rows = db.query("SELECT action, detail FROM asset_audit")
        self.assertEqual(len(rows), 1, "事件被静默丢弃 = 审计漏洞")
        self.assertEqual(rows[0]["action"], "frobnicate", "action 被改写 = 毁掉取证原文")
        self.assertTrue(json.loads(rows[0]["detail"]).get("action_invalid"))

    def test_none_detail_becomes_empty_object(self):
        db.log_asset_event("agent", "pi", "delete", "system")
        self.assertEqual(db.query("SELECT detail FROM asset_audit")[0]["detail"], "{}")

    def test_actions_enum_is_frozen(self):
        self.assertEqual(db.AUDIT_ACTIONS,
                         ("create", "update", "delete", "bind", "unbind", "rebuild"))

    def test_db_path_canary_stays_on_tmp(self):
        """库指向金丝雀（09-24 曾把测试写入落进生产库）：审计只准落 tmp 库。"""
        self.assertIn("l0audit-", db.current_path())


def route_audit_map(module_path):
    """AST 扫一个模块：{(METHOD, path): (是否打了审计, 函数名)}。

    为什么用 AST 而不是 grep：grep 只能证明"文件里某处有这个词"，证明不了
    "这条路由的 handler 体内有"。漏一条写点就等于没做（writeauth 同一口径）。

    **间接层只跟一层**（2026-09-25 实测修正）：memory 的 put_l2/put_l3 把审计收进了
    模块级 helper `_audit_doc()`（避免把 touched 字段推导复制两遍），第一版护栏只看 handler 体
    ⇒ 对着正确实现报"漏审"。解法不是把逻辑抄回 handler，而是让护栏解析一层本地调用：
    handler 体内直接出现 log_asset_event，**或**调用了某个「体内直接出现 log_asset_event」的
    模块级函数，才算已审。再深的链条（helper→helper→审计）故意不算 —— 否则覆盖面可以被
    无限稀释成"看起来调了个函数"（见 test_indirection_is_limited_to_one_level）。
    """
    src = pathlib.Path(module_path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()

    def seg(node):
        return "\n".join(lines[node.lineno - 1:getattr(node, "end_lineno", node.lineno)])

    #: 模块级函数名 → 其体内是否**直接**调 log_asset_event（只扫 tree.body，不递归）
    helpers = {n.name: ("log_asset_event" in seg(n))
               for n in tree.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))}
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if not (isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router"):
                continue
            method = dec.func.attr.upper()
            if method not in ("POST", "PUT", "PATCH", "DELETE"):
                continue
            path = dec.args[0].value if (dec.args and isinstance(dec.args[0], ast.Constant)) else "?"
            body = seg(node)
            called = {c.func.id for c in ast.walk(node)
                      if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            audited = ("log_asset_event" in body) or any(helpers.get(c) for c in called)
            out[(method, path)] = (audited, node.name)
    return out


#: 必须审计的变更点。不在此表里的写路由**故意不审**，理由挂在 NOT_AUDITED。
MUST_AUDIT = {
    "mcpgw": {("POST", "/mcp/servers"), ("DELETE", "/mcp/servers/{sid}"),
              ("POST", "/mcp/acl"), ("DELETE", "/mcp/acl/{acl_id}")},
    "memory": {("POST", "/api/memory/l1"), ("POST", "/api/memory/l1/batch"),
               ("DELETE", "/api/memory/l1/{mid}"), ("PUT", "/api/memory/l2"),
               ("PUT", "/api/memory/l3"), ("POST", "/api/memory/l2/rebuild")},
}

#: 故意不审 + 理由（防止"以后有人加一条变更路由却没人发现漏审"）
NOT_AUDITED = {
    ("POST", "/mcp/servers/probe"): "预览语义，明确不落库（handler docstring 已写）",
    ("POST", "/mcp/call"): "工具**调用**不是资产变更；成败/耗时已由 profile_events 记",
}


class TestCoverage(unittest.TestCase):
    """★ 本闸门的核心：漏一条写点即 FAIL（"漏一条就等于没做"）。"""

    def _map(self, mod):
        return route_audit_map(_REPO / "src" / ("%s.py" % mod))

    def test_mcpgw_change_routes_all_audited(self):
        m = self._map("mcpgw")
        self.assertEqual([k for k in MUST_AUDIT["mcpgw"] if not m.get(k, (False,))[0]], [],
                         "mcpgw 漏审")

    def test_memory_change_routes_all_audited(self):
        m = self._map("memory")
        self.assertEqual([k for k in MUST_AUDIT["memory"] if not m.get(k, (False,))[0]], [],
                         "memory 漏审")

    def test_unaudited_routes_have_written_reasons(self):
        """反向钉子：不审的路由必须挂着理由，否则新增变更路由会静默漏审。"""
        for mod in ("mcpgw", "memory"):
            for k, audited in self._map(mod).items():
                if not audited[0]:
                    self.assertIn(k, NOT_AUDITED,
                                  "%s 出现未审计且无理由的写路由 %s（handler=%s）"
                                  % (mod, k, audited[1]))

    def test_no_env_values_reach_audit(self):
        """★ mcp_servers.env 装的是凭据 ⇒ 审计只准记 has_env 布尔，绝不记值。

        判法必须区分「记布尔」与「记值」：`bool(body.env)` 是安全形态，
        裸 `body.env` / `json.dumps(body.env…)` 才是漏值。第一版闸门把两者一视同仁
        ⇒ 对着正确实现报红（闸门精度缺陷，2026-09-25 实测修正）。
        做法：先摘掉安全形态，余下文本里再出现 body.env 就是漏。
        """
        src = (_REPO / "src" / "mcpgw.py").read_text(encoding="utf-8")
        i = src.index("async def add_server(")
        body = src[i:src.index("\n@router.", i)]
        self.assertIn('"has_env": bool(body.env)', body, "必须只记布尔，不记值")
        audit_seg = body.split("log_asset_event")[-1]
        self.assertNotIn("body.env", audit_seg.replace("bool(body.env)", ""),
                         "env 值进了审计 detail = 凭据落进可导出的正文")
        self.assertNotIn("json.dumps(body.env", audit_seg)

    def test_indirection_is_limited_to_one_level(self):
        """★ 审计只准藏一层：handler→helper→helper→log_asset_event **不算已审**。
        没有这条，覆盖面护栏可以被无限稀释成"看起来调了个函数"。"""
        d = pathlib.Path(tempfile.mkdtemp(prefix="l0cov-"))
        self.addCleanup(shutil.rmtree, d, True)
        f = d / "m.py"
        f.write_text(
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "def _deep():\n"
            "    db.log_asset_event('x', 'y', 'create', 'system')\n"
            "def _mid():\n"
            "    _deep()\n"
            "@router.post('/shallow')\n"
            "async def h_shallow():\n"
            "    _audit_one()\n"
            "def _audit_one():\n"
            "    db.log_asset_event('x', 'y', 'create', 'system')\n"
            "@router.post('/deep')\n"
            "async def h_deep():\n"
            "    _mid()\n", encoding="utf-8")
        m = route_audit_map(f)
        self.assertTrue(m[("POST", "/shallow")][0], "一层本地 helper 该算已审")
        self.assertFalse(m[("POST", "/deep")][0], "两层间接必须判未审（否则护栏可被稀释）")


class TestMemoryAudit(_DbCase):
    """记忆资产的审计行形态。两条口径钉子：
       ① detail **不记正文**（正文已在 memories 表；重复记 = 体积翻倍且多一处凭据面），只记元信息与字符数；
       ② 软删必须如实标 soft=True（delete_l1 是 UPDATE status='deleted'，不是物理删）——
          审计里写 "delete" 而不说清是软删，就是"不静默改数据"的反面。"""

    def setUp(self):
        super().setUp()
        import memory as memory_mod
        self.m = memory_mod
        self.req = types.SimpleNamespace(state=types.SimpleNamespace(actor="user:hub-passcode"))

    def _l1(self, content="端口 3102 由 agent-hub 占用", category="fact"):
        return asyncio.run(self.m.create_l1(
            self.m.MemoryIn(content=content, category=category), self.req))

    def test_create_l1_audits_metadata_not_content(self):
        r = self._l1()
        rows = db.query("SELECT * FROM asset_audit WHERE asset_type='memory_l1'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "create")
        self.assertEqual(rows[0]["asset_slug"], str(r["id"]))
        self.assertEqual(rows[0]["actor"], "user:hub-passcode")
        d = json.loads(rows[0]["detail"])
        self.assertEqual(d["category"], "fact")
        self.assertIn("chars", d)
        self.assertNotIn("content", d, "正文不该复制进审计")
        self.assertNotIn("端口 3102", rows[0]["detail"])

    def test_batch_writes_one_summary_row(self):
        """批量导入记**一行汇总**：逐条写 200 行会让单次调用灌满表，
        而审计要回答的是"谁在什么时候导了多少条"。"""
        items = [self.m.MemoryIn(content="条目 %d" % i, category="fact") for i in range(5)]
        r = asyncio.run(self.m.create_l1_batch(self.m.MemoryBatchIn(items=items), self.req))
        rows = db.query("SELECT * FROM asset_audit WHERE asset_slug='batch'")
        self.assertEqual(len(rows), 1)
        d = json.loads(rows[0]["detail"])
        self.assertEqual(d["count"], 5)
        self.assertEqual(d["ids"], r["ids"])

    def test_delete_l1_marks_soft_delete(self):
        mid = self._l1()["id"]
        asyncio.run(self.m.delete_l1(mid, self.req))
        rows = db.query("SELECT detail FROM asset_audit WHERE asset_slug=? AND action='delete'",
                        (str(mid),))
        self.assertEqual(len(rows), 1)
        self.assertTrue(json.loads(rows[0]["detail"]).get("soft"),
                        "软删未标注 ⇒ 读审计的人会以为数据被物理删除")

    def test_put_l2_records_which_field_changed(self):
        asyncio.run(self.m.put_l2(self.m.DocIn(content="# 新 L2", manual=None), self.req))
        d = json.loads(db.query("SELECT detail FROM asset_audit WHERE asset_slug='L2'")[0]["detail"])
        self.assertEqual(d["touched"], ["content"])
        self.assertNotIn("新 L2", json.dumps(d, ensure_ascii=False))

    def test_put_l3_records_manual_touch(self):
        """manual 是用户手写补充（09-23 曾被 rebuild 静默覆盖）⇒ 它被动过必须单独留痕。"""
        asyncio.run(self.m.put_l3(self.m.DocIn(content=None, manual="用户手写"), self.req))
        d = json.loads(db.query("SELECT detail FROM asset_audit WHERE asset_slug='L3'")[0]["detail"])
        self.assertEqual(d["touched"], ["manual"])
        self.assertEqual(d["manual_chars"], len("用户手写"))

    def test_rebuild_l2_audited_with_manual_untouched(self):
        for i in range(2):
            self._l1(content="记忆 %d" % i)
        try:
            asyncio.run(self.m.rebuild_l2(self.req))
        except Exception as e:                       # noqa: BLE001
            if "近30天无 L1 记忆可压缩" not in str(e):
                raise
        rows = db.query("SELECT detail FROM asset_audit WHERE action='rebuild'")
        self.assertEqual(len(rows), 1)
        d = json.loads(rows[0]["detail"])
        self.assertTrue(d.get("manual_untouched"))
        self.assertIn("items", d)
        self.assertIn("llm", d)

class TestGateSelfCheck(unittest.TestCase):
    """★ 元闸门：本文件里每个 test_* 都必须是某个 TestCase 的**方法**。

    缩进错位会让 test 函数变成另一个函数的嵌套 def ⇒ 语法通过、import 成功、
     discover 收不到、**永远不执行**，而总例数只少一个，肉眼极难发现
    （2026-09-25 本文件实测栽过一次；同族前例＝vitals_loop 函数头丢失致健康检查
    成为不可达死代码、前端 TDZ 声明前访问）。口径：**代码存在 ≠ 会被执行**。
    """

    def test_no_test_function_is_nested_inside_another_function(self):
        src = pathlib.Path(__file__).read_text(encoding="utf-8")
        nested = []
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(node):
                if (sub is not node and isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and sub.name.startswith("test_")):
                    nested.append("%s 嵌在 %s 里" % (sub.name, node.name))
        self.assertEqual(sorted(set(nested)), [], "这些 test_ 永不执行：%s" % sorted(set(nested)))

    def test_intended_gate_count_is_not_silently_shrunk(self):
        """例数下界钉子：少了就说明有 test 掉出收集范围（不是"跑得快"，是"没跑"）。"""
        loader = unittest.TestLoader()
        n = loader.loadTestsFromModule(sys.modules[__name__]).countTestCases()
        self.assertGreaterEqual(n, 21, "本文件应至少收集 21 例，实收 %d ⇒ 有 test 掉出收集范围" % n)

if __name__ == "__main__":
    unittest.main(verbosity=2)
