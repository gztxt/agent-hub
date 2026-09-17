"""Agent Hub - 主入口（Agent_Manager 融合版 v0.3.0）

融合自 Zafer-Liu/Agent_Manager (Apache-2.0) 的设计与语义：
- Hook 遥测端点（agent_http.rs → src/hook.py）
- 三层记忆中心（memory 子系统 → src/memory.py）
- Manager Agent 自然语言指挥官（llm+mcp_agent → src/manager.py）
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
import manager as manager_mod
import tasks as tasks_mod
import mcpgw as mcpgw_mod
import cronjobs as cronjobs_mod
import term as term_mod

print(f"[Agent Hub] 配置: PORT={config.port}, HOST={config.host}")

# 单一版本源：/health、FastAPI 元数据、启动横幅与页脚都取这里
VERSION = "0.6.0"

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
app.include_router(manager_mod.router)
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
    tools: Optional[bool] = None  # hub-self: 启用指挥官工具环
    repair_mode: Optional[bool] = None  # hub-self v0.4：开启后才暴露修复工具（白名单 restart/配置写/回滚）


class AgentRegisterRequest(BaseModel):
    dir: str
    name: Optional[str] = None
    port: Optional[int] = None
    command: Optional[str] = None
    args: Optional[list] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_history(session_id: Optional[str], limit: int = 12):
    """从 chat_messages 表读最近 N 轮 user/assistant 消息，oldest-first 列表。
    流式端点复用。session_id 为空返 []。"""
    if not session_id:
        return []
    try:
        hist = db.query("SELECT role,content FROM chat_messages WHERE session_id=? "
                        "AND role IN ('user','assistant') ORDER BY id DESC LIMIT ?",
                        (session_id, limit))
        return [{"role": r["role"], "content": r["content"]} for r in reversed(hist)]
    except Exception:
        return []


def _build_task_summary(session_id: str, message: str, steps: list,
                        answer: str, model: str, duration_ms: int,
                        success: bool = True, error: str = "") -> dict:
    """v0.5.2 任务完成总结报告：从步骤 + 答案 + 时长自动汇总。

    v0.5.2 改进（用户反馈"总结是复读答案"）：
    - 不再堆砌"思考 N 步 / 工具 N 次"等过程统计
    - 改为"产物视角"：这次任务**实际产出**了啥（路径/commit/动作）
    - 加"下一步建议"段（从 answer 末尾 if 建议/需要/要不要 类语句）
    - 失败时给"未完成项"清单

    返回结构同时给：① 落 chat_messages role=summary；② 推 SSE final.summary。
    """
    import re
    tool_calls = [s for s in steps if s.get("kind") == "toolcall"]
    tool_results = [s for s in steps if s.get("kind") == "toolresult"]
    tool_names = [t.get("tool") for t in tool_calls]
    tool_freq = {}
    for n in tool_names:
        tool_freq[n] = tool_freq.get(n, 0) + 1
    failed_tools = [r.get("tool") for r in tool_results
                    if ('"error"' in (r.get("content") or "")[:300]
                        or "禁用" in (r.get("content") or "")[:200]
                        or "越界" in (r.get("content") or "")[:200]
                        or "未找到" in (r.get("content") or "")[:200])]

    # 任务判定
    task_kind = "查询/分析"
    if any(k in message for k in ("列", "找", "查", "读", "看", "list", "find", "read")):
        task_kind = "信息查询"
    if any(k in message for k in ("改", "写", "配置", "重启", "回滚", "config", "restart", "rollback", "修复", "加", "补")):
        task_kind = "配置/修复"
    if any(k in message for k in ("删", "清理", "归档", "remove", "clean")):
        task_kind = "清理/归档"

    # 时长
    if duration_ms < 1000:
        dur_str = f"{duration_ms}ms"
    elif duration_ms < 60000:
        dur_str = f"{duration_ms/1000:.1f}s"
    else:
        dur_str = f"{duration_ms//60000}分{duration_ms%60000//1000}秒"

    # 状态
    if not success:
        status = "❌ 失败"
        status_emoji = "❌"
    elif failed_tools:
        status = f"⚠️ 部分成功（{len(failed_tools)}/{len(tool_calls)} 工具失败）"
        status_emoji = "⚠️"
    else:
        status = "✅ 完成"
        status_emoji = "✅"

    # ── v0.5.2 产物视角提取 ──
    products = []     # 产物：路径 / commit / 关键 action
    actions = []      # 动作："重启 ccr"/"写 README" 等
    next_steps = []   # 下一步建议
    unfinished = []   # 未完成项（失败时用）

    # 1) 从 steps 抓所有路径（v0.5.2.2 改：只信 steps 不信 answer，避免 hallucinated 路径）
    paths = set()
    # 计算成功 step 的 tool_name（exit_code 0 + 无 error 字段）
    succeeded_tools = set()
    for tr in tool_results:
        c = tr.get("content") or ""
        if '"error"' not in c[:300] and "禁用" not in c[:200] and "越界" not in c[:200] and "未找到" not in c[:200]:
            succeeded_tools.add(tr.get("tool"))
    # 从成功的 list_dir / read_file / find_files / shell_run 等抓 path
    for tc in tool_calls:
        tool = tc.get("tool")
        if tool not in succeeded_tools:
            continue  # 失败的步骤路径不收
        ti = tc.get("tool_input") or {}
        for k in ("path", "root", "cwd", "glob_pattern"):
            v = ti.get(k)
            if v and isinstance(v, str) and (v.startswith(("/", "~")) or "*" in v):
                paths.add(v)
    # write 类工具（config_write / rollback / safe_restart）算动作
    write_tools = [tc for tc in tool_calls if tc.get("tool") in ("config_write", "rollback", "safe_restart")]
    for tc in write_tools:
        ti = tc.get("tool_input") or {}
        if tc.get("tool") == "config_write" and ti.get("path"):
            actions.append(f"写 {ti['path']}（待用户二次确认）")
        elif tc.get("tool") == "safe_restart" and ti.get("unit"):
            actions.append(f"重启 systemd unit {ti['unit']}")
        elif tc.get("tool") == "rollback" and ti.get("backup_id"):
            actions.append(f"回滚 {ti['backup_id']}")

    # 2) commit SHA（要求是 hex-only 7-40 字符；前 8 字符显示）
    # 锚定 `commit xxx` 上下文或行内反引号包住的 hex 串，避免把日期 20260906 误判
    for m in re.finditer(r'commit\s+`?([0-9a-f]{7,40})`?', answer or ""):
        c = m.group(1)
        if re.match(r'^[0-9a-f]{7,40}$', c):
            products.append(f"commit `{c[:8]}`")
            if len([p for p in products if p.startswith("commit")]) >= 3: break

    # 2.5) 答案章节结构（## 标题）—— 让总结"知道答案有 N 节"但不复读正文
    sections = re.findall(r'^\s*#{1,3}\s+(.+?)$', answer or "", re.M)
    if sections:
        products.append(f"答案含 {len(sections)} 个章节：{', '.join(sections[:4])}{'...' if len(sections) > 4 else ''}")

    # 3) 数字化的"产出了 N 项"
    n_items = re.search(r'(\d+)\s*(?:个|项|条|步|文件)', answer or "")
    if n_items and not products:
        # 不重复堆路径太多
        pass

    # 4) 下一步建议（从 answer 末尾找 if 引导的语句）
    lines = (answer or "").split("\n")
    for ln in lines[-8:]:
        s = ln.strip()
        if not s: continue
        if re.search(r'(需要我|要不要|建议|可以|下一步|需要做|如需|需要你)', s):
            # 截短到 80 字
            s_short = s if len(s) <= 80 else s[:77] + "..."
            next_steps.append(s_short)
            if len(next_steps) >= 2: break

    # 5) 失败时填未完成
    if not success:
        unfinished.append(f"任务异常：{(error or 'unknown')[:120]}")
    elif failed_tools:
        for ft in failed_tools:
            unfinished.append(f"{ft} 被拒/失败")
    # 失败工具的具体原因
    for tr in tool_results:
        if tr.get("tool") in failed_tools:
            try:
                import json as _json
                c = tr.get("content") or ""
                # 截到 80 字
                j = _json.loads(c) if c.startswith("{") else {}
                if j.get("error"):
                    unfinished.append(f"  └─ {tr.get('tool')}: {j['error'][:80]}")
            except Exception:
                pass

    # ── v0.5.2.3 表格化 + 紧凑拼装 ──
    # 设计：两段合一
    # 段 1: 表格（属性/值）：状态、耗时、类型、模型、session
    # 段 2: 表格（产物/动作）：路径、commit、动作
    # 段 3: 紧凑列表：下一步 + 未完成（只一行）
    # 目标：扫一眼就看完，不上下翻
    summary_rows = [
        ("状态", status),
        ("耗时", dur_str),
        ("类型", task_kind),
        ("模型", f"`{model or '?'}`"),
        ("会话", f"`{session_id[:12]}`"),
    ]
    tbl_lines = ["| 属性 | 值 |", "|---|---|"]
    for k, v in summary_rows:
        tbl_lines.append(f"| {k} | {v} |")
    parts = ["\n".join(tbl_lines) + "\n"]

    # 产物/动作表
    product_rows = []
    for p in sorted(paths)[:5]:
        product_rows.append(("文件", f"`{p}`"))
    for a in actions:
        product_rows.append(("动作", a))
    for c in products:
        if c.startswith("commit "):
            product_rows.append(("产物", c))
        else:
            product_rows.append(("产物", c))
    if product_rows:
        prod_tbl = ["| 类型 | 内容 |", "|---|---|"]
        for k, v in product_rows[:8]:
            prod_tbl.append(f"| {k} | {v} |")
        parts.append("\n".join(prod_tbl) + "\n")

    # 下一步 + 未完成（一行式紧凑）
    tail = []
    if next_steps:
        # v0.5.2.3 改：只取 1 条最相关的（避免 2 条并列太长）
        best = next_steps[0].replace("\n", " ").strip()
        # 去掉 markdown 加粗前缀和列表符
        best = re.sub(r'^[-*]\s*\*?\*?\d*\.?\s*\*?\*?', '', best).strip()
        best = re.sub(r'\*\*', '', best).strip()  # 去 **
        if best:
            # 截断到 80 字符（中文按 1 字算 1 字符）
            short = best if len(best) <= 80 else best[:77] + "..."
            tail.append(f"**下一步**: {short}")
    if unfinished:
        uf = "; ".join(u.strip().replace("\n", " ") for u in unfinished[:3] if u.strip())
        if uf:
            tail.append(f"**未完成**: {uf[:80]}")
    if tail:
        parts.append(" · ".join(tail))

    summary_md = "\n".join(parts)
    return {
        "status": status,
        "status_emoji": status_emoji,
        "task_kind": task_kind,
        "duration_ms": duration_ms,
        "duration_str": dur_str,
        "tool_freq": tool_freq,
        "products": list(sorted(paths)[:6]),
        "actions": actions,
        "commits": products,
        "next_steps": next_steps[:3],
        "unfinished": unfinished[:5],
        "model": model or "?",
        "session_id": session_id,
        "summary_md": summary_md,
        "error": error,
    }


def _persist_summary_message(session_id: str, summary: dict):
    """把任务总结落 chat_messages 表 role=summary + telemetry 落账。
    三处调用点（流式 /chat/stream、非流式 /chat、_chat_dispatch）都过这一处 → 一致性。"""
    if not session_id:
        return
    try:
        now = _now()
        meta = json.dumps({
            "task_kind": summary.get("task_kind"),
            "duration_ms": summary.get("duration_ms"),
            "tool_freq": summary.get("tool_freq"),
            "status": summary.get("status"),
            "model": summary.get("model"),
        }, ensure_ascii=False)
        db.execute("INSERT INTO chat_messages(session_id,role,content,meta,created_at) "
                   "VALUES(?,?,?,?,?)",
                   (session_id, "summary", summary.get("summary_md", ""), meta, now))
    except Exception as e:  # noqa: BLE001
        # 总结落库失败不影响主对话
        print(f"[summary persist] failed: {e}", flush=True)
    # 写 telemetry
    try:
        db.log_profile_event("hubself_summary", "hub-self",
                             "success" if "✅" in summary.get("status", "") else "fail",
                             summary.get("duration_ms", 0),
                             trace_id=session_id,
                             detail={"task_kind": summary.get("task_kind"),
                                     "tools": summary.get("tool_freq")})
    except Exception:
        pass


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
最终用简洁中文汇报。

【v0.5.2.4 输出规范】回答涉及"列项目/列目录/列文件清单"类问题时：
1. **结构**：按分类（业务系统 / Agent 运行时 / 工具 / 知识库 / 备份归档 / 配置等）分组；
2. **表格化**：每类用 markdown 表格「项目 | 简介 | 关键信息」三列；
3. **简介源**：优先用 list_dir 返回的 `description` 字段（系统已自动从 README 抽取），不要再 read_file README 重复读；
4. **精简**：每项 1-2 行；不要堆路径；不要"详细介绍项目背景"等套话；
5. **末尾**：给 1 条「下一步建议」（如"如需展开某个项目告诉我"），但限 1 条、限 80 字。
示例输出（列 /fs/1000/ftp/技术文档 项目）：
| 分类 | 项目 | 简介 |
|---|---|---|
| Agent 运行时 | Agent_Manager | Agent 管理面板（Tauri+React，含 39K 行代码） |
| 业务系统 | 安防维保管理系统 | Flask+PDF 报告，端口 5001，gunicorn 部署 |"""


