"""Manager Agent —— 自然语言指挥官（Agent_Manager 核心卖点的 Web 化复刻）

上游机制：LLM + 工具环（thought/toolcall/toolresult/answer 步骤流），
工具结果中嵌入 __action__ 标记由前端执行（打开 UI 等）。本实现保持一致。

上游 TUI 类 Agent 的「启动/停止进程」在 NAS 无头环境不适用
（服务由 systemd/其他守护管理），此处提供「打开界面/查询/对话/记忆」类工具。
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import db
import llm

router = APIRouter()

# 运行期由 main.py startup 注入
_ctx: Dict[str, Any] = {}


def set_context(discovery=None, config=None, chat_fn=None):
    if discovery:
        _ctx["discovery"] = discovery
    if config:
        _ctx["config"] = config
    if chat_fn:
        _ctx["chat_fn"] = chat_fn


SYSTEM_PROMPT = """你是 Agent Hub 的 Manager（指挥官），管理本机 AI Agent。
可用工具查询 Agent 状态、与 Agent 对话、检索记忆、查看端口。
规则：
- 回答用户前先用工具取真实状态，不臆测；
- 「打开某 Agent 界面」调用 open_agent_ui；
- 用户表达了值得长期记住的偏好/决策时调用 add_memory；
- 最终用简洁中文汇报。"""

TOOLS = [
    {"type": "function", "function": {
        "name": "list_agents", "description": "列出所有 Agent 及实时状态",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_agent", "description": "查询单个 Agent 详情",
        "parameters": {"type": "object", "properties": {
            "agent_id": {"type": "string"}}, "required": ["agent_id"]}}},
    {"type": "function", "function": {
        "name": "chat_with_agent", "description": "向指定 Agent 发送消息并获取回复",
        "parameters": {"type": "object", "properties": {
            "agent_id": {"type": "string"},
            "message": {"type": "string"}}, "required": ["agent_id", "message"]}}},
    {"type": "function", "function": {
        "name": "open_agent_ui", "description": "获取指定 Agent 的 Web 界面地址（返回 action）",
        "parameters": {"type": "object", "properties": {
            "agent_id": {"type": "string"}}, "required": ["agent_id"]}}},
    {"type": "function", "function": {
        "name": "search_memory", "description": "检索记忆中心 L1 记忆",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_memory_layers", "description": "查看 L2 工作记忆与 L3 Profile 概览",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "add_memory", "description": "把用户的偏好/决策写入记忆中心 L1",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string"},
            "category": {"type": "string", "enum": ["fact", "decision", "constraint", "preference"]},
            "source": {"type": "string"}}, "required": ["content"]}}},
    {"type": "function", "function": {
        "name": "list_ports", "description": "列出本机监听端口及归属进程（可过滤端口号）",
        "parameters": {"type": "object", "properties": {
            "port": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "list_sessions", "description": "查询统一对话/会话历史摘要",
        "parameters": {"type": "object", "properties": {
            "agent_id": {"type": "string"}, "limit": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "mcp_tools", "description": "列出 MCP 聚合网关中所有可用工具（server+tool）",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "mcp_call", "description": "调用 MCP 网关工具（经 ACL 与限流）",
        "parameters": {"type": "object", "properties": {
            "server": {"type": "string", "description": "server id 或名称"},
            "tool": {"type": "string"},
            "args": {"type": "object"}}, "required": ["server", "tool"]}}},
]


class ManagerChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: Optional[str] = None


def _ui_url_for(agent_id: str) -> Optional[str]:
    # 画像驱动：agent 的 ui / 其他实体的 panel / 自定义项 endpoint
    try:
        import profiles
        p = profiles.get_profile(agent_id)
        if p:
            return p.get("ui") or p.get("panel")
    except Exception:  # noqa: BLE001
        pass
    discovery = _ctx.get("discovery")
    if discovery:
        agent = discovery.get_agent(agent_id)
        if agent and agent.endpoint:
            return agent.endpoint
    return None


async def _dispatch_tool(name: str, args: Dict[str, Any]):
    discovery = _ctx.get("discovery")
    if name == "list_agents":
        if not discovery:
            return {"error": "discovery not ready"}
        agents = await discovery.discover_all()
        return {"agents": [a.to_dict() for a in agents]}
    if name == "get_agent":
        agent = discovery.get_agent(args["agent_id"]) if discovery else None
        return agent.to_dict() if agent else {"error": "not found"}
    if name == "chat_with_agent":
        chat_fn = _ctx.get("chat_fn")
        if not chat_fn:
            return {"error": "chat not ready"}
        return await chat_fn(args["agent_id"], args["message"])
    if name == "open_agent_ui":
        url = _ui_url_for(args["agent_id"])
        if not url:
            return {"error": f"{args['agent_id']} 无 Web UI"}
        # 服务端真解析（上游 __action__ 假成功模式已弃用）；前端按结构化字段渲染动作
        return {"ok": True, "url": url, "action": "open_url"}
    if name == "search_memory":
        rows = db.query("SELECT id,category,content,source,created_at FROM memories "
                        "WHERE status='active' AND content LIKE ? ORDER BY id DESC LIMIT ?",
                        (f"%{args['query']}%", int(args.get("limit") or 8)))
        return {"memories": rows}
    if name == "get_memory_layers":
        l2 = db.query("SELECT * FROM memory_docs WHERE layer='L2'")
        l3 = db.query("SELECT * FROM memory_docs WHERE layer='L3'")
        def brief(d):
            if not d:
                return ""
            row = d[0]
            return ((row["content"] or "")[:1500] + "\n[manual]\n" + (row["manual"] or ""))
        return {"L2": brief(l2), "L3": brief(l3)}
    if name == "add_memory":
        cat = args.get("category") or "fact"
        if cat not in ("fact", "decision", "constraint", "preference"):
            cat = "fact"
        mid = db.add_memory(args["content"], cat, args.get("source") or "manager")
        return {"id": mid, "status": "saved"}
    if name == "list_ports":
        import ports as ports_mod
        rows = ports_mod.list_listeners()
        if args.get("port"):
            rows = [r for r in rows if r["port"] == int(args["port"])]
        return {"listeners": rows[:100], "count": len(rows)}
    if name == "list_sessions":
        limit = int(args.get("limit") or 10)
        sql = ("SELECT s.id,s.agent_id,s.title,s.updated_at,"
               "(SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.id) AS messages "
               "FROM chat_sessions s")
        params: list = []
        if args.get("agent_id"):
            sql += " WHERE s.agent_id=?"
            params.append(args["agent_id"])
        sql += " ORDER BY s.updated_at DESC LIMIT ?"
        params.append(limit)
        return {"sessions": db.query(sql, tuple(params))}
    if name == "mcp_tools":
        import mcpgw
        try:
            return await mcpgw.aggregated_tools()
        except Exception as e:  # noqa: BLE001
            return {"error": repr(e)[:300]}
    if name == "mcp_call":
        import mcpgw
        try:
            return await mcpgw.mcp_call(mcpgw.CallIn(
                server=args["server"], tool=args["tool"],
                args=args.get("args") or {}, agent_id="manager"))
        except Exception as e:  # noqa: BLE001
            detail = getattr(e, "detail", None)
            return {"error": str(detail or e)[:400]}
    return {"error": f"unknown tool {name}"}


@router.post("/api/manager/chat")
async def manager_chat(body: ManagerChatIn):
    if not llm.configured():
        raise HTTPException(503, "Manager LLM 未配置")
    session_id = body.session_id or uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    db.execute("INSERT OR IGNORE INTO manager_messages(session_id,role,content,created_at) "
               "VALUES(?,?,?,?)", (session_id, "user", body.message, now))
    # 历史（近 10 条）
    hist = db.query("SELECT role,content FROM manager_messages WHERE session_id=? "
                    "ORDER BY id DESC LIMIT 10", (session_id,))
    # 上游 llm.rs 同款：system prompt 内嵌实时 Agent 快照
    snapshot = ""
    discovery = _ctx.get("discovery")
    if discovery:
        try:
            agents = await discovery.discover_all()
            snapshot = "\n\n当前 Agent 快照：\n" + "\n".join(
                f"- {a.name} (id={a.id}, status={a.status}, port={a.port}, "
                f"desc={a.description[:40]})" for a in agents)
        except Exception:  # noqa: BLE001
            pass
    messages = [{"role": "system", "content": SYSTEM_PROMPT + snapshot}]
    messages += [{"role": r["role"], "content": r["content"]} for r in reversed(hist)]
    try:
        t_total = __import__("time").monotonic()

        async def timed_dispatch(name, args):
            t0 = __import__("time").monotonic()
            ok = False
            try:
                out = await _dispatch_tool(name, args)
                ok = not (isinstance(out, dict) and out.get("error"))
                return out
            finally:
                db.log_profile_event(
                    "manager_tool", name, "success" if ok else "fail",
                    int((__import__("time").monotonic() - t0) * 1000),
                    trace_id=session_id)

        answer, steps = await llm.chat_tools_loop(messages, TOOLS, timed_dispatch,
                                                  max_rounds=6)
        db.log_profile_event("manager_chat", "manager", "success",
                             int((__import__("time").monotonic() - t_total) * 1000),
                             trace_id=session_id)
    except Exception as e:  # noqa: BLE001
        db.log_profile_event("manager_chat", "manager", "fail", None, trace_id=session_id)
        db.execute("INSERT INTO manager_messages(session_id,role,content,created_at) "
                   "VALUES(?,?,?,?)", (session_id, "assistant", f"[LLM 错误] {e}", now))
        return {"session_id": session_id, "answer": None,
                "error": str(e)[:500],
                "hint": "检查 .env 的 MANAGER_LLM_API_KEY 与 FCC(:8082) 是否可达"}
    db.execute("INSERT INTO manager_messages(session_id,role,content,steps,created_at) "
               "VALUES(?,?,?,?,?)", (session_id, "assistant", answer,
                                     json.dumps(steps, ensure_ascii=False), now))
    return {"session_id": session_id, "answer": answer, "steps": steps}


@router.get("/api/manager/history")
async def manager_history(session_id: str = Query(min_length=1), limit: int = 50):
    rows = db.query("SELECT role,content,steps,created_at FROM manager_messages "
                    "WHERE session_id=? ORDER BY id ASC LIMIT ?", (session_id, limit))
    for r in rows:
        r["steps"] = json.loads(r["steps"]) if r["steps"] else []
    return {"messages": rows}
