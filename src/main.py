"""Agent Hub - 主入口（Agent_Manager 融合版 v0.3.0）

融合自 Zafer-Liu/Agent_Manager (Apache-2.0) 的设计与语义：
- Hook 遥测端点（agent_http.rs → src/hook.py）
- 三层记忆中心（memory 子系统 → src/memory.py）
- 端口管理（ports.rs → src/ports.py，只读，无 kill —— NAS 军规）
- 项目类型自动识别（agent_sources.rs → src/sources.py）
"""
import asyncio
import hmac
import json
import os
import re
import sys
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

# 必须在导入 config 前加载 .env
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print(f"[Agent Hub] 已加载 .env: {env_path}")

import aiohttp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
import db
from config import config
from discovery import AgentDiscovery, AgentInfo
from ports import list_listeners, port_in_use
from sources import detect_project
import scanner
from registry import build_adapters, get_adapter
import hook as hook_mod
import memory as memory_mod
import tasks as tasks_mod
import mcpgw as mcpgw_mod
import cronjobs as cronjobs_mod
import term as term_mod
import profiles as profiles_mod
import vitals as vitals_mod
import embed_proxy as embed_proxy_mod

print(f"[Agent Hub] 配置: PORT={config.port}, HOST={config.host}")

# 单一版本源：/health、FastAPI 元数据、启动横幅与页脚都取这里
VERSION = "0.11.0"

app = FastAPI(title="Agent Hub", version=VERSION)


def _parse_cors_origins() -> list:
    """CORS_ORIGINS 逗号分隔解析；异常/为空时退化为仅回环来源（不放松默认安全）"""
    raw = os.getenv(
        "CORS_ORIGINS",
        "http://192.168.5.102:3102,http://127.0.0.1:3102,http://100.117.232.62:3102")
    try:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        for o in origins:
            if not (o.startswith("http://") or o.startswith("https://")):
                raise ValueError(f"invalid origin scheme: {o}")
        if not origins:
            raise ValueError("empty CORS_ORIGINS")
        return origins
    except Exception as e:  # noqa: BLE001
        print(f"[Agent Hub] CORS_ORIGINS 解析失败（{e}），退化为仅回环来源")
        return ["http://127.0.0.1:3102", "http://localhost:3102", "http://[::1]:3102"]


