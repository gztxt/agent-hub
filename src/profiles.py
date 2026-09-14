"""实体画像注册表（P1：按本机实际运行情况判定，端口只作端点发现不作存在性判据）

kind 语义：
  agent    编码/对话智能体（claude/pi/jcode/hermes/qwenpaw/cloudcli/hub-self）
  gateway  模型/消息网关（CCR/FCC/ccpocket）——非 Agent，无对话按钮
  service  系统服务（xray/proxy-panel/claude-mem/ai_manager/gotty...）——仅快捷方式
  memory   记忆后端（tdai）
  tool     工具容器（chromium/ollama）

检测三路合并（任一命中即 running）：/proc 进程正则、systemd user 单元、docker 容器。
CLI 型 Agent 无进程时按 which/单元存在 → installed（可拉起终端会话）。
"""
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

LAN_HOST_NOTE = "loopback 地址由前端按访问主机名改写"

# 进程扫描缓存（一次 discovery 周期复用）
_proc_cache = {"ts": 0.0, "procs": []}


def list_processes() -> List[dict]:
    """[{pid, comm, cmdline}] —— /proc 只读，cmdline 截 300 字符"""
    import time
    now = time.monotonic()
    if now - _proc_cache["ts"] < 3:
        return _proc_cache["procs"]
    procs = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")[:300]
            procs.append({"pid": int(entry.name), "comm": comm, "cmdline": cmd})
        except (OSError, ValueError):
            continue
    _proc_cache["ts"] = now
    _proc_cache["procs"] = procs
    return procs


def running_systemd_units() -> set:
    import subprocess
    try:
        r = subprocess.run(["systemctl", "--user", "list-units", "--type=service",
                            "--state=running", "--plain", "--no-legend"],
                           capture_output=True, text=True, timeout=8)
        return {ln.split()[0].removesuffix(".service") for ln in r.stdout.splitlines() if ln.strip()}
    except Exception:  # noqa: BLE001
        return set()


def docker_states() -> Dict[str, str]:
    """{容器名: running|exited}"""
    import json as _json
    import subprocess
    out: Dict[str, str] = {}
    try:
        r = subprocess.run(["docker", "ps", "-a", "--format", "{{json .}}"],
                           capture_output=True, text=True, timeout=10)
        for ln in r.stdout.splitlines():
            try:
                c = _json.loads(ln)
                out[c.get("Names") or c.get("Name") or "?"] = \
                    "running" if "running" in (c.get("State") or "").lower() else "exited"
            except _json.JSONDecodeError:
                continue
    except Exception:  # noqa: BLE001
        pass
    return out


