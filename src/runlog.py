"""运行日志（v0.13.27）：三中心检索调用的留痕 + 可翻页查询门面。

设计口径（为什么这么小）：
- **零新表**：复用 profile_events（detail 是 JSON TEXT 列已存在），source='rest'。
  既有 source 枚举（hub_chat/task_exec/cron_run/mcp_call）记的是「agent 执行画像」，
  本模块记「三中心被谁调用、多快、哪路降级」——同为 append-only 事件流，口径兼容。
- **埋点是附加价值，不是准入条件**：_fire 整体包 try，DB 炸了绝不打断检索请求
  （但会 print 一行，不静默——本仓纪律）。
- **MCP 通道自动覆盖**：hubmcp 的 9 个工具经 _get() 回环转调 REST ⇒ 在 REST 端点
  埋点即同时覆盖 MCP 通道。区分靠 x-hub-channel 头（hubmcp._get 发起时自带）；
  头可伪造，但这是遥测不是鉴权，误标只产生无害噪声。
- **不埋的点**（防噪声，写死在这里别「顺手加」）：/api/agents、/api/ports、/health
  ——前端 30s 轮询，埋了等于把画像面板淹掉；/api/memory/l1*（管理面非检索面）。

权限：GET /api/runlog **按写方法判**（writeauth.decide("POST", ...)）——运行日志
含查询词可反推用户意图，属敏感面，与 /api/audit/list 同口径（src/audit.py 先例）。
fail-closed：服务端没配口令 ⇒ 503 而不是放行。
"""
from __future__ import annotations

import functools
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request

import db
import tdai_client
import writeauth

router = APIRouter()

#: 本模块管理的 subject 枚举——与 track() 调用点一一对应。查询侧可枚举才做得出下拉。
SUBJECTS = ("mem.search", "mem.context", "kb.search", "kb.browse",
            "kb.status", "skill.list", "skill.read", "cc.start")

#: 埋点 source（与 hub_chat/task_exec/cron_run/mcp_call 并列）。hook.py 的画像聚合
#: 会排除它，防止高频检索事件把 agent 画像挤出前 50（见批1 连带项）。
SOURCE = "rest"

_Q_KEYS = ("q", "name", "sub")            # 从 handler kwargs 提取查询词
_LIMIT_KEYS = ("limit", "k")
_ROUTE_KEYS = ("routes", "sources")
_Q_MAX_CHARS = 120                         # 查询词落库上限（防长文入库膨胀 detail）


def _pick_query(kwargs: Dict[str, Any]) -> str:
    for k in _Q_KEYS:
        v = kwargs.get(k)
        if v:
            return str(v)[:_Q_MAX_CHARS]
    return ""


def _pick_limit(kwargs: Dict[str, Any]) -> Optional[int]:
    for k in _LIMIT_KEYS:
        v = kwargs.get(k)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return None


def _pick_routes(kwargs: Dict[str, Any]) -> str:
    for k in _ROUTE_KEYS:
        v = kwargs.get(k)
        if v:
            return str(v)[:_Q_MAX_CHARS]
    return ""


def _channel_of(request: Any) -> str:
    try:
        h = request.headers.get("x-hub-channel", "")
        return "mcp" if h == "mcp" else "web"
    except Exception:  # noqa: BLE001 —— 桩请求/异常头都按 web 记，埋点不许抛
        return "web"


def _backend_routes(data: Any) -> Dict[str, Any]:
    """从返回载荷泛取逐路健康（memory/kb/skill 三处 backends 形状已确认同构）。

    拿不到就返回空 dict——形状漂移时埋点降级为只记 count，绝不抛。
    """
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Any] = {}
    try:
        backends = data.get("backends") or []
        ok = [b.get("name") for b in backends if isinstance(b, dict) and b.get("ok")]
        out["routes_ok"] = [x for x in ok if x]
        degraded = [b.get("name") for b in backends if isinstance(b, dict) and not b.get("ok")]
        out["degraded"] = [x for x in degraded if x]
        if data.get("count") is not None:
            out["count"] = data.get("count")
    except Exception:  # noqa: BLE001
        return {}
    return out