app.add_middleware(CORSMiddleware, allow_origins=_parse_cors_origins(),
                   allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

# ── 非 MCP 写路径按 IP 滑动窗口限流（风格对齐 mcpgw._check_rate）──────
API_RATE_PER_MIN = int(os.getenv("API_RATE_PER_MIN", "60"))
# ⚙设置口令：查看 TERM_TOKEN 等敏感配置时要求提供（为空则设置页不可用）
HUB_PASSCODE = os.getenv("HUB_PASSCODE", "")
_api_rate: Dict[str, deque] = defaultdict(deque)
_RATE_EXCLUDED_PREFIXES = ("/telemetry/events/", "/health", "/mcp")
_RATE_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


@app.middleware("http")
async def api_rate_limit(request: Request, call_next):
    # 只拦写方法；GET（前端 30s 轮询）与外部推送/健康检查/MCP（自带限流）不拦
    if request.method in _RATE_WRITE_METHODS:
        path = request.url.path
        if not any(path.startswith(p) for p in _RATE_EXCLUDED_PREFIXES):
            ip = request.client.host if request.client else "unknown"
            window = _api_rate[ip]
            cut = time.monotonic() - 60
            while window and window[0] < cut:
                window.popleft()
            if len(window) >= API_RATE_PER_MIN:
                print(f"[rate] 429：{ip} 超过 {API_RATE_PER_MIN}/min（{request.method} {path}）")
                return JSONResponse(
                    {"detail": f"API 限流：超过 {API_RATE_PER_MIN}/min"}, status_code=429)
            window.append(time.monotonic())
    return await call_next(request)

templates_dir = Path(__file__).parent.parent / "templates"
static_path = Path(__file__).parent.parent / "static"
templates = Jinja2Templates(directory=str(templates_dir))

# v0.5.2.7 自定义静态资源路由（强制 no-cache，防 .js 改后浏览器用旧 ETag/Last-Modified 304）
# 替代原 StaticFiles mount（仍保留 fallback）
if static_path.exists():
    from fastapi.responses import FileResponse
    from fastapi import Request
    @app.get("/static/{file_path:path}")
    async def _static_no_cache(file_path: str, request: Request):
        f = (static_path / file_path).resolve()
        # 路径安全：必须在 static_path 下
        if not str(f).startswith(str(static_path.resolve())):
            from fastapi import HTTPException
            raise HTTPException(404)
        if not f.is_file():
            from fastapi import HTTPException
            raise HTTPException(404)
        return FileResponse(
            str(f),
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    # 不再 mount StaticFiles；自定义路由接管 /static/

# 子路由（Hook / 记忆 / 指挥官）
app.include_router(hook_mod.router)
app.include_router(memory_mod.router)
app.include_router(tasks_mod.router)
app.include_router(mcpgw_mod.router)
app.include_router(cronjobs_mod.router)
app.include_router(term_mod.router)

# D2：Hub MCP Server —— 把本机事实源以 MCP 暴露给 Hermes 等外部 Agent。
# 端点为 /hub-mcp/mcp（streamable_http_app 自带 /mcp 子路由，故挂在 /hub-mcp 下，避免与 mcpgw 的 /mcp/* REST 冲突）。
# 用 try 包裹：挂载失败不得影响主服务启动。
try:
    import contextlib

    import hubmcp

    # session_manager 是懒创建的：必须先 build_app() 再取。
    _mcp_app = hubmcp.build_app()
    _mcp_sm = hubmcp.server.session_manager

    # Starlette 不会自动运行 mount 子应用的 lifespan，需手动并入主应用 lifespan，
    # 否则报 "Task group is not initialized. Make sure to use run()."
    _orig_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _hub_lifespan(app_):
        async with _mcp_sm.run():
            async with _orig_lifespan(app_):
                yield

    app.router.lifespan_context = _hub_lifespan
    app.mount("/hub-mcp", _mcp_app)
    print("[Agent Hub] Hub MCP server 已挂载：/hub-mcp/mcp（lifespan 已并入）")
except Exception as e:  # noqa: BLE001
    print(f"[Agent Hub] Hub MCP server 挂载失败（主服务不受影响）：{type(e).__name__}: {e}")

discovery: Optional[AgentDiscovery] = None


class ChatRequest(BaseModel):
    agent_id: Optional[str] = None  # 以路径参数为准（v0.1 遗留必填校验是 bug）
    message: str
    session_id: Optional[str] = None
    model: Optional[str] = None
    cwd: Optional[str] = None


class AgentRegisterRequest(BaseModel):
    dir: str
    name: Optional[str] = None
    port: Optional[int] = None
    command: Optional[str] = None
    args: Optional[list] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "agent-hub", "version": VERSION, "port": config.port}


# ── Agents ────────────────────────────────────────────────────────────

@app.get("/api/agents")
async def list_agents():
    if discovery is None:
        raise HTTPException(status_code=503, detail="Discovery not initialized")
    agents = await discovery.discover_all()
    return {"agents": [a.to_dict() for a in agents], "count": len(agents)}


@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str):
    if discovery is None:
        raise HTTPException(503, "Discovery not initialized")
    agent = discovery.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")
    return agent.to_dict()


def _agent_profs_for_vitals():
    """只体检 kind=agent 的画像（gateway/service/tool 不进菜单闸门），
    且**含已被摘掉的候补** —— 不重新探测就会被永久固化，用户装好了也回不来。"""
    return [p for p in profiles_mod.all_profiles(include_blocked=True)
            if p.get("kind") == "agent"]


@app.get("/api/vitals")
async def get_vitals():
    """判定台账（可审计）：每个 agent 的证据 + 裁决 + 来源 + 概率"""
    snap = vitals_mod.vitals.snapshot()
    return {"count": len(snap), "last_sweep": vitals_mod.vitals.last_sweep,
            "jev_key_present": bool(vitals_mod.profiles_jev_key()),
            "menu_min": vitals_mod.MENU_MIN, "sweep_every_sec": vitals_mod.SWEEP_EVERY,
            "detail": snap}