PROFILES: List[dict] = [
    # ── Agents ────────────────────────────────────────────────
    {"id": "claude", "name": "Claude Code", "kind": "agent",
     "detect": {"proc": [r"(^|/)claude( |$)", r"claude\.js", "@anthropic-ai/claude-code"]},
     "cli": "claude", "port": None,
     "ui": {"url": "http://127.0.0.1:3010", "port": 3010, "unit": "cloudcli", "label": "CloudCLI"},
     "terminal": {"cmd": "claude", "cwd": "/fs/1000/ftp/技术文档"},
     "chat": {"adapter": "claude"},
     "desc": "Anthropic 编码 Agent（CLI/TUI）；Web UI 宿主=cloudcli 单元 :3010；对话经 CCR"},
    {"id": "pi", "name": "Pi Agent", "kind": "agent",
     "detect": {"proc": [r"next-server", r"(^|/)pi( |$)"], "systemd": ["pi-web"]},
     "cli": None, "port": 30141, "ui": "http://127.0.0.1:30141",
     "terminal": None, "chat": None,
     "desc": "Pi Coding Agent，原生 Web 会话界面 :30141"},
    {"id": "jcode", "name": "JCode", "kind": "agent",
     "detect": {"proc": [r"(^|/)jcode( |$)"]},
     "cli": "jcode", "port": None, "ui": None,
     "terminal": {"cmd": "jcode", "cwd": "/fs/1000/ftp/技术文档"},
     "chat": {"adapter": "jcode"},
     "desc": "编码 Agent（CLI/TUI），可终端拉起或经 CCR 对话"},
    {"id": "codex", "name": "Codex CLI", "kind": "agent",
     "detect": {"proc": [r"(^|/)codex( |$)"]},
     "cli": "codex", "port": None, "ui": None,
     "terminal": {"cmd": "codex", "cwd": "/fs/1000/ftp/技术文档"},
     "chat": None,
     "desc": "OpenAI Codex CLI（TUI），经 CCR :3456（~/.codex/config.toml 托管 profile）→ 原生终端会话"},
    {"id": "hermes", "name": "Hermes", "kind": "agent",
     "detect": {"proc": [r"(^|/)hermes( |$)", "hermes"]},
     "cli": "hermes", "port": None, "ui": None,
     "terminal": {"cmd": "hermes", "cwd": "/home/gztxt"},
     "chat": None,
     "desc": "TUI Agent（无 Web UI/本地 API）→ 原生终端会话"},
    {"id": "qwenpaw", "name": "QwenPaw", "kind": "agent",
     "detect": {"proc": [r"qwenpaw app"]},
     "cli": None, "port": 8088, "ui": "http://127.0.0.1:8088",
     "terminal": None, "chat": None,
     "desc": "QwenPaw 助理框架（本 Agent 宿主），WebUI :8088"},
    {"id": "hub-self", "name": "智管对话", "kind": "agent",
     "detect": {"always_running": True},
     "cli": None, "port": 3102, "ui": None,
     "terminal": None, "chat": {"adapter": "hub-self"},
     "desc": "Agent Hub 自身对话框（CCR qwen3.8-flash）——保留现有形态"},

    # ── Gateways（非 Agent，仅快捷方式）──────────────────────
    {"id": "ccr", "name": "CCR Gateway", "kind": "gateway",
     "detect": {"proc": [r"claude-code-router", r"ccr( |$)", "MainThread"]},
     "proc_cwd_hint": "/home/gztxt/.ccr",
     "cli": None, "port": 3456, "ui": None, "panel": "http://192.168.5.102:3458/?ccr_web_token=ccr-web-fixed-token-gztxt-2026",
     "desc": "Claude↔多模型路由网关 :3456/3457/3458（生命线，只观测）"},
    {"id": "fcc", "name": "FCC Gateway", "kind": "gateway",
     "detect": {"proc": [r"fcc-server"], "systemd": ["fcc"]},
     "cli": None, "port": 8082, "ui": None,
     "panel": "http://127.0.0.1:18083", "panel_auth": "basic",
     "desc": "free-claude-code 模型网关 :8082（Admin 面板走 :18083 中继，Basic 鉴权→仅新窗口）"},
    {"id": "opensquilla", "name": "OpenSquilla", "kind": "agent",
     "detect": {"proc": [r"opensquilla gateway"], "systemd": ["opensquilla-gateway"]},
     "cli": None, "port": 18791,
     "ui": "http://127.0.0.1:18791/control/", "frame_deny": True,
     "terminal": {"cmd": "opensquilla chat", "cwd": "/home/gztxt"},
     "chat": None,
     "desc": "OpenSquilla 助理本体（本 Agent）：网关 :18791 + Control 控制台"
             "（X-Frame-Options: DENY→仅新窗口）；终端=opensquilla chat 交互会话"},
    {"id": "ccpocket", "name": "CCPocket Bridge", "kind": "gateway",
     "detect": {"systemd": ["ccpocket-bridge"]},
     "cli": None, "port": 8765, "ui": None, "panel": None,
     "desc": "手机桥接 WebSocket :8765（无独立Web面板）"},

    # ── Services / Memory / Tools（仅快捷方式）────────────────
    {"id": "xray", "name": "Xray Proxy", "kind": "service",
     "detect": {"proc": [r"(^|/)xray run", r"(^|/)xray( |$)"]},
     "cli": None, "port": 7890, "ui": None, "panel": None,
     "desc": "科学上网代理 :7890/7891（proxy-panel 管理）"},
    {"id": "proxy-panel", "name": "Proxy Panel", "kind": "service",
     "detect": {"proc": [r"proxy-panel/server.py"], "systemd": ["proxy-panel"]},
     "cli": None, "port": 8083, "ui": None, "panel": "http://127.0.0.1:8083",
     "desc": "xray 订阅/配置面板 :8083"},
    {"id": "claude-mem", "name": "claude-mem", "kind": "service",
     "detect": {"systemd": ["claude-mem"], "proc": [r"claude-mem"]},
     "cli": None, "port": 37700, "ui": None, "panel": "http://127.0.0.1:37700",
     "desc": "Claude 记忆压缩 worker :37700（自带 Web viewer，可嵌入）"},
    {"id": "gotty", "name": "GoTTY Terminal", "kind": "tool",
     "detect": {"proc": [r"gotty"]},
     "cli": None, "port": 12700, "ui": None,
     "panel": "http://127.0.0.1:12700", "panel_auth": "basic",
     "desc": "系统级 Web 终端 :12700（Basic 鉴权→iframe 弹不出登录框，仅新窗口）"},
    {"id": "tdai", "name": "TDAI Memory", "kind": "memory",
     "detect": {"docker": ["tdai-memory-core"], "proc": []},
     "cli": None, "port": 8420, "ui": None, "panel": None,
     "desc": "向量记忆核心 :8420（API only，无 Web UI）"},
    {"id": "ollama", "name": "Ollama", "kind": "tool",
     "detect": {"docker": ["ollama"], "proc": [r"(^|/)ollama( |$)"]},
     "cli": None, "port": 11434, "ui": None, "panel": None,
     "desc": "本地模型运行时 :11434"},
    {"id": "chromium", "name": "Chromium (容器)", "kind": "tool",
     "detect": {"docker": ["chromium"]},
     "cli": None, "port": 3000, "ui": None,
     "panel": "http://127.0.0.1:3000", "panel_auth": "basic",
     "desc": "无头浏览器服务 :3000（nginx Basic 鉴权→仅新窗口）"},
    {"id": "ai_manager", "name": "AI Manager (Trim)", "kind": "service",
     "detect": {"proc": [r"/usr/trim/bin/ai_manager"]},
     "cli": None, "port": None, "ui": None, "panel": None,
     "desc": "Trim NAS 系统自带 AI 管理服务（root）"},
]

