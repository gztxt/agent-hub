"""Agent Hub - 主入口（Agent_Manager 融合版 v0.3.0）

融合自 Zafer-Liu/Agent_Manager (Apache-2.0) 的设计与语义：
- Hook 遥测端点（agent_http.rs → src/hook.py）
- 三层记忆中心（memory 子系统 → src/memory.py）
- Manager Agent 自然语言指挥官（llm+mcp_agent → src/manager.py）
- 端口管理（ports.rs → src/ports.py，只读，无 kill —— NAS 军规）
- 项目类型自动识别（agent_sources.rs → src/sources.py）
"""
import asyncio
import json
import re
import sys
import uuid
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
from fastapi.responses import HTMLResponse, StreamingResponse
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
import manager as manager_mod
import tasks as tasks_mod
import mcpgw as mcpgw_mod
import cronjobs as cronjobs_mod
import term as term_mod

print(f"[Agent Hub] 配置: PORT={config.port}, HOST={config.host}")

# 单一版本源：/health、FastAPI 元数据、启动横幅与页脚都取这里
VERSION = "0.3.0"

app = FastAPI(title="Agent Hub", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

templates_dir = Path(__file__).parent.parent / "templates"
static_path = Path(__file__).parent.parent / "static"
templates = Jinja2Templates(directory=str(templates_dir))
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# 子路由（Hook / 记忆 / 指挥官）
app.include_router(hook_mod.router)
app.include_router(memory_mod.router)
app.include_router(manager_mod.router)
app.include_router(tasks_mod.router)
app.include_router(mcpgw_mod.router)
app.include_router(cronjobs_mod.router)
app.include_router(term_mod.router)

discovery: Optional[AgentDiscovery] = None


class ChatRequest(BaseModel):
    agent_id: Optional[str] = None  # 以路径参数为准（v0.1 遗留必填校验是 bug）
    message: str
    session_id: Optional[str] = None
    model: Optional[str] = None
    cwd: Optional[str] = None
    tools: Optional[bool] = None  # hub-self: 启用指挥官工具环


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

HUB_SELF_SYSTEM = """你是 Agent Hub 的智管自身对话（Commander 对话模式）。
工作目录 cwd 作为上下文元数据会拼在 system 里，请基于它回答路径相关问题。
如有"现在哪些 Agent 在线""帮我查记忆""打开 xxx 界面"等诉求，调用对应工具。
最终用简洁中文汇报。"""


async def _chat_dispatch_hubself_tools(message, session_id, model, cwd, history, trace_id):
    """hub-self 接入指挥官工具环（list_agents/open_agent_ui/search_memory/...）

    复用 manager.TOOLS + manager._dispatch_tool；落盘复用 chat_sessions/chat_messages 表。
    cwd 注入到 system prompt；model 默认走 MANAGER_LLM_MODEL。
    """
    import time as _time
    import manager as manager_mod
    import llm as llm_mod
    t0 = _time.monotonic()
    sys_prompt = HUB_SELF_SYSTEM + (f"\n\n当前工作目录：{cwd}" if cwd else "")
    msgs = [{"role": "system", "content": sys_prompt}] + list(history or []) + [
        {"role": "user", "content": message}]
    try:
        async def timed(name, args):
            ts = _time.monotonic()
            try:
                out = await manager_mod._dispatch_tool(name, args)
                return out
            finally:
                db.log_profile_event(
                    "hubself_tool", name, "success", int((_time.monotonic() - ts) * 1000),
                    trace_id=session_id)
        answer, steps = await llm_mod.chat_tools_loop(
            msgs, manager_mod.TOOLS, timed, max_rounds=6, model=model)
        dur = int((_time.monotonic() - t0) * 1000)
        db.log_profile_event("hubself_chat", "hub-self", "success", dur,
                             trace_id=trace_id or session_id,
                             detail={"session_id": session_id})
        # 会话落盘（同 chat_sessions/chat_messages）
        if session_id:
            now = _now()
            db.execute("INSERT OR IGNORE INTO chat_sessions(id,agent_id,title,created_at,updated_at) "
                       "VALUES(?,?,?,?,?)",
                       (session_id, "hub-self", message[:60], now, now))
            db.execute("INSERT INTO chat_messages(session_id,role,content,created_at) "
                       "VALUES(?,?,?,?)", (session_id, "user", message, now))
            db.execute("INSERT INTO chat_messages(session_id,role,content,meta,created_at) "
                       "VALUES(?,?,?,?,?)",
                       (session_id, "assistant", answer,
                        json.dumps({"steps": steps}, ensure_ascii=False), now))
            db.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (now, session_id))
        return {"success": True, "agent": "hub-self", "response": answer,
                "model": model or llm_mod.MODEL, "steps": steps}
    except Exception as e:  # noqa: BLE001
        dur = int((_time.monotonic() - t0) * 1000)
        db.log_profile_event("hubself_chat", "hub-self", "fail", dur,
                             trace_id=trace_id or session_id,
                             detail={"error": str(e)[:200]})
        return {"success": False, "agent": "hub-self", "error": str(e)[:500],
                "hint": "检查 .env 的 MANAGER_LLM_API_KEY 与 FCC(:8082) 是否可达"}