@app.post("/api/vitals/sweep")
async def post_sweep():
    """跑一轮完整体检：先全量 L1/L2（不碰模型），再对「上次真请求实测已过期」的
    候补补 L4（每人每 24h 最多一次）。VITALS_RT_SWEEP=0 可退成纯廉价轮。"""
    return await asyncio.to_thread(vitals_mod.vitals.sweep, _agent_profs_for_vitals())


@app.post("/api/agents/{agent_id}/verify")
async def verify_agent(agent_id: str):
    """L4 体检：跑一次真实一次性请求。要耗 token 且慢（冷启动可达 60s），
    所以只给显式动作触发，不进自动周期。"""
    p = profiles_mod.get_profile(agent_id)
    if not p:
        raise HTTPException(404, f"未知 agent: {agent_id}")
    if not p.get("verify_argv"):
        raise HTTPException(400, {"error": "该 Agent 未声明 verify_argv（无法做真实应答实测）",
                                 "shape": (vitals_mod.collect(p)).get("agent_shape")})
    rec = await asyncio.to_thread(vitals_mod.vitals.verify, p)
    ev = rec.get("evidence", {})
    out = {k: rec.get(k) for k in ("verdict", "source", "rule_verdict", "confidence",
                                   "menu_noul", "present_noul", "roundtrip_noul",
                                   "block_noul", "jev_error")}
    out["evidence"] = {kk: ev.get(kk) for kk in
                       ("agent_shape", "resolved_path", "version_rc", "run_rc", "run_ok",
                        "run_evidence", "evidence_sha", "endpoint_serving")}
    return out


@app.post("/api/agents/detect")
async def detect(body: AgentRegisterRequest):
    """目录 → 自动识别类型/启动命令/端口（Agent_Manager 添加 Agent 的第一步）"""
    return detect_project(body.dir)


@app.post("/api/agents")
async def register_agent(body: AgentRegisterRequest):
    """注册自定义 Agent（识别 + 入库；进程管理交由原守护，hub 只做视图与对话）"""
    info = detect_project(body.dir)
    if info["type"] == "unknown" and not body.command:
        raise HTTPException(400, {"error": "无法识别项目类型且未提供 command", "detected": info})
    now = _now()
    agent_id = re.sub(r"[^a-z0-9_-]", "-", (body.name or Path(body.dir).name).lower())
    port = body.port or info.get("port")
    endpoint = f"http://127.0.0.1:{port}" if port else None
    db.execute(
        """INSERT INTO custom_agents(id,name,type,command,args,working_dir,env,port,endpoint,description,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name,type=excluded.type,
             command=excluded.command,args=excluded.args,working_dir=excluded.working_dir,
             port=excluded.port,endpoint=excluded.endpoint,description=excluded.description,
             updated_at=excluded.updated_at""",
        (agent_id, body.name or Path(body.dir).name, info["type"],
         body.command or (info.get("command") and " ".join([info["command"]] + info["args"])),
         json.dumps(info.get("args") or []), str(Path(body.dir).expanduser()),
         "{}", port, endpoint, info.get("entry") or "", now, now))
    if discovery:
        discovery.reload()
    return {"status": "registered", "id": agent_id, "detected": info}


@app.delete("/api/agents/{agent_id}")
async def unregister_agent(agent_id: str):
    n = db.execute("DELETE FROM custom_agents WHERE id=?", (agent_id,))
    if not n:
        raise HTTPException(404, "custom agent not found (内置 Agent 不可删)")
    if discovery:
        discovery.reload()
    return {"status": "deleted", "id": agent_id}


# ── 统一对话（Phase 2：真实适配器直连）──────────────────────────────