def _fire(subject: str, status: str, duration_ms: int, detail: Dict[str, Any]) -> None:
    """唯一落库出口。整体包 try：埋点失败绝不打断业务请求，但 print 不静默。"""
    try:
        db.log_profile_event(SOURCE, subject, status, duration_ms, detail=detail)
    except Exception as e:  # noqa: BLE001
        print(f"[runlog] 埋点失败(不影响业务)：{subject} {type(e).__name__}: {e}", flush=True)


def track(subject: str):
    """装饰器：包住三中心只读检索端点，记 q/limit/routes/channel/逐路健康/耗时。

    用装饰器而不是逐点手写：6+1 个端点形状同构（kwargs 进 dict 出），散写必漏；
    漏一个不是「少一条日志」，是「通道盲区」——和 writeauth 做中间件的理由同型。
    """
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            t0 = time.monotonic()
            # FastAPI 以 kwargs 注入 request；但直调/测试可能走位置参数——
            # 两处都找一遍，找到第一个就叫 request（签名里 request 永远是首个参数）。
            request = kwargs.get("request")
            if request is None and args:
                request = args[0]
            try:
                data = await fn(*args, **kwargs)
                _fire(subject, "success", int((time.monotonic() - t0) * 1000), {
                    **_backend_routes(data),
                    "q": tdai_client.scrub(_pick_query(kwargs)),
                    "limit": _pick_limit(kwargs),
                    "routes": _pick_routes(kwargs),
                    "channel": _channel_of(request),
                })
                return data
            except HTTPException as e:
                # 失败路径同样留痕（查询词 scrub 后截 200），然后原样 re-raise——
                # HTTP 语义（404/409/400 带诊断正文）一字不动。
                _fire(subject, "fail", int((time.monotonic() - t0) * 1000), {
                    "q": tdai_client.scrub(_pick_query(kwargs)),
                    "limit": _pick_limit(kwargs),
                    "routes": _pick_routes(kwargs),
                    "channel": _channel_of(request),
                    "http": e.status_code,
                    "err": tdai_client.scrub(str(e.detail))[:200],
                })
                raise
        return wrapper
    return deco


@router.get("/api/runlog")
async def runlog_query(request: Request,
                       source: Optional[str] = Query(default=None, max_length=40),
                       subject: Optional[str] = Query(default=None, max_length=40),
                       status: Optional[str] = Query(default=None, max_length=20),
                       window: int = Query(default=0, ge=0, le=720),
                       limit: int = Query(default=100, ge=1, le=500),
                       before_id: Optional[int] = Query(default=None, ge=1)):
    """运行日志查询：source/subject/status 过滤 + 时间窗 + id 游标翻页。

    照抄 /api/audit/list 的鉴权先例（GET 但按写方法判）：运行日志含查询词，
    批量读它＝窥探本机使用史，与导出同级敏感。
    """
    verdict, reason = writeauth.decide(
        "POST", request.url.path,
        writeauth.provided_token(request.headers.raw, request.url.query),
        writeauth.secrets_from_env())
    if verdict not in ("allow", "exempt"):
        # 只打 verdict/path/来源，绝不打凭据（与 audit.py 同一口径）
        print(f"[runlog] 拒绝 {verdict}：{request.url.path} "
              f"来源={request.client.host if request.client else '?'} —— {reason}", flush=True)
        raise HTTPException(status_code=503 if verdict == "misconfig" else 401, detail=reason)

    sql = ("SELECT id,source,subject,trace_id,status,duration_ms,detail,created_at"
           " FROM profile_events")
    conds, params = [], []

    if source:
        conds.append("source=?")
        params.append(source)
    if subject:
        if subject not in SUBJECTS:
            raise HTTPException(400, f"subject must be one of {list(SUBJECTS)}")
        conds.append("subject=?")
        params.append(subject)
    if status:
        if status not in ("success", "fail"):
            raise HTTPException(400, "status must be success|fail")
        conds.append("status=?")
        params.append(status)
    if window > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window)).isoformat()
        conds.append("created_at>=?")
        params.append(cutoff)
    if before_id:
        # append-only 表用 id<? 游标翻页，不用 OFFSET（越翻越慢）
        conds.append("id<?")
        params.append(before_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = db.query(sql, tuple(params))
    next_before_id = rows[-1]["id"] if len(rows) == limit else None
    return {
        "events": rows,
        "count": len(rows),
        "next_before_id": next_before_id,
        "sources": ["rest", "hub_chat", "task_exec", "cron_run", "mcp_call"],
        "subjects": list(SUBJECTS),
    }
