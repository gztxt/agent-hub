"""Manager Agent —— 自然语言指挥官（Agent_Manager 核心卖点的 Web 化复刻）

上游机制：LLM + 工具环（thought/toolcall/toolresult/answer 步骤流），
工具结果中嵌入 __action__ 标记由前端执行（打开 UI 等）。本实现保持一致。

上游 TUI 类 Agent 的「启动/停止进程」在 NAS 无头环境不适用
（服务由 systemd/其他守护管理），此处提供「打开界面/查询/对话/记忆」类工具。
"""
import hashlib
import json
import re
import os
import shlex
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

# ── v0.5 只读执行白名单 ────────────────────────────────────
READ_PATH_ROOTS = (
    str(Path(os.path.expanduser("~"))),         # /home/gztxt
    "/vol1",
    "/fs/1000/ftp/技术文档",
)
READ_SHELL_ALLOWED = frozenset({
    "ls", "cat", "head", "tail", "find", "grep", "stat", "du",
    "df", "free", "pwd", "whoami", "date", "uname", "id", "env",
    "readlink", "basename", "dirname", "wc", "file", "tree",
    "systemctl", "journalctl", "ps", "pgrep", "ss", "netstat",
    "ip", "ifconfig", "route", "traceroute", "ping", "nslookup",
    "which", "type", "echo", "true", "false", "test",
    "cd", "sort", "uniq", "tr", "cut", "awk", "sed", "xargs",  # 导航/文本流（只读模式）
})
# 字符级黑名单（任何位置出现即拒）—— 写/破坏/系统级操作
# 注意：'|' 管道操作符**不在**黑名单（v0.5 trace 反馈 LLM 多次用管道全被拒）
# v0.5.2.2 写重定向从黑名单移除（v0.5.1 trace 反馈 `head 2>/dev/null`、`du | sort | head` 等
# 合法只读命令被误拒）；改用下方 DENY_REDIRECT 正则精确匹配
READ_SHELL_DENY_SUBSTR = (
    # v0.5.2.3 移除字符级 cp/mv/ln/rm 拒（改用下方 WRITE_CMD_DENY 正则，命令起始才拒，
    # 防 grep -i cp / grep -i rm 等合法 grep 模式被误伤）
    "dd ", "mkfs", "fdisk", "parted",
    "chmod", "chown", "chgrp", "setfacl",
    "kill ", "kill\t", "kill$", "pkill", "killall",
    "shutdown", "reboot", "halt", "poweroff",
    "mount", "umount",
    # v0.5.2.9 移除字符级包管理/system 用户管理/apt 误伤（adapters/cyber/aptos 等含 apt 子串的合法目录名会被拒）
    # 改用下方 PKG_CMD_DENY / SYS_USER_CMD_DENY 正则，命令起始位置才拒
    "systemctl start", "systemctl stop", "systemctl restart",
    "systemctl reload", "systemctl enable", "systemctl disable",
    "systemctl mask", "systemctl unmask", "systemctl daemon-reload",
    "iptables", "ip route add", "ip route del", "ip rule",
    "passwd", "visudo", "sudo ",
    "crontab", "batch",  # v0.5.2.2 移除 "at "（误伤 cat/stat/data 等含 at 字符串的命令）；改用 AT_DENY_WORD 精确匹配 \bat\b
    ":(){:|:&};:", "wget ", "curl ", "nc ", "ncat ",
    "python ", "python3 ", "perl ", "ruby ", "node ",  # 拒绝脚本逃逸
    "/etc/passwd", "/etc/shadow", "/etc/sudoers", "/etc/fstab",
)
# v0.5.2.2 写重定向精确正则（取代原字符级拒）：
#   - 单 > 或 >> 写文件到 /path：拒（前不能是 2=stderr 绑流/&=复合/0=旧式 fd）
#   - &> / &>> 写文件：拒
#   - 2> / 2>>：放（stderr 绑流到 /dev/null 或文件，是只读 agent 的常见模式）
# 已知瑕疵：`2>>/path` 仍拒（stderr 追加写文件）；`0>/path` 放（agent 几乎不用）
DENY_REDIRECT = re.compile(r'(?:(?<![2&0])>(?!>)|&>|>>(?!>))[\s]*/[\w/]')
# v0.5.2.2 精确拒「at」命令（提交任务调度）—— 必须作为命令起始（行首/管道后/&&/; 后），
# 不在参数位置（grep at / cat foo 等）
AT_DENY_WORD = re.compile(r'(?:^|[|;&])\s*at(\s|$)')
# v0.5.2.3 写类命令精确拒（cp/mv/ln/rm/wget/curl/nc/tee/scp/rsync）—— 同上策略
WRITE_CMD_DENY = re.compile(
    r'(?:^|[|;&]|\bxargs\s+)\s*(cp|mv|ln|rm|wget|curl|nc|ncat|tee|scp|rsync)\b'
)
# v0.5.2.9 包管理命令精确拒（防 adapters/cyber/aptos 等合法目录名误伤）
PKG_CMD_DENY = re.compile(
    r'(?:^|[|;&]|\bxargs\s+)\s*(apt(-get|-cache|-key)?|yum|dnf|pacman|zypper|brew|snap|flatpak)\b'
)
# v0.5.2.9 系统用户管理命令精确拒
SYS_USER_CMD_DENY = re.compile(
    r'(?:^|[|;&]|\bxargs\s+)\s*(useradd|userdel|usermod|groupadd|groupdel|groupmod)\b'
)
# sed 单独限制：只允许 -n/=/s/ 读模式；拒绝 -i/i/a/c（写模式）
SED_DENY_FLAGS = ("-i", "-e ", "--in-place", " --follow-symlinks", "/d", "/a\\", "/i\\", "/c\\")