async def _chat_dispatch(agent_id: str, message: str,
                         session_id: Optional[str] = None,
                         model: Optional[str] = None,
                         cwd: Optional[str] = None,
                         tools: Optional[bool] = None,
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
    # hub-self 工具环模式：复用 manager.py 的 TOOLS + 工具分发
    if agent_id == "hub-self" and tools:
        return await _chat_dispatch_hubself_tools(
            message, session_id, model, cwd, history, trace_id)
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
                                  cwd=req.cwd, tools=req.tools, trace_id=trace_id)
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


# ── 模型代理（前端动态加载：CCR/jcode 等 OpenAI 兼容 /v1/models）────

@app.get("/api/models")
async def list_models(agent_id: Optional[str] = None):
    """统一模型列表端点。

    默认拉 hub-self(CCR:3456) 的 /v1/models 作为"全局可对话模型"。
    按 vendor/display_name 去重后返回；带分组（qwen/deepseek/nvidia/openrouter/agnes）。"""
    adapter_id = agent_id or "hub-self"
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


@app.on_event("startup")
async def startup():
    global discovery
    db.init_db(config.db_path)
    discovery = AgentDiscovery(config, db=db)
    build_adapters(config)
    manager_mod.set_context(discovery=discovery, config=config,
                            chat_fn=lambda a, m, s=None, mo=None, tr=None: _chat_dispatch(a, m, s, mo, tr))
    tasks_mod.ensure_schema()
    tasks_mod.set_context(
        chat_fn=lambda a, m, s=None, mo=None, tr=None: _chat_dispatch(a, m, s, mo, tr),
        agent_ids_fn=lambda: [c["id"] for c in discovery.all_configs()])
    asyncio.create_task(tasks_mod.sweep_stale_tasks())
    mcpgw_mod.ensure_schema()
    cronjobs_mod.ensure_schema()
    cronjobs_mod.set_context(
        chat_fn=lambda a, m, s=None, mo=None, tr=None: _chat_dispatch(a, m, s, mo, tr))
    cronjobs_mod.start_engine()
    print(f"[Agent Hub] 启动完成 v{VERSION}，监听 {config.host}:{config.port}")
    print(f"[Agent Hub] CCR: {config.ccr_url} | pi: {config.pi_url} | "
          f"jcode: {config.jcode_url} | TDAI: {config.tdaI_url}")
    print(f"[Agent Hub] Manager LLM: {config.manager_llm_base} model={config.manager_llm_model}")


@app.on_event("shutdown")
async def shutdown():
    term_mod.kill_all()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host=config.host, port=config.port, log_level="info")
