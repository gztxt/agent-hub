"""Hub MCP Server —— 把 agent-hub 的本机事实源以 MCP 形式暴露给外部 Agent（Hermes 等）。

设计原则（v2.1 方案 D2）：
- **只读优先**：默认只暴露读取类工具；写工具需显式设 HUB_MCP_ALLOW_WRITE=1 才注册。
- **不重复实现业务**：一律转发 hub 自身 REST（/api/*），事实源唯一。
- **依赖最小化**：用标准库 urllib，不引入 httpx 等新依赖（hub venv 无 httpx）。
- **路径无冲突**：由 main.py 挂载到 /hub-mcp，实际 MCP 端点是 /hub-mcp/mcp
  （streamable_http_app 自带 /mcp 子路由，故不能挂到 /mcp，否则与 mcpgw 的 /mcp/* REST 冲突）。

注册工具：hub_list_agents / hub_list_ports / hub_memory_search / hub_memory_context
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from mcp.server.mcpserver import MCPServer

HUB_URL = os.getenv("HUB_MCP_SELF_URL", "http://127.0.0.1:3102")
TIMEOUT_S = float(os.getenv("HUB_MCP_TIMEOUT", "10"))
ALLOW_WRITE = os.getenv("HUB_MCP_ALLOW_WRITE", "0") == "1"

server = MCPServer("agent-hub")


def _get(path: str, **params) -> dict:
    """转发 GET 到 hub 自身 REST；失败时返回带 error 字段的 dict 而非抛异常，
    避免 MCP 会话被单个接口故障打断。"""
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    url = f"{HUB_URL}{path}" + (f"?{qs}" if qs else "")
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"hub REST {e.code}", "path": path}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "path": path}


@server.tool()
def hub_list_agents(kind: str = "", status: str = "") -> str:
    """列出本机已探测到的实体（agent/gateway/service/memory/tool）及运行状态与访问入口。
    kind 可填 agent|gateway|service|memory|tool；status 可填 running|installed|stopped。都留空则返回全部。"""
    data = _get("/api/agents")
    agents = data.get("agents", data if isinstance(data, list) else [])
    if kind:
        agents = [a for a in agents if a.get("kind") == kind]
    if status:
        agents = [a for a in agents if a.get("status") == status]
    slim = [{
        "id": a.get("id"), "kind": a.get("kind"), "status": a.get("status"),
        "port": a.get("port"), "desc": (a.get("desc") or "")[:80],
    } for a in agents]
    return json.dumps({"count": len(slim), "agents": slim}, ensure_ascii=False)


@server.tool()
def hub_list_ports(agent: str = "") -> str:
    """列出本机 TCP 监听端口及归属进程，用于端口冲突排查与网络诊断。agent 非空时按归属过滤。"""
    data = _get("/api/ports")
    ports = data.get("ports", data if isinstance(data, list) else [])
    if agent:
        ports = [p for p in ports if agent.lower() in str(p.get("process", "")).lower()
                 or agent.lower() in str(p.get("owner", "")).lower()]
    slim = [{
        "port": p.get("port"), "addr": p.get("addr"), "pid": p.get("pid"),
        "process": p.get("process"),
    } for p in ports]
    return json.dumps({"count": len(slim), "ports": slim}, ensure_ascii=False)


@server.tool()
def hub_memory_search(q: str, limit: int = 10) -> str:
    """按关键词检索 hub 三层记忆（L1 事实/L2 工作记忆/L3 画像），无命中时自动透传 TDAI。
    返回命中的记忆条目；这是找回本机历史决策与约束的首选工具。"""
    if limit > 50:
        limit = 50
    data = _get("/api/memory/search", q=q, limit=limit)
    if "error" in data:
        return json.dumps(data, ensure_ascii=False)
    return json.dumps({
        "engine": data.get("engine"), "count": data.get("count", 0),
        "memories": [{
            "id": m.get("id"), "layer": m.get("layer"), "category": m.get("category"),
            "content": (m.get("content") or "")[:400],
        } for m in data.get("memories", [])],
        "tdai": bool(data.get("tdai")),
    }, ensure_ascii=False)


@server.tool()
def hub_memory_context() -> str:
    """获取开局上下文包（L3 画像 + L2 工作记忆 + 相关 L1 事实）。新会话初始化时先调它，
    避免在不知道本机约定与约束的情况下动手。"""
    data = _get("/api/memory/context")
    if "error" in data:
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(data, ensure_ascii=False)[:6000]


if ALLOW_WRITE:
    @server.tool()
    def hub_memory_add(content: str, category: str = "fact", layer: str = "L1") -> str:
        """写入一条 hub 记忆。默认不注册——需 HUB_MCP_ALLOW_WRITE=1 才启用。
        category 可填 fact|decision|constraint|preference。"""
        req = urllib.request.Request(
            f"{HUB_URL}/api/memory/l1",
            data=json.dumps({"content": content, "category": category, "layer": layer}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


def build_app():
    """返回可挂载到 FastAPI 的 Starlette 子应用。"""
    return server.streamable_http_app()