async def _chat_dispatch(agent_id: str, message: str,
                         session_id: Optional[str] = None,
                         model: Optional[str] = None,
                         cwd: Optional[str] = None,
                         tools: Optional[bool] = None,
                         repair_mode: Optional[bool] = None,
                         trace_id: Optional[str] = None) -> Dict:
    import time as _time
    t0 = _time.monotonic()
    adapter = get_adapter(agent_id)
    if adapter is None:
        rows = db.query("SELECT * FROM custom_agents WHERE id=?", (agent_id,))
        if rows and rows[0]["port"]:
            from adapters.openai_compat import OpenAICompatAdapter
            adapter = OpenAICompatAdapter(config, agent_id,
                                          base_url=f"http://127.0.0.1:{rows[0]['port']}",
                                          default_model=model or "")
        if adapter is None:
            return {"success": False, "error": f"agent {agent_id} 无可用适配器",
                    "hint": "CLI/TUI Agent 请通过原生界面访问"}
    # 取历史
    history = []
    if session_id:
        hist = db.query("SELECT role,content FROM chat_messages WHERE session_id=? "
                        "AND role IN ('user','assistant') ORDER BY id DESC LIMIT 20",
                        (session_id,))
        history = [{"role": r["role"], "content": r["content"]} for r in reversed(hist)]
    result = await adapter.chat(message, session_id=session_id, model=model,
                                history=history, cwd=cwd)
    # S2 画像埋点：append-only，成功率/耗时统计源
    dur = int((_time.monotonic() - t0) * 1000)
    db.log_profile_event("hub_chat", agent_id,
                         "success" if result.get("success") else "fail", dur,
                         trace_id=trace_id,
                         detail={"session_id": session_id,
                                 "usage": result.get("usage"),
                                 "error": (result.get("error") or "")[:200] or None})
    # 会话落盘
    if session_id:
        now = _now()
        db.execute("INSERT OR IGNORE INTO chat_sessions(id,agent_id,title,created_at,updated_at) "
                   "VALUES(?,?,?,?,?)",
                   (session_id, agent_id, message[:60], now, now))
        db.execute("INSERT INTO chat_messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                   (session_id, "user", message, now))
        reply = result.get("response") or result.get("error") or ""
        db.execute("INSERT INTO chat_messages(session_id,role,content,meta,created_at) VALUES(?,?,?,?,?)",
                   (session_id, "assistant" if result.get("success") else "error", reply,
                    json.dumps(result.get("usage"), ensure_ascii=False) if result.get("usage") else None, now))
        db.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (now, session_id))
    return result


@app.post("/api/agents/{agent_id}/chat")
async def chat(agent_id: str, request: Request, req: ChatRequest):
    session_id = req.session_id or uuid.uuid4().hex[:12]
    trace_id = request.headers.get("x-trace-id")
    result = await _chat_dispatch(agent_id, req.message, session_id, req.model,
                                  cwd=req.cwd, tools=req.tools, repair_mode=req.repair_mode,
                                  trace_id=trace_id)
    return {"agent_id": agent_id, "session_id": session_id,
            "message": req.message, "timestamp": _now(), **result}


@app.post("/api/agents/{agent_id}/chat/stream")
async def chat_stream(agent_id: str, request: ChatRequest):
    adapter = get_adapter(agent_id)
    if adapter is None:
        raise HTTPException(404, f"agent {agent_id} 不支持流式对话")
    session_id = request.session_id or uuid.uuid4().hex[:12]

    async def gen():
        yield f"data: {json.dumps({'session_id': session_id})}\n\n"
        async for chunk in adapter.chat_stream(request.message, session_id=session_id,
                                               model=request.model, cwd=request.cwd):
            yield chunk
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/sessions")
async def list_sessions(agent_id: Optional[str] = None, limit: int = 20):
    sessions = []
    sql = ("SELECT s.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.id) "
           "AS messages FROM chat_sessions s")
    params: list = []
    if agent_id:
        sql += " WHERE s.agent_id=?"
        params.append(agent_id)
    sql += " ORDER BY s.updated_at DESC LIMIT ?"
    params.append(limit)
    sessions = db.query(sql, tuple(params))
    # 合并 hub 外部会话（pi）
    if not agent_id or agent_id == "pi":
        pi_sessions = await _get_pi_sessions(max(0, limit - len(sessions)))
        sessions.extend([{"agent": "pi", "id": s.get("id"), **s} for s in pi_sessions])
    return {"sessions": sessions[:limit], "count": len(sessions)}


@app.get("/api/sessions/{session_id}/messages")
async def session_messages(session_id: str, limit: int = 100):
    return {"messages": db.query(
        "SELECT role,content,meta,created_at FROM chat_messages "
        "WHERE session_id=? ORDER BY id ASC LIMIT ?", (session_id, limit))}