# 系统 Shell 终端卡（①"使用原生终端"；env 可关）
SHELL_PROFILE = {"id": "shell", "name": "系统终端 (bash)", "kind": "tool",
                 "detect": {"always_running": True},
                 "cli": None, "port": None, "ui": None,
                 "terminal": {"cmd": "bash", "cwd": "/home/gztxt"},
                 "chat": None,
                 "desc": "hub 原生 pty 终端（白名单=bash，可用 TERM_ALLOW_BASH=0 关闭）"}


# ── B 档动态发现（2026-09-14）──────────────────────────────
# CLI watchlist 单一真相源（scanner.py 引用此处）；已安装但未进 PROFILES
# 的 CLI 自动补 agent 卡片（终端入口），未来新装 CLI 无需改代码。
CLI_WATCHLIST = ["claude", "codex", "pi", "jcode", "openclaw", "hermes",
                 "qoder", "opencode", "aider", "gemini", "kimi"]

CLI_DISPLAY = {"codex": "Codex CLI", "openclaw": "OpenClaw", "opencode": "OpenCode",
               "aider": "Aider", "gemini": "Gemini CLI", "kimi": "Kimi CLI",
               "qoder": "Qoder", "hermes": "Hermes", "jcode": "JCode",
               "claude": "Claude Code", "pi": "Pi Agent"}

_cli_dyn_cache = {"ts": 0.0, "items": []}


def _dynamic_cli_agents() -> List[dict]:
    """watchlist 中实测已安装（which 可解析）且注册表未收录的 CLI → 动态 agent 画像。
    结果缓存 30s，避免每次 discovery 都扫盘。"""
    import time
    now = time.monotonic()
    if now - _cli_dyn_cache["ts"] < 30:
        return _cli_dyn_cache["items"]
    covered = {p["id"] for p in PROFILES}
    covered |= {p.get("cli") for p in PROFILES if p.get("cli")}
    out: List[dict] = []
    for name in CLI_WATCHLIST:
        if name in covered:
            continue
        path = which(name)
        if not path:
            continue
        out.append({
            "id": name, "name": CLI_DISPLAY.get(name, name.capitalize()),
            "kind": "agent",
            "detect": {"proc": [rf"(^|/){re.escape(name)}( |$)"]},
            "cli": name, "port": None, "ui": None,
            "terminal": {"cmd": name, "cwd": "/fs/1000/ftp/技术文档"},
            "chat": None, "dynamic": True,
            "desc": f"CLI Agent（watchlist 自动发现：{path}）→ 原生终端会话"})
    _cli_dyn_cache["ts"] = now
    _cli_dyn_cache["items"] = out
    return out