def _resolve_under(path: str) -> Optional[Path]:
    """把 path 解析为真实路径，校验在 READ_PATH_ROOTS 内。越界/不存在返回 None。"""
    if not path:
        return None
    try:
        p = Path(path).expanduser().resolve(strict=False)
    except Exception:
        return None
    ps = str(p)
    for root in READ_PATH_ROOTS:
        root_p = str(Path(root).resolve(strict=False))
        if ps == root_p or ps.startswith(root_p + "/"):
            return p
    return None


def _read_readme_summary(d: Path) -> str:
    """v0.5.2.4 读 README 第一段作为目录描述（限 150 字）。

    候选顺序：README.md / README / readme.md / readme / 项目同名.md
    失败/无 README → 返空串
    """
    candidates = ["README.md", "README", "readme.md", "readme", f"{d.name}.md"]
    text = ""
    for name in candidates:
        f = d / name
        if f.is_file() and f.stat().st_size < 256_000:  # 256K 上限
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                if text:
                    break
            except Exception:
                continue
    if not text:
        return ""
    # 提取第一段：跳过标题/空行/HTML/comment
    import re
    lines = text.splitlines()
    para_lines = []
    for ln in lines:
        s = ln.strip()
        if not s:
            if para_lines: break  # 段结束
            continue
        if s.startswith("#") or s.startswith("<") or s.startswith("<!--") or s.startswith("/*"):
            continue
        # 去 markdown 加粗/链接
        s = re.sub(r'\*\*', '', s)
        s = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', s)
        para_lines.append(s)
    summary = " ".join(para_lines).strip()
    if not summary:
        return ""
    if len(summary) > 150:
        summary = summary[:147] + "..."
    return summary


def _shell_allowed(command: str) -> Tuple[bool, str]:
    """校验 command 是否在白名单内。返回 (ok, reason)。

    策略：
    1. 字符级黑名单（> >> 2> 2>>、rm/dd/mkfs、python/node、/etc/passwd 等）
    2. 第一段命令必须在白名单（cd/sort/uniq/tr/cut/awk/sed/xargs 也允许）
    3. 管道 | 允许（v0.5 反馈：列大目录常用 ls | grep / | head）
    4. sed 单独限制 -i（写模式）；awk 默认只读
    5. xargs 限制只接 echo/cat 之类只读命令
    """
    if not command or not command.strip():
        return False, "空命令"
    # 先扫黑名单
    for deny in READ_SHELL_DENY_SUBSTR:
        if deny in command:
            return False, f"命令含禁用子串「{deny.strip()}」"
    # v0.5.2.2 精确拒 at 命令（提交任务调度）
    if AT_DENY_WORD.search(command):
        return False, "「at」命令禁（提交任务调度）"
    # v0.5.2.3 写类命令精确拒（避免误伤 grep -i cp / grep -i rm 等）
    m_w = WRITE_CMD_DENY.search(command)
    if m_w:
        return False, f"「{m_w.group(1)}」命令禁（破坏/网络下载）"
    # v0.5.2.9 包管理命令精确拒
    m_p = PKG_CMD_DENY.search(command)
    if m_p:
        return False, f"「{m_p.group(1)}」包管理禁"
    # v0.5.2.9 系统用户管理精确拒
    m_su = SYS_USER_CMD_DENY.search(command)
    if m_su:
        return False, f"「{m_su.group(1)}」系统用户管理禁"
    # v0.5.2.2 写重定向精确拒（替代 v0.5.1 字符级误伤）
    m = DENY_REDIRECT.search(command)
    if m:
        return False, f"写重定向禁（{m.group(0).strip()}）"
    # 第一段必须是白名单命令
    head = command.strip().split()[0]
    # 去路径前缀
    head_base = head.rsplit("/", 1)[-1]
    if head_base not in READ_SHELL_ALLOWED:
        return False, f"命令「{head_base}」不在白名单（可执行：{sorted(READ_SHELL_ALLOWED)}）"
    # sed 写模式额外拒
    if head_base == "sed":
        for f in SED_DENY_FLAGS:
            if f in command:
                return False, f"sed 写模式「{f.strip()}」禁"
    # xargs 接非只读命令拒（防 xargs rm 这类逃逸）
    if head_base == "xargs":
        # 抓 xargs 后第一个非选项 token 作目标命令
        parts = command.split()
        for i, p in enumerate(parts):
            if p == "xargs":
                tail = parts[i+1:]
                # 跳过 -n -I -L -P 等选项
                j = 0
                while j < len(tail) and tail[j].startswith("-"):
                    j += 1
                if j >= len(tail):
                    return False, "xargs 后缺命令"
                tgt = tail[j].rsplit("/", 1)[-1]
                if tgt not in READ_SHELL_ALLOWED:
                    return False, f"xargs 目标「{tgt}」不在白名单（防 rm 逃逸）"
                break
    return True, "ok"


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
            # v0.5.2.4 bytes + decode 兜底
            r = subprocess.run(args, capture_output=True, timeout=4)
            out = r.stdout.decode("utf-8", errors="replace") if r.stdout else (r.stderr.decode("utf-8", errors="replace") if r.stderr else "")
            return out.strip()
        except Exception as exc:  # noqa: BLE001
            return f"err:{type(exc).__name__}"
    return {
        "unit": unit,
        "active": _run(["systemctl", "--user", "is-active", unit]),
        "enabled": _run(["systemctl", "--user", "is-enabled", unit]),
    }


