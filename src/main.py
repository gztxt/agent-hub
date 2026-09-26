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
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
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
from writeauth import write_gate
from registry import build_adapters, get_adapter
import hook as hook_mod
import memory as memory_mod
import memfed as memfed_mod
import kb as kb_mod
import skill as skill_mod
import tasks as tasks_mod
import mcpgw as mcpgw_mod
import cronjobs as cronjobs_mod
import term as term_mod
import profiles as profiles_mod
import vitals as vitals_mod
import embed_proxy as embed_proxy_mod
import selfattest
import staticguard
import tdai_client
import gwprobe                      # 上游网关（CCR）连通性 + 模型注册清单的缓存式体检
import healthx                      # /health 派生量的纯函数层（L0 不 import src.main，故抽出来）
import sessions_export as export_mod
import writeauth                    # 导出端点按写端点同等鉴权（复用 decide 的 fail-closed）
import audit as audit_mod           # 资产变更审计的只读查询门面（GET /api/audit/list）
import runlog as runlog_mod         # 运行日志：三中心检索留痕 + GET /api/runlog 查询门面
import cloudcli as cloudcli_mod     # CloudCLI 项目直达：项目清单（直读 auth.db）+ 会话启动代理
import localprojects as localprojects_mod  # 本机项目清单（多根 git 扫描 + cloudcli 合并）

print(f"[Agent Hub] 配置: PORT={config.port}, HOST={config.host}")

# 单一版本源：/health、FastAPI 元数据、启动横幅与页脚都取这里
VERSION = "0.13.30"   # 本机项目：/api/localprojects 多根 git 扫描 + cloudcli 合并去重（项目名/会话活跃度）
                      #   + POST /start 铸 JWT 转调创建会话 → 详情抽屉项目列表 + iframe 直达 /session/{id}；
                      #   MCP +hub_cloudcli_projects
                      #   （--embed-line / --embed-bg / --font-display / --on-accent），fr 轨道一律
                      #   minmax(0,…) 防内容顶破容器，数字列 tabular-nums 兜字体回退；DESIGN.md 新增
                      #   「视觉系统」语义索引章（权威源仍是 templates/index.html 的 :root，不复制取值）。
                      #   取值与原字面量逐字相同 ⇒ 渲染零变化；取证见 agent-knowledge/57（真渲染四档 + gate 50）。
                      #   上一版 v0.13.25 后端：终端进程退出时把「为什么没了」说清楚。waitpid 的退出状态原先被
                      #   `_st` 直接丢弃（src/term.py 的 _cleanup / _force_kill）⇒ 崩溃原因永远上不了屏，
                      #   用户只看到一句「[会话结束]」。新增 describe_exit() 把信号/退出码解成人话：
                      #   SIGILL/SIGSEGV/SIGBUS/SIGABRT/SIGKILL 点名「疑似内存不足」；并用 hub_killed
                      #   区分「hub 自己发的 SIGTERM/SIGKILL」（点 × / 空闲 TTL / 服务退出）与内核
                      #   OOM-killer ⇒ 绝不把用户主动关会话报成内存不足。API 侧 to_dict() 透出 exit_reason。
                      #   起因：2026-09-25 排查「菜单点 OpenCode 秒退」，只能靠 dmesg(trap invalid opcode)
                      #   + objdump(ud2) + ulimit -v 三步反推出 bun/JSC 的 MemoryExhaustion 主动 abort。
                      #   真因是整机 swap 耗尽（/vol1/.swap/swap2 那 4G 因开机顺序 + nofail 静默失效），
                      #   hub 代码本身无 bug —— 本次只补「可观测性」。详见 CHANGELOG。
                      #   上一版 v0.13.24 后端+前端：asset_audit 资产变更审计（append-only 表 + log_asset_event 写口径 + /api/audit/list）；
                      #   本地记忆便签 staleness 观测（memstats，**只报告不清理**）挂 /api/kb/status.local_memory；
                      #   chat 会话工具条加导出按钮（blob 下载、token 只走头、四态文案互斥）。
                      #   上一版 v0.13.23 后端：/health 补上游网关(CCR)连通性与模型注册清单 + 画像最近检测时间；
                      #   会话批量导出端点（JSON/CSV，默认脱敏，按写端点同等鉴权）；
                      #   MANAGER_LLM_BASE_URL 默认值由已退役的 FCC :8082 改回 CCR :3456。
                      #   版本号让位：本批原自命名 0.13.22，但 master 上 ff53581（终端页空格接力，纯前端）已占用该标签
                      #   ⇒ 本批改 0.13.23，避免两批共用一个版本号（详见 CHANGELOG）。
                      #   v0.13.21/22 均为纯前端批次，按项目口径
                      #   「VERSION 与清 code_stale 随下次后端改动同批」⇒ 本次一并 bump。
                      #   上一版（v0.13.20 FCC 退役收尾 / v0.13.19 P3 工具注册表 + P4 资产面板）明细见 CHANGELOG.md。

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