async def _chat_dispatch_hubself_tools(message, session_id, model, cwd, history,
                                       trace_id, repair_mode=False, on_step=None):
    """hub-self 接入指挥官工具环（list_agents/open_agent_ui/search_memory/...）

    复用 manager.TOOLS + manager._dispatch_tool；落盘复用 chat_sessions/chat_messages 表。
    cwd 注入到 system prompt；model 默认走 MANAGER_LLM_MODEL。

    v0.4 修复模式门控：repair_mode=True 时才把 5 个修复工具（agent_health/safe_restart/
    config_show/config_write/rollback）暴露给 LLM；默认 False 走原 11 工具。两次确认
    协议、双次确认状态下 chat_sessions 的 meta 等都由 manager._dispatch_tool 内部处理。
    """
    import time as _time
    import manager as manager_mod
    import llm as llm_mod
    t0 = _time.monotonic()
    # 工具集门控：默认 11 工具；修复模式开 16
    if repair_mode:
        tools_for_llm = manager_mod.TOOLS
        sys_extra = ("\n\n【修复模式已开启】可使用 5 个修复工具：agent_health / safe_restart "
                     "/ config_show / config_write / rollback。注意："
                     "(1) safe_restart 仅限白名单 8 单元；(2) config_write 必须双次确认；"
                     "(3) 修改前永远先 config_show；(4) 写 systemd unit 会自动 daemon-reload。")
    else:
        REPAIR_TOOL_NAMES = {"agent_health", "safe_restart", "config_show",
                             "config_write", "rollback"}
        tools_for_llm = [t for t in manager_mod.TOOLS
                         if t.get("function", {}).get("name") not in REPAIR_TOOL_NAMES]
        sys_extra = ("\n\n【修复模式关闭】白名单 restart / 配置写 / 回滚 等高危工具已隐藏。"
                     "若用户想修复 Agent（重启服务、改配置），明确告知："
                     "请在 UI 顶部「⚙ 修复模式」开关处打开后再问。")
    sys_prompt = HUB_SELF_SYSTEM + sys_extra + (f"\n\n当前工作目录：{cwd}" if cwd else "")
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
            msgs, tools_for_llm, timed, max_rounds=10, model=model,
            on_step=on_step)
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
            # v0.5.2 任务完成总结：落独立 summary 消息
            summary = _build_task_summary(
                session_id, message, steps, answer,
                model or llm_mod.MODEL, dur, success=True)
            _persist_summary_message(session_id, summary)
        else:
            summary = _build_task_summary(
                session_id or "anon", message, steps, answer,
                model or llm_mod.MODEL, dur, success=True)
        return {"success": True, "agent": "hub-self", "response": answer,
                "model": model or llm_mod.MODEL, "steps": steps,
                "summary": summary}
    except Exception as e:  # noqa: BLE001
        dur = int((_time.monotonic() - t0) * 1000)
        db.log_profile_event("hubself_chat", "hub-self", "fail", dur,
                             trace_id=trace_id or session_id,
                             detail={"error": str(e)[:200]})
        # 失败时也尝试落 summary
        try:
            if session_id:
                summary = _build_task_summary(
                    session_id, message, [], str(e), model or "", dur,
                    success=False, error=str(e)[:200])
                _persist_summary_message(session_id, summary)
                return {"success": False, "agent": "hub-self", "error": str(e)[:500],
                        "hint": "检查 .env 的 MANAGER_LLM_API_KEY 与 FCC(:8082) 是否可达",
                        "summary": summary}
        except Exception:
            pass
        return {"success": False, "agent": "hub-self", "error": str(e)[:500],
                "hint": "检查 .env 的 MANAGER_LLM_API_KEY 与 FCC(:8082) 是否可达"}


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
    # hub-self 工具环模式：复用 manager.py 的 TOOLS + 工具分发
    if agent_id == "hub-self" and tools:
        return await _chat_dispatch_hubself_tools(
            message, session_id, model, cwd, history, trace_id, repair_mode=repair_mode)
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