# ── v0.5.3 Pydantic / MCP 错误结构化解析 ─────────────────
# 背景：截图（智管调 mcp_call，工具 schema 不匹配）LLM 只看到截 300 字符的 raw Pydantic
# 错误"Error executing tool echo: 1 validation error for echo..."，无法定位字段名/期望类型/
# 实际值，只能瞎猜 server 名（18 次失败）。这里把 Pydantic 2.x 错误回填成结构化 dict，
# 让 LLM 一轮自纠。失败兜底返 None（mcp_call 错误分支继续走原 raw 路径）。
_VALIDATION_HINT_TYPE = re.compile(
    r'\[type=([\w_]+)(?:,\s*input_value=([^,]+))?,\s*input_type=([\w]+)\]?'
)
# 匹配 Pydantic 2.x 错误头部，如 "1 validation error for Echo"
_VALIDATION_HEADER = re.compile(r'(\d+)\s+validation\s+errors?\s+for\s+(\S+)', re.IGNORECASE)
# 单条错误：捕获行首的字段路径（缩进的 2/4 空格标识层级）+ 类型 hint
_VALIDATION_FIELD_LINE = re.compile(r'^\s{2,}(\S+)\s*$')
# 自定义 mcp_call 错误（mcpgw 抛的非 Pydantic 错误）
_MCP_NOT_FOUND = re.compile(r'(?:MCP\s*server\s*未注册|server\s*not\s*found|unknown\s*tool)\s*[:：]?\s*(\S*)', re.IGNORECASE)
_MCP_TIMEOUT = re.compile(r'(?:timeout|timed?\s*out|超时)', re.IGNORECASE)
_MCP_RATE_LIMIT = re.compile(r'(?:限流|rate\s*limit|429)', re.IGNORECASE)
_MCP_ACL = re.compile(r'(?:ACL\s*拒绝|forbidden|403)', re.IGNORECASE)