# ── P1-7 扩展：写端点鉴权闸门（实测 34 条写路由里 32 条此前不设防，6 条对匿名写回 200）。
#    Starlette 里后注册的中间件在最外层 ⇒ 本闸门先于 api_rate_limit：被拒的请求既不该占限流预算，
#    更不该走到 handler 里产生副作用（rebuild 重写记忆就是这类副作用）。 ──
app.middleware("http")(write_gate)

templates_dir = Path(__file__).parent.parent / "templates"
static_path = Path(__file__).parent.parent / "static"
templates = Jinja2Templates(directory=str(templates_dir))

# v0.5.2.7 自定义静态资源路由（替代原 StaticFiles mount）；v0.12.4 改口径：
# 原来是 no-store ⇒ 浏览器每进一次终端页都要重下 290KB 的 vendor/xterm.js，而且从不压缩。
# 现在 no-cache（每次仍回源校验）+ 自己处理 If-None-Match ⇒ 文件没变只回 304 空响应，
# 文件一改 ETag 就变 ⇒ 拿不到旧 JS（当初写 no-store 就是怕这个，304 同样防得住）。
if static_path.exists():
    import gzip
    import hashlib
    import mimetypes
    from fastapi import Request
    from fastapi.responses import FileResponse, Response

    _GZ_SUFFIX = {".js", ".css", ".svg", ".json", ".map"}
    _GZ_MIN = 1024
    _gz_cache: dict = {}   # "路径|mtime_ns|size" -> gzip 字节；键随文件变，天然失效

    @app.get("/static/{file_path:path}")
    async def _static_no_cache(file_path: str, request: Request):
        # 判据全部下沉到 src/staticguard.py（纯函数）：内联在路由里时，单测无法覆盖
        # —— import src.main 会触发 lifespan（开真库、起后台任务）。历史三条判据与
        # 实测红-绿见该模块 docstring。
        f = staticguard.resolve_serveable(static_path, file_path)
        if f is None:
            raise HTTPException(404)
        st = f.stat()
        # 与 FileResponse 同一套算法（md5("mtime-size")）仅用于 revalidate 分支。
        # 判据在 staticguard.cache_policy（纯函数、可单测）：URL 的 ?v= 等于文件内容哈希
        # 才许 immutable。09-23 自研 APP 事故的根治——旧口径下 ETag 由 mtime+size 算出，
        # 与 query、与 content-encoding 都无关 ⇒ 不同 ?v= 与 gzip/identity 共用同一个 ETag，
        # 条件请求可让端侧继续执行旧体（实测手机对 hub.js 先 200 后 304，且 ?v=22c 与 ?v=23a
        # 同 ETag）。immutable 分支不发校验器、永不回 304：换新体的唯一途径是 URL 变化本身。
        policy, want_tok = staticguard.cache_policy(
            (request.query_params.get("v") or "").strip(), f)
        headers = {"Vary": "Accept-Encoding", "X-Asset-Token": want_tok}
        if policy == "immutable":
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            etag = '"%s"' % hashlib.md5(
                f"{st.st_mtime}-{st.st_size}".encode(),
                usedforsecurity=False).hexdigest()
            headers.update({"Cache-Control": "no-cache", "Pragma": "no-cache", "ETag": etag})
            # 304 分支此前漏了 Vary：共享缓存可能把 gzip 版回给不接受 gzip 的客户端
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=headers)
        media = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if (f.suffix.lower() in _GZ_SUFFIX and st.st_size >= _GZ_MIN
                and "gzip" in (request.headers.get("accept-encoding") or "")):
            key = f"{f}|{st.st_mtime_ns}|{st.st_size}"
            body = _gz_cache.get(key)
            if body is None:
                body = gzip.compress(f.read_bytes(), 6)
                if len(_gz_cache) > 32:
                    _gz_cache.clear()
                _gz_cache[key] = body
            return Response(content=body, media_type=media,
                            headers={**headers, "Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        # 09-24：immutable 分支**不走 FileResponse**。它会在 __call__ 里 stat 并调
        # `set_stat_headers()`，自动补 `etag`/`last-modified`——两者都是 mtime+size 的函数，
        # 与 URL 上的内容哈希提手无关 ⇒ 客户端可能拿 304 复用"提手对不上"的旧副本
        # （09-23 APP 事故实测：?v=…22c 与 ?v=…23a 共用同一 ETag，先 200 后 304）。
        # 曾试图覆写 FileResponse.set_headers —— 那个钩子在本 Starlette 版本里不存在，
        # 方法永不执行（假动作，影子实测当场抓出）。这里直接给全量字节，与 gzip 分支同一做法，
        # 不依赖任何私有方法名。代价：不支持 Range；静态资源最大约 300KB，可接受。
        if policy == "immutable":
            return Response(content=f.read_bytes(), headers=headers, media_type=media)
        # revalidate 分支照旧：发 ETag、可回 304（提手不对/没提手时的安全阀）。
        return FileResponse(str(f), headers=headers, media_type=media)
    # 不再 mount StaticFiles；自定义路由接管 /static/

# 子路由（Hook / 记忆 / 指挥官）
app.include_router(hook_mod.router)
app.include_router(memory_mod.router)
app.include_router(memfed_mod.router)
app.include_router(kb_mod.router)
app.include_router(skill_mod.router)
app.include_router(tasks_mod.router)
app.include_router(mcpgw_mod.router)
app.include_router(cronjobs_mod.router)
app.include_router(term_mod.router)
app.include_router(audit_mod.router)
app.include_router(runlog_mod.router)
app.include_router(cloudcli_mod.router)
app.include_router(localprojects_mod.router)

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
    """自证端点（P0-2）：不只看活没活，还要能看出「跑的是哪份代码」。

    旧口径只回 {status,service,version,port} ⇒ 版本漂移（进程报 0.13.2 / HEAD 已 0.13.3）
    与「对话端点必 500」这类**静默不可用**全都看不见。
    新增字段全 additive；前端 pollHealth 只读 status，不会被改坏。
    code_stale 只兑情报、不改 status：代码改了没重启不等于服务坏了。
    """
    try:
        db.query("SELECT 1 FROM sqlite_master LIMIT 1")
        db_ok = True
    except Exception as e:  # noqa: BLE001
        db_ok = False
        print(f"[health] db 自检失败：{type(e).__name__}: {str(e)[:120]}", flush=True)
    out = {"status": "ok", "service": "agent-hub", "version": VERSION, "port": config.port,
           "db_ok": db_ok,
           "term_sessions": term_mod.alive_count(),
           "term_idle_max_s": term_mod.idle_max_s()}
    out.update(selfattest.snapshot())
    # 记忆后端体检（P0-6）：权威库在 TDAI，它挂不挂必须从 /health 能看出来。
    # 旧态是「/api/memory/search 永远回 count:0 且无任何错误字段」——全绿而功能层已死。
    # 纯读缓存不起网络（见 tdai_client.backend_status 注释），且**不改 status**：
    # 记忆后端不可用不等于 hub 坏了，同 code_stale 只兑情报的设计意图。
    out["memory_backend"] = tdai_client.backend_status()
    # 上游网关（CCR）连通性 + 模型注册清单 —— 0924 方案档 §三「health 增强（运维 P3→P2）」收口。
    # 为什么必须有：本机三次同源事故都是**模型 ID 失效而 /health 全绿**（09-06 `minimax-m3:free`
    # HTTP 400、09-19 `'ultra'` 无效、09-23 `qwen3.8-flash` 缺 provider 前缀）——即 09-22 定名的
    # 「静默不可用」家族。现在 watch 里的每个写死 ID 是否仍在册，直接是 /health 的可断言字段。
    # 纯读缓存（gwprobe 自己 stale-while-revalidate，TTL 300s），**不改 status**：
    # 上游网关不可达不等于 hub 坏了，与 code_stale / memory_backend 同一设计意图。
    out["ccr_gateway"] = gwprobe.status()
    # 画像最近检测时间（同属「health 增强」的另一半：CCR 连通性 + 画像检测时间 + DB 状态）。
    # 只给 last_sweep 会被「新一轮扫了 6 家、漏了第 7 家」骗过 ⇒ 必须给最坏值 oldest_check_age_s
    # 与 unchecked（在册却从没被扫到的家数，正是 09-22「在册却静默不可用 21 天」的形态）。
    try:
        out["profiles_last_check"] = healthx.profiles_last_check(
            vitals_mod.vitals.snapshot(), vitals_mod.vitals.last_sweep, vitals_mod.SWEEP_EVERY)
    except Exception as e:  # noqa: BLE001  # 情报字段不得把 /health 打挂
        out["profiles_last_check"] = {"state": "error",
                                     "error": f"{type(e).__name__}: {str(e)[:120]}"}
    if not db_ok:
        out["status"] = "degraded"
    return out


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
                                   "block_noul", "jev_error", "rt_state", "rt_flaky")}
    # 应答态与生死判定分两栏回：前端拿 rt_state 说明模型层，拿 verdict 决定颜色
    out["evidence"] = {kk: ev.get(kk) for kk in
                       ("agent_shape", "resolved_path", "version_rc", "run_rc", "run_ok",
                        "run_output", "run_note", "run_model", "run_evidence",
                        "evidence_sha", "endpoint_serving")}
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
    # P0-1 回归（实测取证）：v0.10.0 commit 2cf96fa 把 tools/repair_mode 从 ChatRequest
    # 字段表里删了，但调用点仍写 req.tools ⇒ 本端点从 09-20 起每请求必 500
    # （AttributeError），而 /health 全程 200、vitals 全绿 —— 直连对话框静默不可用三天。
    # 这两个参数在 v0.10.0 移除 hub-self 工具环后已无实体，**别再往回加**。
    # 钉死它的测：tests/test_pydantic_attr_drift.py（AST 静态取证，不导 main）
    result = await _chat_dispatch(agent_id, req.message, session_id, req.model,
                                  cwd=req.cwd, trace_id=trace_id)
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