@app.post("/api/agents/hub-self/chat/stream")
async def hubself_chat_stream(req: ChatRequest):
    """hub-self 流式端点：每完成一个 step（thought/toolcall/toolresult/answer）SSE 推一次"""
    session_id = req.session_id or uuid.uuid4().hex[:12]

    async def event_gen():
        yield f"data: {json.dumps({'event': 'start', 'session_id': session_id})}\n\n"
        queue: asyncio.Queue = asyncio.Queue()

        async def on_step(step):
            await queue.put({"event": "step", "step": step})

        async def runner():
            try:
                history = _load_history(req.session_id, limit=12)
                res = await _chat_dispatch_hubself_tools(
                    req.message, session_id, req.model, req.cwd, history,
                    trace_id=req.session_id, repair_mode=bool(req.repair_mode),
                    on_step=on_step)
                await queue.put({"event": "final", **res})
            except Exception as e:  # noqa: BLE001
                await queue.put({"event": "error", "error": str(e)[:500]})
            finally:
                await queue.put({"event": "_done_"})

        runner_task = asyncio.create_task(runner())
        try:
            while True:
                item = await queue.get()
                if item.get("event") == "_done_":
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            if not runner_task.done():
                runner_task.cancel()
                try: await runner_task
                except Exception: pass

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


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