def _parse_validation_error(error_text: str) -> Optional[Dict[str, Any]]:
    """把 Pydantic 2.x 错误 / MCP 自定义错误解析成结构化 dict；解析不到返 None。

    返回 dict 字段：
      error_type: "validation" | "not_found" | "timeout" | "rate_limit" | "acl" | "unknown"
      tool:        Pydantic 报错的 model 名（"Echo"）；解析不到则 "unknown"
      field:       字段路径（"message" / "items.0.name"）；解析不到则 "unknown"
      expected:    期望类型/约束（"required" / "integer" / "string" …）；解析不到则 "unknown"
      got:         实际值字符串（截 80 字符）；拿不到则 None
      raw:         原 300 字符文本（兜底回显）
    """
    if not error_text:
        return None
    text = str(error_text)

    # ── 1. 自定义 mcp_call 错误优先（mcpgw 抛的 404/429/403 等）──
    m_nf = _MCP_NOT_FOUND.search(text)
    if m_nf:
        return {
            "error_type": "not_found",
            "tool": m_nf.group(1) or "unknown",
            "field": "unknown",
            "expected": "registered",
            "got": None,
            "raw": text[:300],
        }
    if _MCP_RATE_LIMIT.search(text):
        return {
            "error_type": "rate_limit",
            "tool": "unknown", "field": "unknown",
            "expected": "under_rate_limit", "got": None,
            "raw": text[:300],
        }
    if _MCP_ACL.search(text):
        return {
            "error_type": "acl",
            "tool": "unknown", "field": "unknown",
            "expected": "acl_allowed", "got": None,
            "raw": text[:300],
        }
    if _MCP_TIMEOUT.search(text) and "validation" not in text.lower():
        return {
            "error_type": "timeout",
            "tool": "unknown", "field": "unknown",
            "expected": "fast_enough", "got": None,
            "raw": text[:300],
        }

    # ── 2. Pydantic 2.x validation error ──
    if "validation error" not in text.lower():
        return None
    header = _VALIDATION_HEADER.search(text)
    if not header:
        # 可能是精简版（"Error executing tool X: ..."）—— 仍标 validation 但字段 unknown
        return {
            "error_type": "validation",
            "tool": "unknown", "field": "unknown",
            "expected": "unknown", "got": None,
            "raw": text[:300],
        }

    tool_name = header.group(2) or "unknown"
    field = "unknown"
    expected = "unknown"
    got: Optional[str] = None

    lines = text.splitlines()
    # 优先用语义化提示（"Field required" / "Input should be a valid integer"）——
    # spec 要求 expected 字段反映「人类可读约束」而非 Pydantic 内部代码 (missing/int_parsing)。
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Pydantic 2.x 错误：消息行通常缩进更深，含 "Field required" 或 "Input should be"
        # 字段名通常是上一行（缩进较浅）
        if "Field required" in stripped:
            expected = "required"
            if i > 0:
                field = lines[i - 1].strip()
            break
        if "Input should be" in stripped:
            # "Input should be a valid integer [type=int_parsing, ...]" → 取 [ 之前部分
            after = stripped.split("Input should be", 1)[1].strip()
            expected = after.split("[", 1)[0].strip().strip(",").strip().strip(".").strip()
            if i > 0:
                field = lines[i - 1].strip()
            break
        if "missing" in stripped and "type=missing" in stripped and expected == "unknown":
            # 兜底：仅 "[type=missing]" 无 "Field required" 字样的精简格式
            expected = "required"
            if i > 0:
                field = lines[i - 1].strip()
            break
    # got 从 [type=X, input_value=Y, input_type=Z] 抓 Y（spec 只用 Y，不用 Z）
    # 注：expected==required 时字段没传，input_value 是整个 args dict（如 {}）—— 此时 got 不
    # 反映字段值，会误导 LLM；按 spec 设为 None
    if expected != "required":
        for line in lines:
            m_type = _VALIDATION_HINT_TYPE.search(line)
            if m_type and m_type.group(2):
                got = (m_type.group(2) or "").strip().strip("'\"")[:80] or None
                break

    return {
        "error_type": "validation",
        "tool": tool_name,
        "field": field or "unknown",
        "expected": expected or "unknown",
        "got": got,
        "raw": text[:300],
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
- 最终用简洁中文汇报。

Tool priority（必须遵守，按顺序优先用）：
1) 本地 5 个只读执行工具（list_dir / read_file / shell_run / find_files / grep_search）是默认首选——日常『看文件/搜内容/列目录』95% 场景直接用这 5 个，**不要绕 mcp_call**；
2) search_memory / get_memory_layers 查过往记忆与画像；
3) chat_with_agent 联系其他 Agent（**不要用此问 hub-self 自己，会形成循环**）；
4) mcp_call 是最后手段——只在 hub-self 自己的 5 工具 + 其他 16 工具确实解决不了时才考虑；**永远不要**在 mcp_tools 没列出的 server 上调 mcp_call，否则会一直 Pydantic validation error 浪费 token。
"""

TOOLS = [
    {"type": "function", "function": {
        "name": "list_agents", "description": "列出所有已知 Agent 及实时状态（id / 类型 / running/stopped / 端口 / 端点）。回答“现在有哪些 Agent/谁在跑/那个端口是哪个”时第一个调。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_agent", "description": "查询单个 Agent 的完整画像（profile / 监听 / 配置路径 / 端点）。当 list_agents 列表里看到 id 但需要详情（端口、URL、tags）时调；如果 agent_id 不存在会直接报错，不要反复猜 id。",
        "parameters": {"type": "object", "properties": {
            "agent_id": {"type": "string"}}, "required": ["agent_id"]}}},
    {"type": "function", "function": {
        "name": "chat_with_agent", "description": "向指定 Agent 发送消息并获取回复。**典型场景**：用户想把任务转交给特定 Agent（如让 pi 算东西、让 jcode 改代码）时用。**不要用此工具问 hub-self 自己——会形成死循环**，hub-self 自问自答请直接推理或用本地 5 工具。",
        "parameters": {"type": "object", "properties": {
            "agent_id": {"type": "string"},
            "message": {"type": "string"}}, "required": ["agent_id", "message"]}}},
    {"type": "function", "function": {
        "name": "open_agent_ui", "description": "获取指定 Agent 的 Web 界面 URL（不是打开浏览器，而是在 hub 前端展示打开按钮）。**典型场景**：用户说“打开 claude/jcode/pi 界面”或“我想看 X 的页面”。返回 action 字段，前端会处理跳转；agent_id 必须先 list_agents 拿准确值。",
        "parameters": {"type": "object", "properties": {
            "agent_id": {"type": "string"}}, "required": ["agent_id"]}}},
    {"type": "function", "function": {
        "name": "search_memory", "description": "按 query 检索记忆中心 L1（用户偏好、决策、约束等结构化条目）。**典型场景**：用户问“之前怎么定的/我记得说过……”或当前任务依赖历史偏好时。query 用自然语言短语而非关键词堆砌，limit 决定返回几条；返回空列表时不要反复重试同 query。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_memory_layers", "description": "查看 L2 当前工作记忆（本次任务上下文）与 L3 用户/系统 Profile 概览（无需 query 的全局快照）。**典型场景**：刚接进对话想“校准”自己——已知用户身份/系统约束时用；不记得某条事实具体内容但知道它在哪一层时用。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "add_memory", "description": "把用户表达的稳定偏好/决策写入 L1。**典型场景**：用户说“以后都这样”、“我偏好 X”、“记住我……”。**只存稳定信息**，不要存单次任务细节（“今天改了 jcode 配置”不要存）；category 取值 fact/decision/constraint/preference，不确定就 fact；source 默认空字符串。",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string"},
            "category": {"type": "string", "enum": ["fact", "decision", "constraint", "preference"]},
            "source": {"type": "string"}}, "required": ["content"]}}},
    {"type": "function", "function": {
        "name": "list_ports", "description": "列出本机监听端口及归属进程（端口/PID/cmdline）。**典型场景**：用户问“3102/3456/8083 是谁”、诊断端口冲突、或需要判断某服务是否真在跑。可选参数 port 用于精确过滤（不要用模糊词如'3 开头'，传整数即可）。",
        "parameters": {"type": "object", "properties": {
            "port": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "list_sessions", "description": "查询统一对话的会话历史摘要（agent_id / 时间 / 首条消息）。**典型场景**：用户想“找回上次跟 X 的对话”、回顾近期和某 Agent 聊过什么。不传 agent_id 拿全部；limit 控制条数（默认即可）。",
        "parameters": {"type": "object", "properties": {
            "agent_id": {"type": "string"}, "limit": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "mcp_tools", "description": "列出 MCP 聚合网关中**当前真实可用**的所有 server 与 tool（每次返回即快照，无缓存）。**典型场景**：在调 mcp_call **之前必须**先调一次——拿到 server 准确名 + 该 server 暴露的 tool 名 + 参数 schema。返回里没有的 server/tool **绝对不要**猜，否则 mcp_call 会一直 Pydantic validation error 浪费 token。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "mcp_call",
        "description": "调用 MCP 聚合网关中的某个 server+tool。**【强制前置】永远先调 mcp_tools 看 server 列表；列表里没出现的 server 名绝对不要瞎试，否则会一直 Pydantic validation error 浪费 token**。**日常任务请优先用 hub-self 的本地 5 工具**（list_dir / read_file / shell_run / find_files / grep_search）——它们能解决 95% 任务，mcp_call 是最后手段。【失败自检】看到 'validation error for X'，说明参数 X 的类型/必填/嵌套错了，先 mcp_tools 看 X 的 schema 再调整 args；看到 'server not found'，检查大小写/拼写/当前网关是否注册了该 server（可能已下线）。",
        "parameters": {"type": "object", "properties": {
            "server": {"type": "string", "description": "server id 或名称（如 'hub-demo'）"},
            "tool": {"type": "string"},
            "args": {"type": "object"}}, "required": ["server", "tool"]}}},
    # ── 修复能力工具（v0.4 起，对话框默认不暴露；UI「修复模式」开启才可用）──
    {"type": "function", "function": {
        "name": "agent_health", "description": "单个 Agent/服务的一页画像：profile + 监听 + 配置 mtime + journal 尾 10 行。**仅只读**，不动任何东西也不重启。**典型场景**：用户问“X 现在怎么样/挂了没/日志里说啥”、或在做修复前先取证判断能否下手。agent_id 用 list_agents 返回的精确值。",
        "parameters": {"type": "object", "properties": {
            "agent_id": {"type": "string"}}, "required": ["agent_id"]}}},
    {"type": "function", "function": {
        "name": "safe_restart", "description": "重启白名单内的 systemd user 单元（agent-hub / ccr / proxy-panel / pi-web / cloudcli / ccpocket-bridge / fcc-refresh-free / agent-hub-self）。**【重要警告】重启 agent-hub 会立即断掉当前 hub-self 对话**，执行前必须先向用户说清影响 + 等用户显式 yes；reason 必填并写入审计。**典型场景**：服务确认挂掉且 agent_health 证据齐备后用；只是怀疑或只是看日志不要调——先 list_ports / agent_health 取证。",
        "parameters": {"type": "object", "properties": {
            "unit": {"type": "string"},
            "reason": {"type": "string", "description": "为什么重启（写入备份名+审计）"}},
            "required": ["unit", "reason"]}}},
    {"type": "function", "function": {
        "name": "config_show", "description": "读取并时间戳备份指定配置文件（白名单内：jcode / claude / hermes 配置 + ~/.config/systemd/user/*.service），返回 backup 路径与 sha256 前 8 位。**仅只读，绝不写回**。**典型场景**：任何写配置前必先调一次取证 + 留回滚余地——rollback 需要 backup_id，所以这是 config_write / rollback 链路的第一环。path 用绝对路径，reason 必填。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "绝对路径，限于 jcode/claude/hermes 配置 + ~/.config/systemd/user/*.service"},
            "reason": {"type": "string"}}, "required": ["path", "reason"]}}},
    {"type": "function", "function": {
        "name": "config_write", "description": "**【双次确认】**写白名单内配置。**第一次调用**：pending_write_id 留空，仅做“试写+比对+生成 pending id”，不落盘；**第二次调用**：pending_write_id 填上次返回的 id + new_content 再次给完整文件，才真覆盖。任何一步可 rollback。**典型场景**：用户明确说“改 X”且已经先 config_show 取证；不在用户授权链上的不要碰。reason 必填，写入备份名+审计。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "new_content": {"type": "string", "description": "完整文件新内容（覆盖式）"},
            "reason": {"type": "string"},
            "pending_write_id": {"type": "string", "description": "首次调用留空；二次确认时填上次返回的 id"}},
            "required": ["path", "new_content", "reason"]}}},
    {"type": "function", "function": {
        "name": "rollback", "description": "用 backup_id（config_show 或 config_write 返回的文件名，不含目录）把指定文件还原到 data/backups/ 下对应备份。**仅还原白名单内文件**。**典型场景**：config_write 写完发现不对、或 service 重启后行为异常需要回到上一版；**调 rollback 前最好先 config_show 当前值对比**，避免直接覆盖丢失改动现场。",
        "parameters": {"type": "object", "properties": {
            "backup_id": {"type": "string", "description": "文件名（不含目录），如 20260907_182856-config.toml-manual-fix-aabbccdd.bak"}},
            "required": ["backup_id"]}}},
    # ── v0.5 只读执行类（默认暴露，路径/命令双重白名单）──
    {"type": "function", "function": {
        "name": "list_dir", "description": "列出白名单路径下目录内容（子目录与文件大小）。**默认首选**：用户问“目录里有什么/项目结构/列文件”时直接用这个，不要绕 mcp_call。**失败自检**：path 不在白名单（/home/gztxt|/vol1|/fs/1000/ftp/技术文档）会拒——用户给的路径要先确认在这三个根下；depth 最大 3。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "绝对路径，限于 /home/gztxt、/vol1、/fs/1000/ftp/技术文档"},
            "depth": {"type": "integer", "description": "递归深度 0=不递归 默认1最大3", "default": 1, "minimum": 0, "maximum": 3},
            "hidden": {"type": "boolean", "description": "是否包含隐藏文件", "default": False}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "读取白名单路径下文件的前 N 字节（默认 4KB，上限 16KB）。**默认首选**：用户要看具体文件内容/某段代码/某段配置时用——比 shell_run + cat 更安全（有大小截断）。**失败自检**：path 必须在白名单内；max_bytes 不超过 16384；要看更大文件用 shell_run + head -c。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "max_bytes": {"type": "integer", "default": 4096, "minimum": 1, "maximum": 16384}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "shell_run", "description": "执行白名单内只读 shell 命令（ls/cd/cat/head/tail/find/grep/stat/du/sort/uniq/cut/awk/systemctl status/journalctl -n 等；管道 | 允许；sed 仅只读；xargs 仅接白名单命令）。**默认首选**：list_dir/read_file 表达不了时（如要 grep / sort / journalctl / 多步管道）才升级到这个。**【强制】字符级拒绝 rm/dd/mkfs/python/> 等写入类与系统关键文件**，不要尝试绕过；cwd 必须也在白名单内；timeout 上限 20 秒。",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "完整 shell 命令字符串"},
            "cwd": {"type": "string", "description": "工作目录（白名单内），可选；如不用本参数也可用 cd <path>"},
            "timeout": {"type": "integer", "description": "秒", "default": 5, "minimum": 1, "maximum": 20}},
            "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "find_files", "description": "在白名单路径下按 glob 模式找文件（如 **/*.md 或 *.toml）。**默认首选**：用户问“找某个文件/找所有 X 后缀”时用，比 shell_run + find 更结构化（自动 max_results 截断）。**失败自检**：root 必须在白名单内；max_results 上限 200；glob 写法是 shell glob 不是 regex（如 *.md 不需 .*\\.md）。",
        "parameters": {"type": "object", "properties": {
            "glob_pattern": {"type": "string", "description": "如 **/*.md 或 *.toml"},
            "root": {"type": "string", "description": "根目录"},
            "max_results": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200}},
            "required": ["glob_pattern", "root"]}}},
    {"type": "function", "function": {
        "name": "grep_search", "description": "在白名单路径下按模式搜内容（仅 grep -rEn，自动加行号与文件名）。**默认首选**：用户问“哪段代码提到 X / 哪个文件有 Y / 全文搜关键字”时用，比 shell_run + grep -r 更结构化（结果含 file:line:content）。**失败自检**：path 必须在白名单内；pattern 是 regex（特殊字符要转义）；ignore_case=true 处理大小写不敏感；max_results 上限 100。",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "max_results": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            "ignore_case": {"type": "boolean", "default": False}},
            "required": ["pattern", "path"]}}},
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
        target = args["agent_id"]
        message = args.get("message", "")
        require_alive = bool(args.get("require_alive"))
        if require_alive:
            # 先健康检查：profile+端口+有 adapter
            discovery = _ctx.get("discovery")
            agent = discovery.get_agent(target) if discovery else None
            if not agent:
                return {"error": f"agent {target} 不存在", "reason": "not_registered",
                        "hint": "调 list_agents 查可用 agent"}
            if agent.status != "running":
                return {"error": f"{target} 当前状态 {agent.status}",
                        "reason": "not_alive",
                        "hint": "v0.5 起智管有 10 个本地工具（list_dir/read_file/shell_run 等）可直接修复，"
                                "不必依赖其他 Agent"}
        try:
            r = await chat_fn(target, message)
            # 归一化错误：上游返回 dict 含 error 时，补 reason 字段
            if isinstance(r, dict) and r.get("error") and "reason" not in r:
                err = str(r["error"])
                reason = "unknown"
                if "404" in err or "not found" in err.lower():
                    reason = "not_registered"
                elif "timeout" in err.lower() or "Connection" in err:
                    reason = "timeout_or_unreachable"
                elif "down" in err.lower() or "503" in err or "not running" in err.lower():
                    reason = "not_alive"
                elif "tool" in err.lower() or "401" in err or "403" in err:
                    reason = "auth_or_tool"
                r["reason"] = reason
            return r
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:300], "reason": "exception"}
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
        # v0.5.2.9 先列已注册 server，错误时告诉 LLM 实际名单（避免它反复猜）
        try:
            return await mcpgw.mcp_call(mcpgw.CallIn(
                server=args["server"], tool=args["tool"],
                args=args.get("args") or {}, agent_id="manager"))
        except Exception as e:  # noqa: BLE001
            detail = getattr(e, "detail", None)
            # 直接查 mcp_servers 表，列出实际已注册 server 名字
            try:
                import db as _db
                # 确保 db 已初始化（mcp_call 可能从非 manager 入口进来）
                try:
                    _db.init_db(config.Config().db_path)
                except Exception:
                    pass
                regs = _db.query("SELECT id, name FROM mcp_servers ORDER BY created_at")
            except Exception:
                regs = []
            names = [r.get("name") or r.get("id") for r in regs]
            # ── v0.5.3 解析 Pydantic / MCP 错误为结构化字段，让 LLM 一轮自纠 ──
            raw_text = str(detail or e)
            # mcpgw 把 out 序列化成 JSON 字符串塞进 HTTPException.detail；尝试解一层
            parsed_payload: Any = None
            try:
                parsed_payload = json.loads(raw_text)
            except Exception:
                pass
            err_text = raw_text
            if isinstance(parsed_payload, dict) and parsed_payload.get("error"):
                err_text = str(parsed_payload["error"])
            parsed = _parse_validation_error(err_text)
            if parsed:
                et = parsed["error_type"]
                # 把结构化字段拼成"人话"给 LLM（dict 被 llm.chat_tools_loop json.dumps，
                # LLM 在下一轮既能看到字段也能看到自然语言描述）
                if et == "validation":
                    got_str = f"，实际传入 {parsed['got']}" if parsed.get("got") not in (None, "", "null") else "，但没传该字段"
                    human = (
                        f"参数错误: tool '{parsed['tool']}' 的 '{parsed['field']}' 字段"
                        f"应为 {parsed['expected']}{got_str}。"
                        f"请先调 mcp_tools 查 {parsed['tool']} 的 inputSchema，按 schema 补齐 args 后再调 mcp_call。"
                    )
                elif et == "not_found":
                    human = (
                        f"找不到 {parsed['tool']}。当前已注册 MCP server: "
                        f"{names if names else '（空）'}。"
                        f"请调 mcp_tools 查准确 server 名/工具名再调 mcp_call。"
                    )
                elif et == "rate_limit":
                    human = "触发限流，请稍等 60s 再试。"
                elif et == "acl":
                    human = "ACL 拒绝该调用，hub 聚合网关策略禁止此 agent 调该工具。"
                elif et == "timeout":
                    human = "上游 MCP server 调用超时（默认 60s），可重试或换工具。"
                else:
                    human = f"mcp_call 错误: {err_text[:200]}"
                return {
                    "error": human,
                    "error_type": et,
                    "field": parsed["field"],
                    "expected": parsed["expected"],
                    "got": parsed["got"],
                    "tool": parsed["tool"],
                    "raw": parsed["raw"],
                    "registered_servers": names,
                    "hint": (
                        f"当前已注册 MCP server: {names if names else '（空）'}. "
                        f"如非必要请改用 list_dir/read_file/shell_run/find_files/grep_search 等 hub-self 本地工具."
                    ),
                }
            # 非 validation / 解析失败：原 raw 路径（保证 fallback 兼容）
            hint = (
                f"当前已注册 MCP server: {names if names else '（空）'}. "
                f"请改用 list_dir/read_file/shell_run/find_files/grep_search 等 hub-self 21 工具."
            )
            return {"error": str(detail or e)[:300], "registered_servers": names, "hint": hint}

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
                # v0.5.2.4 bytes + decode 兜底
                r = subprocess.run(
                    ["journalctl", "--user", "-u", unit, "-n", "10", "--no-pager", "-q"],
                    capture_output=True, timeout=4)
                out = r.stdout.decode("utf-8", errors="replace") if r.stdout else ""
                journal_tail = out.splitlines()[-10:]
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

    # ── v0.5 只读执行 ──────────────────────────────────────
    if name == "list_dir":
        p = _resolve_under(args.get("path") or "")
        if not p:
            return {"error": f"路径越界或不存在: {args.get('path')}",
                    "allowed_roots": list(READ_PATH_ROOTS)}
        depth = max(0, min(int(args.get("depth") or 1), 3))
        hidden = bool(args.get("hidden"))
        # v0.5.2.4: 探测深度（depth=0 时不读 README；只 1 层且只对顶层加 desc）
        with_desc = depth <= 1
        try:
            items = []
            for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if not hidden and child.name.startswith("."):
                    continue
                try:
                    st = child.stat()
                    item = {
                        "name": child.name,
                        "type": "dir" if child.is_dir() else "file",
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                    }
                    # v0.5.2.4 自动读 README 第一段作为 description（仅 depth<=1 的目录）
                    if with_desc and child.is_dir():
                        item["description"] = _read_readme_summary(child)
                    items.append(item)
                    if depth > 0 and child.is_dir():
                        # 递归一层
                        for sub in sorted(child.iterdir(), key=lambda x: x.name.lower()):
                            if not hidden and sub.name.startswith("."):
                                continue
                            try:
                                sst = sub.stat()
                                items.append({
                                    "name": f"{child.name}/{sub.name}",
                                    "type": "dir" if sub.is_dir() else "file",
                                    "size": sst.st_size,
                                    "mtime": sst.st_mtime,
                                    "depth": 1,
                                })
                            except Exception:
                                pass
                except Exception:
                    pass
            return {"path": str(p), "depth": depth, "count": len(items), "items": items[:200]}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"列目录失败: {exc}"}

    if name == "read_file":
        p = _resolve_under(args.get("path") or "")
        if not p:
            return {"error": f"路径越界或不存在: {args.get('path')}",
                    "allowed_roots": list(READ_PATH_ROOTS)}
        if p.is_dir():
            return {"error": f"{p} 是目录，请用 list_dir"}
        max_bytes = max(1, min(int(args.get("max_bytes") or 4096), 16384))
        try:
            data = p.read_bytes()[:max_bytes]
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                return {"path": str(p), "size": p.stat().st_size,
                        "truncated_to": len(data),
                        "note": "二进制文件，前 N 字节 hex 预览",
                        "hex": data.hex()[:512]}
            return {"path": str(p), "size": p.stat().st_size,
                    "truncated": p.stat().st_size > max_bytes,
                    "truncated_to": len(data),
                    "content": text}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"读文件失败: {exc}"}

    if name == "shell_run":
        cmd = args.get("command") or ""
        ok, reason = _shell_allowed(cmd)
        if not ok:
            return {"error": f"shell 拒: {reason}",
                    "allowed_commands": sorted(READ_SHELL_ALLOWED),
                    "deny_substrings": list(READ_SHELL_DENY_SUBSTR)[:10]}
        timeout = max(1, min(int(args.get("timeout") or 5), 20))
        # v0.5.1 cwd 参数：显式工作目录（白名单内），省去 `cd X && cmd` 的拼接
        cwd = args.get("cwd")
        if cwd:
            cwdr = _resolve_under(str(cwd))
            if not cwdr:
                return {"error": f"cwd 越界: {cwd}",
                        "allowed_roots": list(READ_PATH_ROOTS)}
            cwd = str(cwdr)
        try:
            # v0.5.2.4 改用 bytes + errors=replace 兜底（部分子进程用 GBK 输出，
            # LC_ALL=C.UTF-8 不一定管用；用 bytes 后用 utf-8 解 + 错误替换）
            r = subprocess.run(
                cmd, shell=True, capture_output=True, timeout=timeout,
                cwd=cwd,
                env={**os.environ, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"})
            # 解码为文本，错误字符替换为 �
            try:
                out = r.stdout.decode("utf-8", errors="replace")
                err = r.stderr.decode("utf-8", errors="replace")
            except Exception:
                out = str(r.stdout)
                err = str(r.stderr)
            # v0.5.1 输出截断提升到 32KB（v0.5 8KB 太小，列大目录 ls -lh 被截）
            out_trunc = len(out) > 32768
            err_trunc = len(err) > 8192
            return {
                "command": cmd,
                "cwd": cwd or None,
                "exit_code": r.returncode,
                "stdout": out[:32768],
                "stdout_truncated": out_trunc,
                "stderr": err[:8192],
                "stderr_truncated": err_trunc,
                "timeout": timeout,
            }
        except subprocess.TimeoutExpired:
            return {"error": f"超时（{timeout}s）", "command": cmd, "cwd": cwd or None}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"执行失败: {exc}"}

    if name == "find_files":
        root = _resolve_under(args.get("root") or "")
        if not root:
            return {"error": f"root 越界: {args.get('root')}",
                    "allowed_roots": list(READ_PATH_ROOTS)}
        pattern = args.get("glob_pattern") or "*"
        max_results = max(1, min(int(args.get("max_results") or 50), 200))
        try:
            matches = []
            for m in root.glob(pattern):
                try:
                    matches.append({"path": str(m), "is_dir": m.is_dir(),
                                    "size": m.stat().st_size if m.is_file() else None})
                except Exception:
                    matches.append({"path": str(m), "error": "stat failed"})
                if len(matches) >= max_results:
                    break
            return {"root": str(root), "pattern": pattern,
                    "count": len(matches), "truncated": len(matches) >= max_results,
                    "matches": matches}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"find 失败: {exc}"}

    if name == "grep_search":
        p = _resolve_under(args.get("path") or "")
        if not p:
            return {"error": f"path 越界: {args.get('path')}",
                    "allowed_roots": list(READ_PATH_ROOTS)}
        pattern = args.get("pattern") or ""
        if not pattern:
            return {"error": "pattern 必填"}
        max_results = max(1, min(int(args.get("max_results") or 20), 100))
        ignore_case = "-i" if args.get("ignore_case") else ""
        cmd = f"grep -rEn {ignore_case}-- {shlex.quote(pattern)} {shlex.quote(str(p))}"
        # _shell_allowed 必过（grep 在白名单），但仍校验
        ok, reason = _shell_allowed(cmd)
        if not ok:
            return {"error": f"内部校验: {reason}"}
        try:
            # v0.5.2.4 改 bytes + decode 兜底
            r = subprocess.run(
                cmd, shell=True, capture_output=True, timeout=10)
            out = r.stdout.decode("utf-8", errors="replace") if r.stdout else ""
            lines = out.splitlines()[:max_results]
            return {"path": str(p), "pattern": pattern, "count": len(lines),
                    "truncated": len((r.stdout or "").splitlines()) > max_results,
                    "matches": lines}
        except subprocess.TimeoutExpired:
            return {"error": "grep 超时（10s）"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"grep 失败: {exc}"}

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

        # v0.5.3 软熔断：连续 3 次同错误指纹 → 提前 final（max_rounds 仅兜底，正常 3 轮内停）
        answer, steps = await llm.chat_tools_loop(messages, TOOLS, timed_dispatch,
                                                  max_rounds=10)
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
