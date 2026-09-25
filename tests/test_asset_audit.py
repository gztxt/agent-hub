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


if __name__ == "__main__":
    unittest.main(verbosity=2)
