"""SQLite 数据层（借鉴 Agent_Manager telemetry_store 的账本先行设计）

表：
- telemetry_events  Hook 遥测事件账本（append-only，(source,session_id,event) 覆盖式 upsert）
- memories          L1 可检索记忆（分类 fact/decision/constraint/preference）
- memory_docs       L2 近30天工作记忆 / L3 长期 Profile（整文档存储，用户可手写补充）
- chat_sessions     统一对话会话
- chat_messages     统一对话消息
- custom_agents     动态注册的自定义 Agent
- manager_messages  Manager Agent 指挥官会话（含工具步骤）
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,            -- codex|claude|qoder|workbuddy|...
    session_id TEXT NOT NULL,
    event TEXT NOT NULL,             -- session_usage|hook|...
    cwd TEXT,
    payload TEXT NOT NULL,           -- 原始 JSON
    usage_scope TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(source, session_id, event)
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layer TEXT NOT NULL DEFAULT 'L1',
    category TEXT NOT NULL DEFAULT 'fact',  -- fact|decision|constraint|preference
    content TEXT NOT NULL,
    source TEXT,                     -- 来源 agent / manual
    session_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',  -- active|superseded|deleted
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_docs (
    layer TEXT PRIMARY KEY,          -- L2|L3
    content TEXT NOT NULL,
    manual TEXT NOT NULL DEFAULT '', -- 用户手写补充（独立保存，清洗不触碰）
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,              -- user|assistant|tool|error
    content TEXT NOT NULL,
    meta TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS custom_agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'custom',
    command TEXT,
    args TEXT,                       -- JSON array
    working_dir TEXT,
    env TEXT,                        -- JSON object
    port INTEGER,
    endpoint TEXT,
    description TEXT DEFAULT '',
    auto_restart INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manager_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,              -- user|assistant
    content TEXT NOT NULL,
    steps TEXT,                      -- JSON: thought/toolcall/toolresult/answer
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_mgr_session ON manager_messages(session_id);
"""


def init_db(path: Path) -> None:
    global _conn
    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    with _lock:
        _conn.executescript(SCHEMA)
        _conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        cur = _conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.rowcount


def upsert_telemetry(source: str, session_id: str, event: str,
                     cwd: Optional[str], payload: dict, usage_scope: Optional[str]) -> None:
    """同一 (source,session_id,event) 重报覆盖而非叠加（Agent_Manager 口径）"""
    execute(
        """INSERT INTO telemetry_events(source,session_id,event,cwd,payload,usage_scope,created_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(source,session_id,event) DO UPDATE SET
             cwd=excluded.cwd, payload=excluded.payload,
             usage_scope=excluded.usage_scope, created_at=excluded.created_at""",
        (source, session_id, event, cwd, json.dumps(payload, ensure_ascii=False),
         usage_scope, _now()))


def add_memory(content: str, category: str = "fact", source: str = "manual",
               session_id: Optional[str] = None, layer: str = "L1") -> int:
    now = _now()
    with _lock:
        cur = _conn.execute(
            "INSERT INTO memories(layer,category,content,source,session_id,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (layer, category, content, source, session_id, now, now))
        _conn.commit()
        return cur.lastrowid