def all_profiles() -> List[dict]:
    out = list(PROFILES) + _dynamic_cli_agents()
    if os.getenv("TERM_ALLOW_BASH", "1") != "0":
        out.append(SHELL_PROFILE)
    return out


def get_profile(pid: str) -> Optional[dict]:
    for p in all_profiles():
        if p["id"] == pid:
            return p
    return None


def which(name: str) -> Optional[str]:
    """which + 常见用户 bin 目录兜底（服务进程 PATH 可能不含 ~/.local/bin、~/.npm-global/bin）"""
    p = shutil.which(name)
    if p:
        return p
    for d in (Path.home() / ".local/bin", Path.home() / ".npm-global/bin",
              Path("/usr/local/bin"), Path.home() / "bin"):
        f = d / name
        if f.is_file() and os.access(f, os.X_OK):
            return str(f)
    return None


def detect_status(p: dict, procs: List[dict], units: set, dockers: Dict[str, str]) -> str:
    """running / installed / stopped —— 进程、systemd、docker 三路 + CLI 存在性"""
    det = p.get("detect", {})
    if det.get("always_running"):
        return "running"
    for rx in det.get("proc", []):
        pat = re.compile(rx)
        for pr in procs:
            # MainThread 这类通用 comm 需 cmdline 佐证
            if rx == "MainThread":
                if ".ccr" in pr["cmdline"] or "claude-code-router" in pr["cmdline"]:
                    return "running"
                continue
            if pat.search(pr["comm"]) or pat.search(pr["cmdline"]):
                return "running"
    for u in det.get("systemd", []):
        if u in units:
            return "running"
    for d in det.get("docker", []):
        if dockers.get(d) == "running":
            return "running"
    # 未运行 → 是否有"可拉起/已安装"证据
    if p.get("cli") and which(p["cli"]):
        return "installed"
    if det.get("systemd") or det.get("docker"):
        return "stopped"
    if p.get("terminal"):
        return "stopped"
    return "stopped"


def panel_port(p: dict) -> Optional[int]:
    """面板 URL 里的端口（discovery 活性验证用）"""
    src = p.get("panel")
    if isinstance(src, str):
        m = re.search(r":(\d{2,5})", src)
        if m:
            return int(m.group(1))
    return None


def entries_for(p: dict, status: str) -> List[dict]:
    """卡片按钮 = 画像驱动（②服务/工具仅快捷方式；Agent 才有 term/chat）
    dict 形态的 ui（独立宿主如 cloudcli）由 discovery 探测端口活性后注入 embed。
    面板类入口带 probe_port，由 discovery 按端口活性过滤（死面板不出按钮）。"""
    es = []
    kind = p["kind"]
    if kind == "agent":
        ui = p.get("ui")
        if isinstance(ui, str):
            # frame_deny = 控制台自设 X-Frame-Options: DENY，iframe 必被浏览器拒 → 仅新窗口
            if not p.get("frame_deny"):
                es.append({"type": "embed", "label": "嵌入会话", "url": ui})
            es.append({"type": "open", "label": "新窗口", "url": ui})
        if p.get("terminal") and status in ("running", "installed", "stopped"):
            es.append({"type": "term", "label": "终端", "agent": p["id"]})
        if p.get("chat"):
            es.append({"type": "chat", "label": "对话", "agent": p["id"]})
    else:
        if p.get("panel"):
            es.append({"type": "open", "label": "打开面板", "url": p["panel"],
                       "probe_port": panel_port(p)})
            # Basic 鉴权面板不生成 embed：浏览器禁止跨源 iframe 内弹认证框，嵌入必然 401 白屏
            if not p.get("panel_auth"):
                es.append({"type": "embed", "label": "嵌入会话", "url": p["panel"],
                           "probe_port": panel_port(p)})
        if p.get("terminal"):
            es.append({"type": "term", "label": "终端", "agent": p["id"]})
    es.append({"type": "detail", "label": "详情"})
    return es