@app.get("/api/sessions/export")
async def export_sessions(request: Request, format: str = "json", agent_id: Optional[str] = None,
                          limit: int = 1000, with_messages: int = 1, redact: int = 1):
    """批量导出 hub 自己的会话（0924 方案档 §三「会话导出 P2.5」）。

    三个刻意的设计决定：
      1) **按写端点同等鉴权**：服务绑 0.0.0.0:3102，批量导出正文是数据外流动作，影响面比
         单条 `/messages` 大一个量级。复用 `writeauth.decide`（fail-closed：服务端没配口令
         ⇒ 503 而不是放行），与 09-23「31 个写端点不设防」的收口同一口径。
      2) **默认脱敏**（`redact=1`）：本工作区三次被凭据外流打过（备份镜像 82 个活凭据文件、
         `wiki/log.md` 历史含 CCR web token、外发净仓被闸门拦下 3 个抄了真 token 的文档）。
         导出件正是最容易被顺手 commit/转发的形态；命中数在 meta 里如实回报，**不静默改数据**，
         要原始字节须显式 `redact=0`。
      3) **不导出外部 CLI 的历史会话**（claude/jcode/codex/opencode/grok/hermes 的 session store）：
         那是别的工具链的私有存档，批量外流属另一层隐私裁定，须用户点名；本端点只覆盖
         hub 自己库里的 `chat_sessions` / `chat_messages`。
    """
    verdict, reason = writeauth.decide(
        "POST", request.url.path,                       # 强制按写方法判：导出=数据外流
        writeauth.provided_token(request.headers.raw, request.url.query),
        writeauth.secrets_from_env())
    if verdict not in ("allow", "exempt"):
        print(f"[export] 拒绝 {verdict}：{request.url.path} "
              f"来源={request.client.host if request.client else '?'} —— {reason}", flush=True)
        raise HTTPException(status_code=503 if verdict == "misconfig" else 401, detail=reason)

    fmt = "csv" if str(format).lower() == "csv" else "json"
    n_lim = max(1, min(int(limit or 1000), 5000))       # 上限防一次性拖库打爆内存
    sql = ("SELECT s.id, s.agent_id, s.title, s.created_at, s.updated_at, "
           "(SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.id) AS messages "
           "FROM chat_sessions s")
    params: list = []
    if agent_id:
        sql += " WHERE s.agent_id=?"
        params.append(agent_id)
    sql += " ORDER BY s.updated_at DESC LIMIT ?"
    params.append(n_lim)
    rows = [dict(r) for r in db.query(sql, tuple(params))]

    meta = {"agent_id": agent_id or "*", "limit": n_lim, "with_messages": bool(with_messages)}
    if fmt == "csv" and with_messages:
        # CSV 是扁平表 ⇒ 导出正文时以「一行一条消息」呈现（表头恒定，下游可断言）
        mrows: list = []
        for r in rows:
            for m in db.query("SELECT role,content,created_at FROM chat_messages "
                              "WHERE session_id=? ORDER BY id ASC", (r.get("id"),)):
                mrows.append({"session_id": r.get("id"), "role": m.get("role"),
                              "created_at": m.get("created_at"), "content": m.get("content")})
        body, ctype, fname, meta = export_mod.render(
            mrows, export_mod.MESSAGE_COLUMNS, "csv", "messages", meta=meta, redact=bool(redact))
    else:
        if with_messages and fmt == "json":
            for r in rows:
                r["transcript"] = [
                    {"role": m.get("role"), "created_at": m.get("created_at"),
                     "content": m.get("content")}
                    for m in db.query("SELECT role,content,created_at FROM chat_messages "
                                      "WHERE session_id=? ORDER BY id ASC", (r.get("id"),))]
        body, ctype, fname, meta = export_mod.render(
            rows, export_mod.SESSION_COLUMNS, fmt, "sessions", meta=meta, redact=bool(redact))

    print(f"[export] {meta.get('kind')} fmt={fmt} rows={meta.get('count')} "
          f"redacted={'yes' if redact else 'no'} hits={meta.get('redacted_hits')} "
          f"来源={request.client.host if request.client else '?'}", flush=True)
    return Response(content=body, media_type=ctype,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"',
                             "X-Export-Count": str(meta.get("count", 0)),
                             "X-Export-Redacted-Hits": str(meta.get("redacted_hits", 0))})


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
    """发现源扫描（docker/systemd/CLI 名单，全部只读探测）

    2026-09-24：扫描收尾补一次「定向重判」。原实现只跑 scanner.run_scan，
    而它「仅报告安装状态（不注册）」（见 scanner.py 顶部注释），卡片补发由
    profiles._dynamic_cli_agents 负责，其闸门读的是**上一轮 vitals 结论**
    ⇒ 点「自动扫描」永远刷不动一枚陈旧 not_installed，新装 CLI 出不来（opencode 实例）。
    重判只走 L1/L2（which / --version / --help），不碰模型、不烧 token。"""
    result = await asyncio.to_thread(scanner.run_scan, body.auto_register, db)
    rejudge: dict = {}
    try:
        rejudge = await asyncio.to_thread(
            vitals_mod.vitals.rejudge_stale, _agent_profs_for_vitals())
    except Exception as e:  # noqa: BLE001  重判挂了不能把扫描本身弄失败
        rejudge = {"error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    result["vitals_rejudge"] = rejudge
    need_reload = bool(rejudge.get("changed")) or bool(
        body.auto_register and result.get("added"))
    if need_reload and discovery:
        if rejudge.get("changed"):
            profiles_mod.invalidate_cli_cache()   # 30s 候补缓存不得压住刚翻案的卡
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
    resp = templates.TemplateResponse(request, "index.html", {"version": VERSION})
    # 2026-09-26：HTML 原先不带任何缓存头/验证器 ⇒ 浏览器与已开标签页长期不自愈，
    # 部署新版后用户看到的仍是旧 UI。no-cache = 可存，但每次导航必须回源校验。
    resp.headers["Cache-Control"] = "no-cache"
    return resp


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


async def prov_loop():
    """代码溯源刷新：每 30s 一次 git status（走线程，不进任何请求路径）。

    为什么不搭 vitals 的慢拍：VITALS_SWEEP_SEC 默认 900s，而“工作区改了没”是
    健康台账里最想要分钟级响应的字段；搭慢拍会让它在 15 分钟里拿着旧值讲现测。
    """
    while True:
        try:
            await asyncio.to_thread(selfattest.refresh)
        except Exception as e:  # noqa: BLE001
            print("[prov] 溯源刷新异常（下轮重试）%s: %s" % (
                type(e).__name__, str(e)[:160]), flush=True)
        await asyncio.sleep(float(os.getenv("HUB_PROV_SEC", "30")))


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
    # 上游网关注入（/health 的 ccr_gateway 情报源）。watch 放两个「写死在配置里的模型 ID」：
    # manager 用的那个 + vitals L4 探针用的那个。上游一旦改名/下架，watch.<id>=false 当天可见，
    # 不必等探活烧一轮 token 才发现（09-23 的 M1 阻塞「拿不到在线清单」就此长期解除）。
    gwprobe.configure(config.manager_llm_base, config.manager_llm_key,
                      watch=[config.manager_llm_model, vitals_mod.RT_MODEL])
    tasks_mod.ensure_schema()
    tasks_mod.set_context(
        chat_fn=lambda a, m, s=None, mo=None, tr=None: _chat_dispatch(a, m, s, mo, tr),
        agent_ids_fn=lambda: [c["id"] for c in discovery.all_configs()])
    asyncio.create_task(tasks_mod.sweep_stale_tasks())
    asyncio.create_task(vitals_loop())
    asyncio.create_task(prov_loop())   # 代码溯源（工作区脏度）刷新
    # P0-4：终端会话回收必须有独立心跳，不能寄生在前端轮询上
    asyncio.create_task(term_mod.reap_loop())
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
    sa = selfattest.boot()   # 记下启动那一刻的 sha，供 /health 判 code_stale
    print(f"[Agent Hub] 启动完成 v{VERSION} sha={sa['git_sha_boot'] or '?'}，"
          f"监听 {config.host}:{config.port}")
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
