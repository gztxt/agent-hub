"""实体画像注册表（P1：按本机实际运行情况判定，端口只作端点发现不作存在性判据）

kind 语义：
  agent    编码/对话智能体（claude/pi/jcode/hermes/qwenpaw/cloudcli）
  gateway  模型/消息网关（CCR/ccpocket）——非 Agent，无对话按钮
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

# ── 外框统一注入代理（用户 09-20 选定方案 b）──────────────────────
# qwenpaw 自带 56px 顶栏（实测 h=56、sider=calc(100vh-64px)），跨源 iframe 下 hub 注不进
# 任何样式，而官方也没有 hideHeader/custom_css 钩子。hub 在进程内起一个本地反代，
# HTML 出栈前插一段 <style>（见 src/embed_proxy.py）。默认开；想回直连只需在 .env
# 写 EMBED_UNIFY=0 重启 hub，不必改代码。
EMBED_UNIFY = os.getenv("EMBED_UNIFY", "1") == "1"
EMBED_PROXY_PORT = int(os.getenv("EMBED_PROXY_PORT", "3103"))
QWENPAW_UI = ("http://127.0.0.1:%d" % EMBED_PROXY_PORT) if EMBED_UNIFY else "http://127.0.0.1:8088"

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
    {"id": "grok", "name": "Grok CLI", "kind": "agent",
     "detect": {"proc": [r"(^|/)grok( |$)", r"(^|/)grok-native( |$)"]},
     "cli": "grok", "port": None, "ui": None,
     "terminal": {"cmd": "grok", "cwd": "/fs/1000/ftp/技术文档"},
     "chat": None,
     "desc": "xAI Grok CLI（native TUI）；默认模型经本机 CCR :3456" +
             "（~/.grok/config.toml [models].default）→ 原生终端会话"},
    {"id": "qwenpaw", "name": "QwenPaw", "kind": "agent",
     "detect": {"proc": [r"qwenpaw app"]},
     "cli": None, "port": 8088, "ui": QWENPAW_UI,
     "terminal": None, "chat": None,
     "desc": "QwenPaw 助理框架（本 Agent 宿主），WebUI :8088" +
             ("；经 hub 注入代理 :%d 统一外框" % EMBED_PROXY_PORT if EMBED_UNIFY else "；直连（注入已关）")},
    {"id": "codebuddy", "name": "CodeBuddy Code", "kind": "agent",
     "detect": {"proc": [r"(^|/)codebuddy( |$)"]},
     "cli": None, "port": 35431, "ui": "http://127.0.0.1:35431",
     "terminal": None, "chat": None,
     "desc": "WorkBuddy 包内捆绑 CLI（`codebuddy --serve`）的遥控 Web 界面 :35431" +
             "（" + LAN_HOST_NOTE + "）→ 原生会话"},

    # ── Gateways（非 Agent，仅快捷方式）──────────────────────
    {"id": "ccr", "name": "CCR Gateway", "kind": "gateway",
     "detect": {"proc": [r"claude-code-router", r"ccr( |$)", "MainThread"]},
     "proc_cwd_hint": "/home/gztxt/.ccr",
     "cli": None, "port": 3456, "ui": None, "panel": "http://192.168.5.102:3458/?ccr_web_token=ccr-web-fixed-token-gztxt-2026",
     "desc": "Claude↔多模型路由网关 :3456/3457/3458（生命线，只观测）"},
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


# ── B 档动态发现（2026-09-14；2026-09-21 改多候选名）──────────────
# CLI watchlist 单一真相源（scanner.py 引用此处）；已安装但未进 PROFILES
# 的 CLI 自动补 agent 卡片（终端入口）。
#
# 2026-09-21 修正：原扁平名单与本机实际安装名系统性错配 —— 实装的是
# qodercli / fcc-opencode / fcc-aider / fcc-muse ...，而名单写 qoder / opencode /
# aider，which() 每次落空（grok 也是这样，装了却永不出现）。改为
# 「id → 候选名列表」：同一 Agent 允许多个可执行名（含 fcc- 前缀的
# free-claude-code 包装），逐个 which，首个命中即用；names[0] 作为卡片 id。
# 注意：不做 ~/.local/bin/fcc-* 盲枚举 —— fcc-server / fcc-desktop 等不是
# 【2026-09-25 注】free-claude-code 包已卸载（PT-20260924-15），下方 aliases 里的 `fcc-*` 入口壳
#   在本机已不存在，保留仅为历史命名兼容（不影响检测：`which` 打不到即跳过）。
# Agent，盲枚举会把网关和桌面端也变成卡片。
CLI_ALIASES: Dict[str, dict] = {
    "claude":   {"display": "Claude Code", "names": ["claude"]},
    "codex":    {"display": "Codex CLI",   "names": ["codex", "fcc-codex"]},
    "pi":       {"display": "Pi Agent",    "names": ["pi", "fcc-pi"]},
    "jcode":    {"display": "JCode",       "names": ["jcode"]},
    "hermes":   {"display": "Hermes",      "names": ["hermes", "fcc-hermes"]},
    "openclaw": {"display": "OpenClaw",    "names": ["openclaw"]},
    "qoder":    {"display": "Qoder CLI",   "names": ["qodercli", "qoder"]},
    "opencode": {"display": "OpenCode",    "names": ["opencode", "fcc-opencode"]},
    "aider":    {"display": "Aider",       "names": ["aider", "fcc-aider"]},
    "muse":     {"display": "Muse",        "names": ["muse", "fcc-muse"]},
    "cline":    {"display": "Cline",       "names": ["cline", "fcc-cline"]},
    "dsh":      {"display": "DSH",         "names": ["dsh", "fcc-dsh"]},
    "gemini":   {"display": "Gemini CLI",  "names": ["gemini"]},
    "kimi":     {"display": "Kimi CLI",    "names": ["kimi"]},
}

# 扁平候选名（scanner.py 的 cli_installed 报告用，保持同名兼容）
CLI_WATCHLIST = [n for s in CLI_ALIASES.values() for n in s["names"]]
CLI_DISPLAY = {s["names"][0]: s["display"] for s in CLI_ALIASES.values()}


# ── 探针声明（vitals.py 消费；2026-09-21 加）─────────────────────
# aliases      ：该 Agent 在本机可能的可执行名（含 fcc-* 入口壳），逐个 which
# verify_argv  ：一次性真实请求（{p} 换成短提示）。**烧 token**，只在显式「体检」时跑；
#                慢周期只跑 --version/--help 这类不碰模型的探针。
# 服务型 Agent（pi :30141、qwenpaw :8088）不在此表 —— 它们的存在性证据是自有端口应声。
# 未声明 verify_argv 的 Agent 不做 L4，停在 L2（可用但标「未实测应答」）。
#
# {m} = 本次真请求指定的模型（留空则不注入）。2026-09-22 用户口径：测试模型统一用
# Agnes-2.0-flash，但只有走 CCR 的两个 CLI 认它（实测 claude 14s 真答「好」、jcode 9s 出字）；
# grok/qodercli 传 agnes 直接报 "unknown model id"，codex 报 429 —— 它们的 agnes 路由在本机
# 根本不存在，硬塞只会把「模型标识不对」读成「Agent 坏了」。故这三个保留各自默认模型。
AGENT_PROBE: Dict[str, dict] = {
    "claude": {"verify_argv": ["claude", "-p", "{p}", "--model", "{m}"]},
    "jcode":  {"verify_argv": ["jcode", "run", "--model", "{m}", "{p}"]},
    "codex":  {"aliases": ["codex", "fcc-codex"],
               # --skip-git-repo-check 必需：cwd（技术文档根）不在 codex 信任目录里，
               # 缺这个参数 codex 会 rc=1 报「Not inside a trusted directory」——
               # 那是探针自摆一道，不是 Agent 坏了（2026-09-21 实测踩到并已修正）。
               "verify_argv": ["codex", "exec", "--skip-git-repo-check", "{p}"]},
    "hermes": {"aliases": ["hermes", "fcc-hermes"],
               "verify_argv": ["hermes", "-z", "{p}", "--cli"]},
    "grok":   {"aliases": ["grok", "fcc-grok"], "verify_argv": ["grok", "-p", "{p}"]},
    # qoder 卡片 id 是 qoder，实际可执行名 qodercli（WorkBuddy 21:51 的多候选名修正）
    "qoder":  {"aliases": ["qodercli", "qoder"], "verify_argv": ["qodercli", "-p", "{p}"]},
    # opencode：一次性非交互真请求 `opencode run -m <provider/model> "<提示>"`。
    # rt_model 必须带 **provider 前缀** 且用 CCR 的斜杠形式（本机全局配置里 provider id = ccr）；
    # 不能沿用默认 RT_MODEL=Agnes-2.0-flash —— 那是 claude/jcode 才认的写法。
    # 选 stable 非 :free 模型：CCR 每日 12:00 轮换会摘掉 429/403 成员，写死免费模型必然中午失效。
    "opencode": {"aliases": ["opencode", "fcc-opencode"],
                 "verify_argv": ["opencode", "run", "-m", "{m}", "{p}"],
                 "rt_model": "ccr/deepseek/deepseek-v4-flash"},
}


def _enrich(p: dict) -> dict:
    """把 AGENT_PROBE + CLI_ALIASES 的候选名/探针注入画像（不新增条目，只补字段）"""
    spec = dict(AGENT_PROBE.get(p.get("id"), {}))
    alias = CLI_ALIASES.get(p.get("id"))
    if alias:
        spec.setdefault("aliases", alias["names"])
    if not spec:
        return p
    out = dict(p)
    for k, v in spec.items():
        out.setdefault(k, v)
    return out


_cli_dyn_cache: Dict[bool, dict] = {}


def _dynamic_cli_agents(gate: bool = True) -> List[dict]:
    """CLI_ALIASES 中实测已安装（候选名逐个 which）且注册表未收录的 CLI → 动态 agent 画像。

    2026-09-21 第二道闸门：`which()` 命中只代表「有个同名文件能执行」，不代表该 Agent
    真的存在。fcc-* 入口壳会 rc=0 地打印「Could not find X command: muse」后退出，
    单靠 which 一口气冒出 5 张假卡。改为再问 vitals：判为 not_installed 的不进菜单。
    vitals 没结论（首次启动、探针未跑完）时**保留卡片**，宁多不误删。
    结果缓存 30s，避免每次 discovery 都扫盘。"""
    import time
    now = time.monotonic()
    cache = _cli_dyn_cache.setdefault(gate, {"ts": 0.0, "items": []})
    if now - cache["ts"] < 30:
        return cache["items"]
    covered = {p["id"] for p in PROFILES}
    covered |= {p.get("cli") for p in PROFILES if p.get("cli")}
    out: List[dict] = []
    for cid, spec in CLI_ALIASES.items():
        if cid in covered:
            continue
        hit = None
        for name in spec["names"]:
            if name in covered:
                continue
            path = which(name)
            if path:
                hit = (name, path)
                break
        if not hit:
            continue
        name, path = hit
        if gate:                                  # vitals ↔ profiles 互引，延迟导入断开环
            try:
                import vitals
                if not vitals.vitals.show_in_menu(cid):
                    continue
            except Exception:  # noqa: BLE001  判定层挂了不能把菜单清空
                pass
        out.append({
            "id": cid, "name": spec["display"],
            "kind": "agent",
            "detect": {"proc": [rf"(^|/){re.escape(name)}( |$)"]},
            "cli": name, "port": None, "ui": None,
            "terminal": {"cmd": name, "cwd": "/fs/1000/ftp/技术文档"},
            "chat": None, "dynamic": True,
            "desc": f"CLI Agent（自动发现：{path}）→ 原生终端会话"})
    cache["ts"] = now
    cache["items"] = out
    return out


def invalidate_cli_cache() -> None:
    """强制下一次重算候补卡（清掉 _dynamic_cli_agents 的 30s 缓存）。

    判定刚翻案（not_installed → usable）时，30s 缓存会把「刚装好」再压 30 秒，
    表现为「点了扫描还是没出来」。只在重判后调用，不改变缓存本身的降频意图。"""
    _cli_dyn_cache.clear()


def all_profiles(include_blocked: bool = False) -> List[dict]:
    """include_blocked=True：被判定为假卡的候补也要给出。
    体检必须覆盖它们，否则「今日判为未安装」会被永久固化：被摘掉的卡再也不会被
    重新探测，用户装好了也不会自动回来。菜单（discovery）走带门版的默认值。"""
    out = list(PROFILES) + _dynamic_cli_agents(gate=not include_blocked)
    if os.getenv("TERM_ALLOW_BASH", "1") != "0":
        out.append(SHELL_PROFILE)
    return [_enrich(p) for p in out]


def get_profile(pid: str) -> Optional[dict]:
    for p in all_profiles(include_blocked=True):   # 体检/终端拉起床都可能点名一张被摘的卡
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
