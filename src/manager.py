"""Manager Agent —— 自然语言指挥官（Agent_Manager 核心卖点的 Web 化复刻）

上游机制：LLM + 工具环（thought/toolcall/toolresult/answer 步骤流），
工具结果中嵌入 __action__ 标记由前端执行（打开 UI 等）。本实现保持一致。

上游 TUI 类 Agent 的「启动/停止进程」在 NAS 无头环境不适用
（服务由 systemd/其他守护管理），此处提供「打开界面/查询/对话/记忆」类工具。
"""
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import db
import llm

router = APIRouter()

# 运行期由 main.py startup 注入
_ctx: Dict[str, Any] = {}

# ── 修复工具白名单（hard-coded）───────────────────────────
# 仅以下 systemctl unit 可被 safe_restart；增删需改代码并提交
SAFE_RESTART_UNITS = frozenset({
    "agent-hub", "agent-hub-self", "ccr", "proxy-panel",
    "pi-web", "cloudcli", "ccpocket-bridge", "fcc-refresh-free",
})

# 仅以下配置文件可被 config_write/rollback；CCR/FCC 永禁写
SAFE_WRITE_PATHS = [
    str(Path(os.path.expanduser("~")) / ".config" / "jcode" / "config.toml"),
    str(Path(os.path.expanduser("~")) / ".claude" / "settings.json"),
    str(Path(os.path.expanduser("~")) / ".claude" / "settings.local.json"),
    str(Path(os.path.expanduser("~")) / ".hermes" / "config.yaml"),
]
SAFE_WRITE_UNITS_GLOB = str(Path(os.path.expanduser("~")) / ".config" / "systemd" / "user" / "*.service")

BACKUP_DIR = Path(__file__).resolve().parent.parent / "data" / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _safe_backup(path: str, reason: str) -> Tuple[str, str]:
    """时间戳备份；返回 (backup_path, sha256_prefix)。失败抛 RuntimeError。"""
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"目标不存在: {path}")
    sha = hashlib.sha256(p.read_bytes()).hexdigest()[:8]
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(c if c.isalnum() or c in "-_" else "-" for c in reason)[:40].strip("-") or "backup"
    dest = BACKUP_DIR / f"{ts}-{p.name}-{safe_reason}-{sha}.bak"
    shutil.copy2(p, dest)
    return str(dest), sha