class SessionPatch(BaseModel):
    title: Optional[str] = None


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, req: SessionPatch):
    if req.title is None:
        raise HTTPException(400, "no fields to update")
    n = db.execute("UPDATE chat_sessions SET title=?, updated_at=? WHERE id=?",
                   (req.title.strip()[:120], _now(), session_id))
    if not n:
        raise HTTPException(404, "session not found")
    return {"status": "renamed", "id": session_id, "title": req.title}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    n1 = db.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
    n2 = db.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
    if not n2:
        raise HTTPException(404, "session not found")
    return {"status": "deleted", "id": session_id, "messages": n1}


# ── ⚙设置（口令保护的敏感配置查看）────────────────────────

class PasscodeRequest(BaseModel):
    passcode: str = ""


@app.post("/api/settings/term-token")
async def settings_term_token(request: Request, req: PasscodeRequest):
    """口令正确时返回 TERM_TOKEN。

    有意用 POST 而非 GET：现有 api_rate_limit 只拦写方法，
    口令爆破会被 429 限流拦住（60 次/分钟/IP）。
    """
    if not HUB_PASSCODE:
        raise HTTPException(503, "未设置 HUB_PASSCODE，请先在 .env 配置后再使用设置页")
    if not hmac.compare_digest(req.passcode, HUB_PASSCODE):
        ip = request.client.host if request.client else "?"
        print(f"[settings] 口令错误：{ip}")
        raise HTTPException(401, "口令错误")
    tok = os.getenv("TERM_TOKEN", "")
    return {"term_token": tok, "set": bool(tok)}


# ── 模型代理（前端动态加载：CCR/jcode 等 OpenAI 兼容 /v1/models）────

