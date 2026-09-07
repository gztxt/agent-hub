"""MCP 聚合网关（部署方案 S4：统一工具路由 + ACL + 限流）

用官方 mcp Python SDK 做客户端（不手搓协议）。聚合本机 MCP servers，
Agent 只连 hub 一个端点即可看到全部工具；敏感凭证留在 server 配置内。
- 每次调用短连接（stdio 子进程按需拉起，60s 上限）；tools 列表缓存 60s
- ACL：agent 一旦有 allow 规则则白名单语义（默认拒）；无规则的 agent 默认放行
- 限流：每主体滑动窗口计数（MCP_RATE_PER_MIN，默认 60/min）
"""
import asyncio
import fnmatch
import json
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from contextlib import AsyncExitStack
from fastapi import APIRouter, HTTPException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel

import db
import config

router = APIRouter()

RATE_PER_MIN = int(os.getenv("MCP_RATE_PER_MIN", "60"))
CALL_TIMEOUT_S = int(os.getenv("MCP_CALL_TIMEOUT_S", "60"))
LIST_TTL_S = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    transport TEXT NOT NULL DEFAULT 'stdio',   -- stdio|http
    command TEXT, args TEXT DEFAULT '[]', env TEXT DEFAULT '{}',
    url TEXT, enabled INTEGER DEFAULT 1,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mcp_acl (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,            -- 'manager' / 'jcode' / '*'
    server_id TEXT,                    -- NULL = 任意 server
    tool_pattern TEXT NOT NULL,        -- fnmatch，如 'read_*' 或 '*'
    allow INTEGER NOT NULL DEFAULT 1
);
"""

_tool_cache: Dict[str, Tuple[float, list]] = {}
_rate: Dict[str, deque] = defaultdict(deque)


def ensure_schema():
    db.execute_script(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_rate(subject: str) -> None:
    window = _rate[subject]
    cut = time.monotonic() - 60
    while window and window[0] < cut:
        window.popleft()
    if len(window) >= RATE_PER_MIN:
        raise HTTPException(429, f"MCP 限流：{subject} 超过 {RATE_PER_MIN}/min")
    window.append(time.monotonic())


def _acl_check(agent_id: Optional[str], server_id: str, tool: str) -> None:
    if not agent_id:
        return
    rows = db.query("SELECT * FROM mcp_acl WHERE agent_id IN (?, '*')", (agent_id,))
    if not rows:
        return  # 无规则 = 默认放行（首次接入零摩擦）
    for r in rows:
        if (not r["server_id"] or r["server_id"] == server_id) and \
                fnmatch.fnmatch(tool, r["tool_pattern"]):
            if r["allow"]:
                return
            raise HTTPException(403, f"ACL 拒绝 {agent_id} 调用 {server_id}/{tool}")
    raise HTTPException(403, f"ACL 白名单未含 {agent_id}:{server_id}/{tool}")


async def _session_for(server: dict) -> Tuple[AsyncExitStack, ClientSession]:
    stack = AsyncExitStack()
    if server["transport"] == "http":
        read, write, _ = await stack.enter_async_context(
            streamable_http_client(server["url"]))
    else:
        params = StdioServerParameters(
            command=server["command"],
            args=json.loads(server["args"] or "[]"),
            env={k: str(v) for k, v in json.loads(server["env"] or "{}").items()} or None)
        read, write = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return stack, session


class ServerIn(BaseModel):
    name: str
    transport: str = "stdio"
    command: Optional[str] = None
    args: Optional[list] = None
    env: Optional[dict] = None
    url: Optional[str] = None
    description: str = ""


@router.post("/mcp/servers")
async def add_server(body: ServerIn):
    if body.transport not in ("stdio", "http"):
        raise HTTPException(400, "transport 仅支持 stdio|http")
    if body.transport == "stdio" and not body.command:
        raise HTTPException(400, "stdio 需要 command")
    if body.transport == "http" and not body.url:
        raise HTTPException(400, "http 需要 url")
    sid = f"{int(time.time()*1000):x}"
    now = _now()
    db.execute(
        "INSERT INTO mcp_servers(id,name,transport,command,args,env,url,description,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (sid, body.name, body.transport, body.command,
         json.dumps(body.args or []), json.dumps(body.env or {}),
         body.url, body.description, now, now))
    return {"id": sid, "status": "added"}


@router.get("/mcp/servers")
async def list_servers():
    rows = db.query("SELECT * FROM mcp_servers ORDER BY created_at")
    for r in rows:
        r["args"] = json.loads(r["args"] or "[]")
        r["env"] = "(redacted)" if r["env"] and json.loads(r["env"]) else "{}"
    return {"servers": rows}


@router.delete("/mcp/servers/{sid}")
async def del_server(sid: str):
    if not db.execute("DELETE FROM mcp_servers WHERE id=?", (sid,)):
        raise HTTPException(404, "server not found")
    _tool_cache.clear()
    return {"status": "deleted"}


class ServerProbe(BaseModel):
    command: str
    args: list = []


@router.post("/mcp/servers/probe")
async def probe_server(body: ServerProbe):
    """临时起 stdio server 看它有哪些工具（不落库）——添加前的'预览'语义"""
    fake = {"transport": "stdio", "command": body.command,
            "args": json.dumps(body.args), "env": "{}", "url": None}
    tools = await _list_tools(fake)
    return {"tools": [{"name": t.name, "description": (t.description or "")[:120]}
                      for t in tools]}


async def _list_tools(server: dict):
    key = server["id"] if "id" in server else server["command"]
    hit = _tool_cache.get(key)
    if hit and time.monotonic() - hit[0] < LIST_TTL_S:
        return hit[1]
    async with AsyncExitStack() as outer:
        stack, session = await _session_for(server)
        outer.push_async_callback(stack.aclose)
        resp = await asyncio.wait_for(session.list_tools(), timeout=30)
        tools = resp.tools
    _tool_cache[server.get("id", key)] = (time.monotonic(), tools)
    return tools


class AclIn(BaseModel):
    agent_id: str
    tool_pattern: str
    server_id: Optional[str] = None
    allow: bool = True


@router.post("/mcp/acl")
async def add_acl(body: AclIn):
    aid = db.query("SELECT id FROM mcp_servers WHERE id=? OR name=?",
                   (body.server_id, body.server_id))[0]["id"] if body.server_id else None
    db.execute("INSERT INTO mcp_acl(agent_id,server_id,tool_pattern,allow) VALUES(?,?,?,?)",
               (body.agent_id, aid, body.tool_pattern, 1 if body.allow else 0))
    return {"status": "added"}


@router.get("/mcp/acl")
async def list_acl():
    return {"rules": db.query("SELECT * FROM mcp_acl ORDER BY id")}


@router.delete("/mcp/acl/{acl_id}")
async def del_acl(acl_id: int):
    if not db.execute("DELETE FROM mcp_acl WHERE id=?", (acl_id,)):
        raise HTTPException(404, "rule not found")
    return {"status": "deleted"}


@router.get("/mcp/tools")
async def aggregated_tools():
    out, errors = [], {}
    servers = db.query("SELECT * FROM mcp_servers WHERE enabled=1")
    for s in servers:
        try:
            tools = await _list_tools(dict(s))
            for t in tools:
                out.append({"server": s["id"], "server_name": s["name"],
                            "name": t.name,
                            "description": (t.description or "")[:160]})
        except Exception as e:  # noqa: BLE001
            errors[s["name"]] = repr(e)[:200]
    return {"tools": out, "count": len(out), "errors": errors}


class CallIn(BaseModel):
    server: str            # server id 或 name
    tool: str
    args: dict = {}
    agent_id: Optional[str] = None


@router.post("/mcp/call")
async def mcp_call(body: CallIn):
    # 确保 db 已初始化（manager 调 mcp_call 路径可能绕过 main.py init）
    try:
        db.init_db(config.Config().db_path)
    except Exception:
        pass
    _check_rate(body.agent_id or "anon")
    rows = db.query("SELECT * FROM mcp_servers WHERE enabled=1 AND (id=? OR name=?)",
                    (body.server, body.server))
    if not rows:
        raise HTTPException(404, f"MCP server 未注册: {body.server}")
    server = dict(rows[0])
    _acl_check(body.agent_id, server["id"], body.tool)
    t0 = time.monotonic()
    status = "success"
    try:
        async with AsyncExitStack() as outer:
            stack, session = await _session_for(server)
            outer.push_async_callback(stack.aclose)
            result = await asyncio.wait_for(
                session.call_tool(body.tool, body.args), timeout=CALL_TIMEOUT_S)
        texts = [c.text for c in (result.content or []) if getattr(c, "type", "") == "text"]
        is_err = bool(getattr(result, "is_error", False))
        out = {"ok": not is_err,
               "content": "\n".join(texts)[:20000],
               "is_error": is_err}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        status = "fail"
        out = {"ok": False, "error": repr(e)[:500]}
    finally:
        db.log_profile_event("mcp_call", f"{server['name']}/{body.tool}", status,
                             int((time.monotonic() - t0) * 1000),
                             detail={"agent_id": body.agent_id})
    if not out.get("ok"):
        raise HTTPException(502, json.dumps(out, ensure_ascii=False))
    return out
