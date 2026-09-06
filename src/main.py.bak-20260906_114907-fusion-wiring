"""Agent Hub - 主入口"""
import asyncio
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional

# 必须在导入 config 前加载 .env
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print(f"[Agent Hub] 已加载 .env: {env_path}")

import aiohttp
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent))
from config import config
from discovery import AgentDiscovery, AgentInfo

print(f"[Agent Hub] 配置: PORT={config.port}, HOST={config.host}")

app = FastAPI(title="Agent Hub", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 模板和静态文件
templates_dir = Path(__file__).parent.parent / "templates"
static_path = Path(__file__).parent.parent / "static"
jinja_env = Environment(loader=FileSystemLoader(str(templates_dir)))
templates = Jinja2Templates(env=jinja_env)

if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# 全局状态
discovery: Optional[AgentDiscovery] = None
agents_cache: Dict[str, AgentInfo] = {}


class ChatRequest(BaseModel):
    agent_id: str
    message: str
    session_id: Optional[str] = None


class TaskRequest(BaseModel):
    agent_ids: list[str]
    message: str
    strategy: str = "sequential"


@app.get("/health")
async def health():
    return {"status": "ok", "service": "agent-hub", "version": "0.1.0", "port": config.port}


@app.get("/api/agents")
async def list_agents():
    if discovery is None:
        raise HTTPException(status_code=503, detail="Discovery not initialized")
    agents = await discovery.discover_all()
    return {"agents": [a.to_dict() for a in agents], "count": len(agents)}


@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str):
    if discovery is None:
        raise HTTPException(status_code=503, detail="Discovery not initialized")
    agent = discovery.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return agent.to_dict()


@app.post("/api/agents/{agent_id}/chat")
async def chat(agent_id: str, request: ChatRequest):
    return {
        "agent_id": agent_id,
        "message": request.message,
        "response": f"[模拟响应] {agent_id} 暂不支持直接对话，请通过原生界面访问",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/api/sessions")
async def list_sessions(agent_id: Optional[str] = None, limit: int = 20):
    sessions = []
    if agent_id is None or agent_id == "pi":
        pi_sessions = await _get_pi_sessions(limit)
        sessions.extend([{"agent": "pi", **s} for s in pi_sessions])
    return {"sessions": sessions[:limit], "count": len(sessions)}


@app.get("/api/memory")
async def get_memory(query: Optional[str] = None, limit: int = 10):
    if not query:
        return {"memories": [], "count": 0, "hint": "提供 query 参数进行语义搜索"}
    memories = await _search_memory(query, limit)
    return {"memories": memories, "count": len(memories)}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: dict = {}):
    return templates.TemplateResponse("index.html", {"request": request})


async def _get_pi_sessions(limit: int) -> list:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{config.pi_url}/api/sessions",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("sessions", [])[:limit]
    except:
        pass
    return []


async def _search_memory(query: str, limit: int) -> list:
    return []


@app.on_event("startup")
async def startup():
    global discovery
    discovery = AgentDiscovery(config)
    print(f"[Agent Hub] 启动完成，监听 {config.host}:{config.port}")
    print(f"[Agent Hub] CCR: {config.ccr_url}")
    print(f"[Agent Hub] pi: {config.pi_url}")
    print(f"[Agent Hub] jcode: {config.jcode_url}")
    print(f"[Agent Hub] TDAI: {config.tdaI_url}")


if __name__ == "__main__":
    import uvicorn
    print(f"[Agent Hub] 启动 uvicorn, host={config.host}, port={config.port}")
    uvicorn.run(
        "src.main:app",
        host=config.host,
        port=config.port,
        log_level="info"
    )
