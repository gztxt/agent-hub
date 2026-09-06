"""Hook 遥测路由（Agent_Manager agent_http.rs 语义移植）

端点：
  POST /telemetry/events/{source}   外部 Agent 推送事件（先落盘账本，异步处理不丢）
  GET  /telemetry/events            查询账本
  GET  /telemetry/usage/summary     Token 用量聚合（同 session 覆盖式口径）

鉴权：设了 HOOK_AUTH_TOKEN 则必须 Bearer 携带；未设则仅允许回环来源。
"""
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

import db

router = APIRouter()

ALLOWED_SOURCES = {"codex", "claude", "qoder", "workbuddy", "minimax", "kimi",
                   "pi", "jcode", "custom"}


class TelemetryEvent(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    event: str = Field(min_length=1, max_length=100)   # session_usage / hook / ...
    cwd: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None             # input/output/cached tokens
    usage_scope: Optional[str] = None                  # session | turn
    data: Optional[Dict[str, Any]] = None              # 其它负载原样入档


def _check_auth(request: Request) -> None:
    token = os.getenv("HOOK_AUTH_TOKEN", "")
    if token:
        got = request.headers.get("authorization", "")
        if got != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="invalid hook token")
        return
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="hook writes allowed from loopback only")


@router.post("/telemetry/events/{source}")
async def push_event(source: str, body: TelemetryEvent, request: Request):
    if source not in ALLOWED_SOURCES:
        # 与 Agent_Manager 一致：只收已知 adapter，但留 custom 前缀扩展
        if not source.startswith("custom"):
            raise HTTPException(status_code=400,
                                detail=f"unknown source {source}; allowed: {sorted(ALLOWED_SOURCES)}|custom*")
    payload: Dict[str, Any] = {}
    if body.usage is not None:
        # 口径校验（telemetry_store.rs 规则）：input 必须为全量，cached 仅作明细
        usage = body.usage
        for k in ("input_tokens", "output_tokens", "cached_tokens"):
            v = usage.get(k)
            if v is not None and (not isinstance(v, int) or v < 0):
                raise HTTPException(status_code=400, detail=f"usage.{k} must be a non-negative int")
        payload["usage"] = {k: v for k, v in usage.items()
                            if k in ("input_tokens", "output_tokens", "cached_tokens",
                                     "reasoning_tokens", "total_cost")}
    if body.data:
        payload["data"] = body.data
    db.upsert_telemetry(source, body.session_id, body.event, body.cwd,
                        payload, body.usage_scope)
    return {"status": "recorded", "source": source, "session_id": body.session_id,
            "event": body.event, "ts": datetime.now(timezone.utc).isoformat()}


@router.get("/telemetry/events")
async def list_events(source: Optional[str] = None, session_id: Optional[str] = None,
                      limit: int = Query(default=50, le=500)):
    sql = "SELECT * FROM telemetry_events"
    params: list = []
    conds = []
    if source:
        conds.append("source=?")
        params.append(source)
    if session_id:
        conds.append("session_id=?")
        params.append(session_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, tuple(params))
    return {"events": rows, "count": len(rows)}


@router.get("/telemetry/usage/summary")
async def usage_summary():
    """每 (source,session) 取最后一行 session_usage —— 覆盖而非叠加"""
    rows = db.query("""
        SELECT source, session_id, cwd, payload, usage_scope, created_at
        FROM telemetry_events te
        WHERE event='session_usage' AND id = (
            SELECT id FROM telemetry_events
            WHERE source=te.source AND session_id=te.session_id AND event='session_usage'
            ORDER BY id DESC LIMIT 1)
    """)
    import json as _json
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        usage = (_json.loads(r["payload"]) or {}).get("usage") or {}
        a = agg.setdefault(r["source"], {"sessions": 0, "input_tokens": 0,
                                         "output_tokens": 0, "cached_tokens": 0})
        a["sessions"] += 1
        for k in ("input_tokens", "output_tokens", "cached_tokens"):
            a[k] += int(usage.get(k) or 0)
    total = {k: sum(a[k] for a in agg.values())
             for k in ("sessions", "input_tokens", "output_tokens", "cached_tokens")}
    return {"by_source": agg, "total": total}