def _unit_status(unit: str) -> Dict[str, Any]:
    """读 systemctl is-active / is-enabled；不抛。"""
    def _run(args: List[str]) -> str:
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=4)
            return (r.stdout or r.stderr or "").strip()
        except Exception as exc:  # noqa: BLE001
            return f"err:{type(exc).__name__}"
    return {
        "unit": unit,
        "active": _run(["systemctl", "--user", "is-active", unit]),
        "enabled": _run(["systemctl", "--user", "is-enabled", unit]),
    }


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
    # ── 修复能力工具（v0.4 起，对话框默认不暴露；UI「修复模式」开启才可用）──
    {"type": "function", "function": {
        "name": "agent_health", "description": "单个 Agent/服务 一页画像：profile、监听、配置 mtime、journal 尾 10 行。仅只读，不动任何东西。",
        "parameters": {"type": "object", "properties": {
            "agent_id": {"type": "string"}}, "required": ["agent_id"]}}},
    {"type": "function", "function": {
        "name": "safe_restart", "description": "重启白名单内 systemd user 单元（agent-hub/ccr/proxy-panel/pi-web/cloudcli/ccpocket-bridge/fcc-refresh-free/agent-hub-self）。先做时间戳备份再 restart；agent-hub 重启会断本对话，请提前知会用户。",
        "parameters": {"type": "object", "properties": {
            "unit": {"type": "string"},
            "reason": {"type": "string", "description": "为什么重启（写入备份名+审计）"}},
            "required": ["unit", "reason"]}}},
    {"type": "function", "function": {
        "name": "config_show", "description": "读取并时间戳备份指定配置文件（白名单内），返回 backup 路径与 sha256 前 8 位。绝不写回。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "绝对路径，限于 jcode/claude/hermes 配置 + ~/.config/systemd/user/*.service"},
            "reason": {"type": "string"}}, "required": ["path", "reason"]}}},
    {"type": "function", "function": {
        "name": "config_write", "description": "【双次确认】写白名单内配置。第一次调用仅试写+备份+比对，存为 pending_write；hub-self 第二次被问「执行 pending_write_id=xxx 吗」时答 yes 才真写。任意失败可 rollback。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "new_content": {"type": "string", "description": "完整文件新内容（覆盖式）"},
            "reason": {"type": "string"},
            "pending_write_id": {"type": "string", "description": "首次调用留空；二次确认时填上次返回的 id"}},
            "required": ["path", "new_content", "reason"]}}},
    {"type": "function", "function": {
        "name": "rollback", "description": "用 backup_id 把指定文件还原到 data/backups/ 下对应备份。仅还原白名单内文件。",
        "parameters": {"type": "object", "properties": {
            "backup_id": {"type": "string", "description": "文件名（不含目录），如 20260907_182856-config.toml-manual-fix-aabbccdd.bak"}},
            "required": ["backup_id"]}}},
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

    # ── 修复能力：只读画像 ───────────────────────────────────
    if name == "agent_health":
        agent_id = args.get("agent_id") or ""
        discovery = _ctx.get("discovery")
        agent = discovery.get_agent(agent_id) if discovery else None
        if not agent:
            return {"error": f"agent {agent_id} 不存在"}
        ad = agent.to_dict()
        # 端口探测
        import ports as ports_mod
        listeners = [r for r in ports_mod.list_listeners()
                     if r.get("port") and r["port"] == ad.get("port")] if ad.get("port") else []
        # 配置 mtime / sha
        cfg = []
        for cp in ([ad.get("config_path")] if ad.get("config_path") else []):
            try:
                p = Path(cp)
                if p.exists():
                    raw = p.read_bytes()
                    cfg.append({"path": cp,
                                "size": len(raw),
                                "mtime": p.stat().st_mtime,
                                "sha256_8": hashlib.sha256(raw).hexdigest()[:8]})
            except Exception as exc:  # noqa: BLE001
                cfg.append({"path": cp, "error": str(exc)[:120]})
        # 对应 systemd unit journal 尾部（按惯例命名 agent_id.service）
        unit = f"{agent_id}.service" if agent_id in SAFE_RESTART_UNITS else None
        journal_tail = []
        if unit:
            try:
                r = subprocess.run(
                    ["journalctl", "--user", "-u", unit, "-n", "10", "--no-pager", "-q"],
                    capture_output=True, text=True, timeout=4)
                journal_tail = (r.stdout or "").splitlines()[-10:]
            except Exception as exc:  # noqa: BLE001
                journal_tail = [f"err:{type(exc).__name__}"]
        return {
            "agent": ad,
            "listeners": listeners,
            "config": cfg,
            "journal_tail": journal_tail,
            "active_in_white_list": agent_id in SAFE_RESTART_UNITS,
        }

    # ── 修复能力：白名单 restart ─────────────────────────────
    if name == "safe_restart":
        unit = (args.get("unit") or "").strip()
        reason = (args.get("reason") or "").strip()[:120]
        if not unit or not reason:
            return {"error": "unit 与 reason 必填"}
        if unit not in SAFE_RESTART_UNITS:
            return {"error": f"{unit} 不在白名单",
                    "hint": f"可重启: {sorted(SAFE_RESTART_UNITS)}"}
        # 备份单元文件
        unit_path = Path(os.path.expanduser("~")) / ".config" / "systemd" / "user" / f"{unit}.service"
        backup_info = None
        if unit_path.exists():
            try:
                bp, sha = _safe_backup(str(unit_path), f"pre-restart-{reason}")
                backup_info = {"path": bp, "sha256_8": sha}
            except Exception as exc:  # noqa: BLE001
                return {"error": f"备份失败: {exc}"}
        before = _unit_status(unit)
        try:
            r = subprocess.run(
                ["systemctl", "--user", "restart", unit],
                capture_output=True, text=True, timeout=15)
            exit_code = r.returncode
            stderr = (r.stderr or "")[:300]
        except Exception as exc:  # noqa: BLE001
            return {"error": f"restart 调用失败: {exc}"}
        time.sleep(2)
        after = _unit_status(unit)
        return {"unit": unit, "reason": reason, "backup": backup_info,
                "before": before, "after": after,
                "exit_code": exit_code, "stderr": stderr,
                "warning": "agent-hub 自重启会断本对话" if unit == "agent-hub" else None}

    # ── 修复能力：配置快照（只读 + 备份，绝不写回）──────────
    if name == "config_show":
        path = (args.get("path") or "").strip()
        reason = (args.get("reason") or "").strip()[:120]
        if not path or not reason:
            return {"error": "path 与 reason 必填"}
        # 白名单校验：每次重算 glob（systemd 单元可能新增）
        import glob as _glob
        allowed_set = set(SAFE_WRITE_PATHS) | set(_glob.glob(SAFE_WRITE_UNITS_GLOB))
        if path not in allowed_set:
            return {"error": f"{path} 不在白名单",
                    "hint": "可写白名单：" + ", ".join(sorted(allowed_set))[:400],
                    "note": "CCR/FCC .env 永禁写"}
        if not Path(path).exists():
            return {"error": f"{path} 不存在"}
        try:
            bp, sha = _safe_backup(path, f"show-{reason}")
        except Exception as exc:  # noqa: BLE001
            return {"error": f"备份失败: {exc}"}
        content = Path(path).read_text(errors="replace")
        return {"path": path, "size": len(content.encode("utf-8")),
                "sha256_8": sha, "backup": bp,
                "content_preview": content[:2000],
                "content_truncated": len(content) > 2000}

    # ── 修复能力：双次确认写 ─────────────────────────────────
    if name == "config_write":
        path = (args.get("path") or "").strip()
        new_content = args.get("new_content") or ""
        reason = (args.get("reason") or "").strip()[:120]
        pending_id = (args.get("pending_write_id") or "").strip()
        if not path or not reason:
            return {"error": "path / new_content / reason 必填"}
        import glob as _glob
        allowed_set = set(SAFE_WRITE_PATHS) | set(_glob.glob(SAFE_WRITE_UNITS_GLOB))
        if path not in allowed_set:
            return {"error": f"{path} 不在白名单"}
        if not Path(path).exists():
            return {"error": f"{path} 不存在（config_write 是覆盖式）"}
        sha_new = hashlib.sha256(new_content.encode("utf-8")).hexdigest()[:8]
        if not pending_id:
            # 第一次：仅试写备份 + 比对，不落盘
            try:
                bp, sha_old = _safe_backup(path, f"pending-{reason}")
            except Exception as exc:  # noqa: BLE001
                return {"error": f"备份失败: {exc}"}
            pid = uuid.uuid4().hex[:12]
            # 把 pending 暂存到 db（chat_sessions meta 在外部管理，这里放轻量临时文件）
            pending_file = BACKUP_DIR / f"pending-{pid}.json"
            pending_file.write_text(json.dumps({
                "pending_write_id": pid, "path": path, "new_content": new_content,
                "old_sha8": sha_old, "new_sha8": sha_new, "reason": reason,
                "backup": bp, "created_at": time.time(),
            }, ensure_ascii=False))
            return {"phase": "pending",
                    "pending_write_id": pid,
                    "message": "第一次调用：仅做了试写备份，未落盘。请再用同一 id 调一次确认执行。",
                    "backup": bp, "old_sha8": sha_old, "new_sha8": sha_new,
                    "diff_bytes": len(new_content.encode("utf-8")) - Path(path).stat().st_size}
        # 第二次：找 pending 校验后真写
        pending_file = BACKUP_DIR / f"pending-{pending_id}.json"
        if not pending_file.exists():
            return {"error": f"pending_write_id={pending_id} 不存在或已过期"}
        rec = json.loads(pending_file.read_text())
        if rec.get("path") != path or rec.get("new_sha8") != sha_new:
            return {"error": "pending 与当前请求不一致（path 或 content 改了），请重新走 config_write"}
        # 二次备份（防止用户在 pending 期间文件又被别处改过）
        try:
            bp2, sha_now = _safe_backup(path, f"pre-commit-{reason}")
        except Exception as exc:  # noqa: BLE001
            return {"error": f"二次备份失败: {exc}"}
        if sha_now != rec["old_sha8"]:
            return {"error": f"文件自首次备份后被外部修改（sha {rec['old_sha8']} -> {sha_now}），已拒绝写入。请重新走 config_write。",
                    "old_sha8": rec["old_sha8"], "current_sha8": sha_now}
        # 真写
        try:
            Path(path).write_text(new_content)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"写入失败: {exc}"}
        sha_after = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:8]
        # 若是 systemd unit 文件，自动 daemon-reload
        reloaded = False
        if path.endswith(".service"):
            try:
                subprocess.run(["systemctl", "--user", "daemon-reload"],
                               capture_output=True, timeout=8)
                reloaded = True
            except Exception:  # noqa: BLE001
                reloaded = False
        # 清理 pending
        try: pending_file.unlink()
        except Exception: pass
        return {"phase": "committed",
                "path": path, "reason": reason,
                "expected_sha8": sha_new, "actual_sha8": sha_after,
                "backup": bp2, "systemd_reloaded": reloaded}

    # ── 修复能力：回滚 ───────────────────────────────────────
    if name == "rollback":
        bid = (args.get("backup_id") or "").strip()
        if not bid or "/" in bid or ".." in bid:
            return {"error": "backup_id 必填且不可含路径分隔符"}
        src = BACKUP_DIR / bid
        if not src.exists():
            return {"error": f"{bid} 不存在"}
        # 推断原始路径：备份文件命名 ts-<原名>-<reason>-<sha>.bak
        # 原名段含 1 个或多个 '-'，从尾部截 sha8 + 倒数 reason（可含 -）
        stem = bid[:-4]  # 去掉 .bak
        parts = stem.split("-")
        if len(parts) < 4:
            return {"error": "备份名格式不符，无法推断原路径"}
        sha8 = parts[-1]
        orig_name = parts[1]  # 仅取原文件名段
        # 找原始路径
        candidates = []
        if orig_name.endswith(".service"):
            candidates.append(Path(os.path.expanduser("~")) / ".config" / "systemd" / "user" / orig_name)
        elif orig_name == "config.toml":
            candidates.append(Path(os.path.expanduser("~")) / ".config" / "jcode" / "config.toml")
        elif orig_name in ("settings.json", "settings.local.json"):
            candidates.append(Path(os.path.expanduser("~")) / ".claude" / orig_name)
        elif orig_name == "config.yaml":
            candidates.append(Path(os.path.expanduser("~")) / ".hermes" / "config.yaml")
        # 用白名单兜底
        import glob as _glob
        allowed_set = set(SAFE_WRITE_PATHS) | set(_glob.glob(SAFE_WRITE_UNITS_GLOB))
        target = next((c for c in candidates if str(c) in allowed_set), None)
        if not target:
            return {"error": "无法在白名单内找到原路径",
                    "hint": "请用 config_write 重写而不是 rollback"}
        # 落盘前再做一次当前值备份
        try:
            guard_bp, _ = _safe_backup(str(target), f"pre-rollback-{bid[:24]}")
        except Exception as exc:  # noqa: BLE001
            return {"error": f"当前值备份失败: {exc}"}
        try:
            shutil.copy2(src, target)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"还原失败: {exc}"}
        reloaded = False
        if str(target).endswith(".service"):
            try:
                subprocess.run(["systemctl", "--user", "daemon-reload"],
                               capture_output=True, timeout=8)
                reloaded = True
            except Exception:  # noqa: BLE001
                reloaded = False
        return {"rolled_back_from": bid, "to": str(target),
                "guard_backup": guard_bp, "systemd_reloaded": reloaded}

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