@app.get("/api/models")
async def list_models(agent_id: Optional[str] = None):
    """统一模型列表端点。

    默认拉 claude(CCR:3456) 的 /v1/models 作为"全局可对话模型"
    （v0.10.0 起 hub-self 已移除，claude/jcode 共用同一 CCR 模型表）。
    按 vendor/display_name 去重后返回；带分组（qwen/deepseek/nvidia/openrouter/agnes）。"""
    adapter_id = agent_id or "claude"
    adapter = get_adapter(adapter_id)
    base = None
    if hasattr(adapter, "base_url"):
        base = adapter.base_url
    if not base:
        return {"models": [], "groups": {}, "error": "no compatible adapter"}
    import aiohttp as _aio
    out: list = []
    try:
        async with _aio.ClientSession() as s:
            async with s.get(f"{base}/v1/models",
                             headers=adapter.build_headers() if hasattr(adapter, "build_headers") else {},
                             timeout=_aio.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    data = await r.json()
                    out = data.get("data") or []
    except Exception as e:  # noqa: BLE001
        return {"models": out, "groups": {}, "error": str(e)[:200]}
    # 去重 + 分组
    seen = set()
    groups: dict = {}
    cleaned = []
    for m in out:
        mid = m.get("id")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        cleaned.append({"id": mid, "name": m.get("display_name") or mid,
                        "owner": m.get("owned_by") or "?"})
        # 分组键 = mid 第一段（vendor/）
        gk = mid.split("/", 1)[0] if "/" in mid else "default"
        groups.setdefault(gk, []).append(mid)
    return {"models": cleaned, "groups": groups, "count": len(cleaned),
            "source": adapter_id}


# ── 端口管理 ──────────────────────────────────────────────────────────

# ── 只读发现扫描（S2）────────────────────────────────

class ScanIn(BaseModel):
    auto_register: bool = True


@app.post("/api/scan/run")
async def scan_run(body: ScanIn):
    """发现源扫描（docker/systemd/CLI 名单，全部只读探测）"""
    result = await asyncio.to_thread(scanner.run_scan, body.auto_register, db)
    if body.auto_register and result.get("added") and discovery:
        discovery.reload()
    return result


@app.get("/api/ports")
async def api_ports():
    rows = list_listeners()
    # 标注归属已知 Agent
    known = {}
    if discovery:
        for a in await discovery.discover_all():
            if a.port:
                known[a.port] = a.id
    for r in rows:
        r["agent"] = known.get(r["port"])
    return {"listeners": rows, "count": len(rows)}


@app.get("/api/ports/{port}")
async def api_port_detail(port: int):
    return {"port": port, "in_use": port_in_use(port)}


# ── Dashboard ─────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "index.html", {"version": VERSION})


# ── 兼容旧端点：/api/memory（别名到 L1 列表）──────────────────────────

@app.get("/api/memory")
async def get_memory_alias(query: Optional[str] = None, limit: int = 10):
    if not query:
        rows = db.query("SELECT * FROM memories WHERE status='active' ORDER BY id DESC LIMIT ?",
                        (limit,))
        return {"memories": rows, "count": len(rows)}
    return await memory_mod.search_memory(query, limit)


async def _get_pi_sessions(limit: int) -> list:
    if limit <= 0:
        return []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{config.pi_url}/api/sessions",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("sessions", [])[:limit]
    except Exception:  # noqa: BLE001
        pass
    return []


_embed_proxy = None      # 外框注入代理句柄（EMBED_UNIFY=0 时保持 None）


async def vitals_loop():
    """可用心跳慢周期：首轮延后 2s（先让 hub 开接请求），之后每 VITALS_SWEEP_SEC 一轮。
    只跑 L1/L2（which / 文件头 / --version / --help / 端点探活），不碰模型；
    任何异常都不打死循环（否则一次偶发就把菜单永久冻在旧结论上）。"""
    await asyncio.sleep(2)
    while True:
        try:
            r = await asyncio.to_thread(vitals_mod.vitals.sweep, _agent_profs_for_vitals())
            print("[vitals] sweep %s" % r, flush=True)
        except Exception as e:  # noqa: BLE001
            print("[vitals] sweep 异常（下轮重试）%s: %s" % (
                type(e).__name__, str(e)[:160]), flush=True)
        await asyncio.sleep(vitals_mod.SWEEP_EVERY)


@app.on_event("startup")
async def startup():
    global discovery
    global _embed_proxy
    db.init_db(config.db_path)
    discovery = AgentDiscovery(config, db=db)
    build_adapters(config)
    tasks_mod.ensure_schema()
    tasks_mod.set_context(
        chat_fn=lambda a, m, s=None, mo=None, tr=None: _chat_dispatch(a, m, s, mo, tr),
        agent_ids_fn=lambda: [c["id"] for c in discovery.all_configs()])
    asyncio.create_task(tasks_mod.sweep_stale_tasks())
    asyncio.create_task(vitals_loop())
    mcpgw_mod.ensure_schema()
    cronjobs_mod.ensure_schema()
    cronjobs_mod.set_context(
        chat_fn=lambda a, m, s=None, mo=None, tr=None: _chat_dispatch(a, m, s, mo, tr))
    cronjobs_mod.start_engine()
    # 外框统一注入代理（用户 09-20 方案 b）：:3103 → qwenpaw :8088，HTML 出栈前插 <style>。
    # 起不来也不能影响 hub 本体：裹 try/except，顶多外框不统一（嵌入视图仍直连可用）。
    if profiles_mod.EMBED_UNIFY:
        try:
            qp = next((p for p in profiles_mod.PROFILES if p.get("id") == "qwenpaw"), None)
            if qp and qp.get("port"):
                _embed_proxy = embed_proxy_mod.EmbedProxy(
                    "QwenPaw", "127.0.0.1", qp["port"],
                    listen_host=os.getenv("EMBED_PROXY_HOST", config.host),
                    listen_port=profiles_mod.EMBED_PROXY_PORT)
                await _embed_proxy.start()
        except Exception as e:
            print(f"[Agent Hub] 注入代理启动失败（不影响其他功能）：{type(e).__name__}: {e}")
            _embed_proxy = None
    print(f"[Agent Hub] 启动完成 v{VERSION}，监听 {config.host}:{config.port}")
    print(f"[Agent Hub] CCR: {config.ccr_url} | pi: {config.pi_url} | "
          f"jcode: {config.jcode_url} | TDAI: {config.tdaI_url}")
    print(f"[Agent Hub] LLM（记忆 L2 重建 / DAG 拆解）: {config.manager_llm_base} "
          f"model={config.manager_llm_model}")


@app.on_event("shutdown")
async def shutdown():
    if _embed_proxy is not None:
        await _embed_proxy.stop()
    term_mod.kill_all()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host=config.host, port=config.port, log_level="info")
