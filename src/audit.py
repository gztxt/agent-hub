"""资产变更审计的**只读查询**门面。

为什么单独一个模块而不是塞进 db.py：db.py 是最底层（被所有模块 import），放路由进去
会让存储层长出 HTTP 依赖。读写分居两层的口径是——
  写：`db.log_asset_event()` 是唯一入口（append-only，落库前脱敏）；
  读：本模块的 `GET /api/audit/list`。

鉴权口径照抄 `/api/sessions/export`（src/main.py:531）的既有先例：**GET 但按写方法判**。
理由是审计行含 actor 与变更细节，批量读它＝数据外流动作，与导出同级；且服务绑
0.0.0.0:3102，不按写判就等于把变更史对整个局域网敞开。
fail-closed：服务端没配口令 ⇒ 503 而不是放行（与 writeauth.decide 同一口径）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

import db
import writeauth

router = APIRouter()

#: 允许的 asset_type —— 与 db.log_asset_event 各调用点用的字面量一一对应。
#: 写死成白名单而不是放开自由文本：查询侧要能枚举，否则前端做不出下拉。
VALID_TYPES = ("mcp_server", "mcp_acl", "memory_l1", "memory_doc",
               "agent", "session", "setting", "skill", "repo")

_LIMIT_MAX = 1000


@router.get("/api/audit/list")
async def audit_list(request: Request,
                     asset_type: Optional[str] = Query(default=None, max_length=40),
                     asset_slug: Optional[str] = Query(default=None, max_length=200),
                     limit: int = Query(default=100, le=_LIMIT_MAX)):
    verdict, reason = writeauth.decide(
        "POST", request.url.path,                       # 强制按写方法判：批量读审计＝数据外流
        writeauth.provided_token(request.headers.raw, request.url.query),
        writeauth.secrets_from_env())
    if verdict not in ("allow", "exempt"):
        # 只打 verdict/path/来源，绝不打凭据（与 main.py 的 [export] 日志同一口径）
        print(f"[audit] 拒绝 {verdict}：{request.url.path} "
              f"来源={request.client.host if request.client else '?'} —— {reason}", flush=True)
        raise HTTPException(status_code=503 if verdict == "misconfig" else 401, detail=reason)

    if asset_type and asset_type not in VALID_TYPES:
        raise HTTPException(400, f"asset_type must be one of {list(VALID_TYPES)}")

    sql = "SELECT id,asset_type,asset_slug,action,actor,detail,created_at FROM asset_audit"
    conds, params = [], []
    if asset_type:
        conds.append("asset_type=?")
        params.append(asset_type)
    if asset_slug:
        conds.append("asset_slug=?")
        params.append(str(asset_slug))
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit or 100), _LIMIT_MAX)))
    rows = db.query(sql, tuple(params))
    return {"audit": rows, "count": len(rows), "types": list(VALID_TYPES)}
