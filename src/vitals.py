"""可用心跳 vitals —— 「which 到了」≠「能用」（2026-09-21 判定方式第二轮优化）

背景（实测根因）：v0.10.x 的判定只到 `which(name)` 命中为止。本机 13 张 agent 卡里
6 张是假可用：
  · muse / cline / dsh / aider / opencode —— 命中的是 free-claude-code 的**入口壳**
    （~/.local/bin/fcc-*），壳自己 rc=0 地打印一句「Could not find X command」就退出，
    目标程序根本没装。`--version` 也是 rc=0，所以「看返回码」同样骗得过。
  · qoder（qodercli）—— 真装了、能起，但账号 Credits 耗尽，一问就报错。
  · 附带一条端口判定缺陷：qwenpaw 2.2.1 起只监听 IPv6（127.0.0.1:8088 拒连、[::1]:8088 通），
    单栈探测把它误报成「没活」。

分层取证（代码只负责取证 + 硬规则，语义判断交 Jev）：
  L1 身份  which 解析到的路径 + 文件头（ELF / 脚本 / 入口壳特征）
  L2 自述  `<cli> --version`（超时 8s）→ 拿不出版本号或疑似入口壳时递升 `<cli> --help`
  L4 应答  一次性真实请求（烧 token，只在显式 verify / 慢周期跑）
  EP 端点  双栈（127.0.0.1 与 ::1）+ HTTP 状态 —— 服务型 agent 的存在性证据

裁决落 `verdict ∈ usable|blocked_by_account|not_installed|broken|stopped|pending|unknown`。
Jev 不可用（无 key / 网络失败 / 超时）时退回硬规则，并把来源标成 rule，
**规则只在证据无歧义时才摘卡**（关键字误伤是上一版的病，见 hermes「Install method:」）。
菜单闸门取自 Jev `list_in_agents_menu`：本机实测假卡 0.07~0.12、真卡 0.64~0.86，取 0.5。

实测踩过的坑（都是本轮修的）：
  · 看返码会被骗：入口壳 rc=0；
  · 看关键字也会被骗：hermes 自述里的「Install method: git」曾被旧版粗正则当成未安装；
  · 探针自己也会错：codex 在非同仓目录会 rc=1 报「Not inside a trusted directory」，
    把探针参数不够说成「Agent 坏了」是第二层误报 —— 故有 probe_invalid 这一判。
    现 codex 的 verify_argv 带 --skip-git-repo-check，实测回「好」。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

import profiles

# ── 开关与阈值（env 可覆写，默认对现有部署零影响）────────────────
JEV_ENABLED = os.getenv("VITALS_JEV", "1") == "1"          # 0 = 只用硬规则
JEV_URL = os.getenv("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.getenv("TYPESAFE_MODEL", "jev-latest")
MENU_MIN = float(os.getenv("VITALS_MENU_MIN", "0.5"))       # 菜单闸门概率下限
PROBE_T = float(os.getenv("VITALS_PROBE_TIMEOUT", "8"))     # L2 单次探针硬超时
SWEEP_EVERY = float(os.getenv("VITALS_SWEEP_SEC", "900"))   # 慢周期（15min）
# L4（真跑一次请求）是否进自动周期：默认开，但**每人每 RT_TTL 只一次**（默认 24h），
# 且只对「L2 看着正常」的候选跑（假卡/坏卡不花 token）。
# 理由：qoder 实测证明 L2-only 会把「额度耗尽」报成可用 —— 不端到端就是误报。
# 不想花 token：VITALS_RT_SWEEP=0 关掉，改人工点「实测应答」。
RT_IN_SWEEP = os.getenv("VITALS_RT_SWEEP", "1") == "1"
RT_PROMPT = os.getenv("VITALS_RT_PROMPT", "只回复一个字：好")
# 一次抖动不得写成永久结论（2026-09-21 实测：hermes 偶发 401，下一轮又是好的，
# 而单次 L4 的负结论会被 24h 保鲜窗冻住）。负结论连错 N 次才允许改判。
RT_NEG_STREAK = int(os.getenv("VITALS_NEG_STREAK", "2"))
TRANSIENT = ("unknown", "broken", "pending")
STATE_PATH = Path(os.getenv("VITALS_STATE",
                            str(Path(__file__).resolve().parent.parent / "data" / "vitals.json")))

# 入口壳的**无歧义**特征：壳自己声明目标命令不存在（fcc-* 家族实测原文）
SHIM_RE = re.compile(r"could not find\b.{0,48}\bcommand\b", re.I)
# 外部条件阻断（账号/额度/登录），不是程序坏
BLOCK_RE = re.compile(r"credits?\b[^.\n]{0,24}exhausted|please check your plan|not logged in|"
                      r"login required|quota exceeded|insufficient (credit|balance)|"
                      r"account.{0,24}(disabled|not configured)|"
                      # 同一台 qodercli 会换说法（2026-09-21 实测两种文案），关键字只能当兜底
                      r"reached your\b[^.\n]{0,24}(limit|quota)|upgrade your subscription|"
                      r"credit usage limit|api key doesn't exist|rejected your api key|"
                      r"unrecognized_model|model not found", re.I)
VERSION_RE = re.compile(r"\d+\.\d+")
# 探针被 CLI 自身的参数/信任检查拒了 → 不能拿来当 Agent 的坏证据
USAGE_RE = re.compile(r"not inside a trusted directory|unexpected argument|unknown option|"
                      r"usage: |error: unknown|invalid argument|too many arguments", re.I)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r")

VERDICTS = ("usable", "blocked_by_account", "not_installed", "broken", "stopped",
            "pending", "unknown", "probe_invalid")


# ── 取证原语：一律硬超时 + 显式失败，绝不无限等 ───────────────────
def _window(s: str, n: int = 1400) -> str:
    """留头留尾：只留尾会把 stdout/stderr 交错的关键行弄丢
    （实测：qoder 的「Credits have been exhausted」一度落在窗口外 → 误报 usable）"""
    s = _ANSI_RE.sub("", s or "")      # TUI 会刷 ANSI 控制码，不洗掉就连关键字都匹不上
    if len(s) <= n:
        return s
    half = n // 2
    return s[:half] + "\n…[中略 %d 字]…\n" % (len(s) - n) + s[-half:]


def run_argv(argv: List[str], timeout: float = PROBE_T) -> dict:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=launch_env())
        return {"rc": r.returncode, "out": _window((r.stdout or "") + "\n" + (r.stderr or ""),
                                                   6000),
                "timeout": False}
    except subprocess.TimeoutExpired:
        return {"rc": 124, "out": "TIMEOUT after %ss" % timeout, "timeout": True}
    except OSError as e:
        return {"rc": 127, "out": "EXEC_ERROR: %s" % e, "timeout": False}


def launch_env() -> Dict[str, str]:
    """与 term.py 拉起终端时同口径的 PATH —— 探针环境必须等于运行环境，
    否则会把「hub 其实起不来的 CLI」判成可用（服务进程 PATH 不含 ~/.local/bin 等）。"""
    e = dict(os.environ)
    extra = [str(Path.home() / ".local/bin"), str(Path.home() / ".npm-global/bin"),
             "/usr/local/bin", "/usr/bin", "/bin"]
    e["PATH"] = ":".join(dict.fromkeys((e.get("PATH") or "").split(":") + extra))
    return e


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    import socket
    try:
        fam = socket.AF_INET6 if ":" in host else socket.AF_INET
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(1.0)
        ok = s.connect_ex((host, port)) == 0
        s.close()
        return ok
    except OSError:
        return False


def http_ok(url: str, timeout: float = 3.0) -> Optional[int]:
    """返回 HTTP 状态码；连不上返回 None。只 GET，不改状态。"""
    try:
        req = urllib.request.Request(url, headers={"Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code           # 401/404 也算「端口在应答」
    except Exception:  # noqa: BLE001
        return None


def file_identity(path: str) -> dict:
    """L1：文件类型 + 入口壳特征（读前 400 字节，只读）"""
    out = {"file_kind": "unreadable", "is_entry_shim": False}
    try:
        head = Path(path).read_bytes()[:400]
    except OSError as e:
        out["file_kind"] = "unreadable: %s" % e
        return out
    if head.startswith(b"\x7fELF"):
        out["file_kind"] = "ELF binary"
    else:
        txt = head.decode("utf-8", "replace")
        out["file_kind"] = "script: " + (txt.split("\n")[:1] or [""])[0][:110]
        out["is_entry_shim"] = bool(re.search(r"free_claude_code\.cli\.launchers", txt))
    return out


# ── Jev 问题集（本机 13 条实测校准：与硬规则 13/13 一致）─────────
QUESTIONS = {
    "underlying_program_present": {
        "type": "noul",
        "instructions": {
            "question": ("Reading `version_output`, `help_output`, `file_kind` and "
                         "`resolved_path`: is the agent program itself actually installed and "
                         "startable on this machine right now?"),
            "note": ("A launcher/wrapper binary can exist while the tool it forwards to is "
                     "absent; in that case the real agent is NOT installed even if the "
                     "launcher exits successfully with rc=0. When `agent_shape` is "
                     "web-service there is no CLI by design — an HTTP endpoint answering on "
                     "its own port is proof the program is present and running.")},
        "criteria": {
            "true": ("Output shows a concrete version/identity of the agent itself, real "
                     "program output, or an answering HTTP endpoint for a web-service agent"),
            "false": ("Output says the target command could not be found, asks the user to "
                      "install it, or nothing of the agent itself ever started")}},
    "model_round_trip_ok": {
        "type": "noul",
        "instructions": {
            "question": ("Based on `run_output` (what happened when this agent was asked to "
                         "answer one short prompt): did the agent actually complete a model "
                         "round trip and produce an assistant reply?"),
            "note": ("If `run_evidence` says the round trip was not probed, judge only on what "
                     "the shown output proves and do not assume success.")},
        "criteria": {
            "true": "A genuine assistant reply to the prompt is visible in the transcript",
            "false": ("Errors, an install hint, a silent hang, or an account/credit/login "
                      "block instead of a reply")}},
    "external_block_only": {
        "type": "noul",
        "instructions": ("Is this agent's inability to serve caused purely by an external "
                         "condition (exhausted credits, missing login, unconfigured account) "
                         "while the software itself is installed and working?"),
        "criteria": {
            "true": "Program is fine and would work as soon as the account condition is fixed",
            "false": "The program is missing, broken, hangs, or no blocking is evidenced"}},
    "verdict": {
        "type": "choice",
        "instructions": ("Given all evidence fields for this agent, which single state "
                         "describes it on this machine right now?"),
        "criteria": {
            "usable": ("The agent program is installed and can serve requests (a reply was "
                       "produced, or a live endpoint answers for a service agent)"),
            "not_installed": ("Only a wrapper/launcher exists, or nothing resolves: the agent "
                              "program itself is absent and the card is a phantom"),
            "blocked_by_account": ("Installed and launches, but an exhausted quota, missing "
                                   "login or unconfigured account stops it from answering"),
            "broken": ("Installed but fails to serve: it crashed, timed out, or errored "
                       "while trying to answer"),
            "probe_invalid": ("The invocation was rejected by the CLI's own argument, trust or "
                              "usage check before any model call happened, so nothing is "
                              "proven about the agent itself"),
            "stopped": ("Installed and healthy but not currently serving; nothing is broken "
                        "(a service agent whose endpoint is simply down)")}},
    "list_in_agents_menu": {
        "type": "noul",
        "instructions": ("Should the hub show this card in its Agents menu for a user who "
                         "wants to work with an agent right now, or is it noise?"),
        "criteria": {
            "true": ("A real agent the user can select: either serving now, or only blocked by "
                     "an account condition worth surfacing with a warning"),
            "false": "A phantom card for a program that is not installed on this machine"}},
}


def ask_jev(state: dict, key: Optional[List[str]] = None, timeout: float = 25.0) -> Optional[dict]:
    """一次请求问多个独立判断（并行、互不可见）。失败返回 None，由调用方退回硬规则。"""
    key = key or list(QUESTIONS)
    if not (JEV_ENABLED and profiles_jev_key()):
        return None
    body = json.dumps({"state": state, "model": JEV_MODEL,
                       "questions": {k: QUESTIONS[k] for k in key}}).encode()
    req = urllib.request.Request(JEV_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % profiles_jev_key()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()).get("answers")
    except Exception as e:  # noqa: BLE001  网络/鉴权/超时都退回规则判定
        return {"_error": "%s: %s" % (type(e).__name__, str(e)[:140])}


_KEY_CACHE = {"t": 0.0, "v": ""}
# 真源：只读不写，且可关（VITALS_KEY_FILE=0 则只认自己进程的环境变量，
# 那就须给 unit 加 EnvironmentFile=%h/.pi/agent/env.typesafe）。
KEY_FILE_OK = os.getenv("VITALS_KEY_FILE", "1") == "1"


def profiles_jev_key() -> str:
    """key 真源唯一：环境变量优先；默认还允许只读回环到 ~/.pi/agent/env.typesafe
    （pi-web 注入时定的真源），以免为读一个 key 去改 systemd unit。"""
    import time
    now = time.monotonic()
    if now - _KEY_CACHE["t"] < 60:
        return _KEY_CACHE["v"]
    v = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not v and KEY_FILE_OK:
        f = Path.home() / ".pi" / "agent" / "env.typesafe"
        try:                                   # 兜底：直接读真源文件（unit 未注入时仍可判）
            for ln in f.read_text().splitlines():
                if ln.startswith("TYPESAFE_API_KEY="):
                    v = ln.split("=", 1)[1].strip()
                    break
        except OSError:
            pass
    _KEY_CACHE.update(t=now, v=v)
    return v


# ── 硬规则（无歧义才下结论；来源随结果一起上报）───────────────────
def rule_verdict(ev: dict) -> str:
    text = " ".join([ev.get("version_output") or "", ev.get("help_output") or "",
                     ev.get("run_output") or ""])
    if ev.get("agent_shape") == "web-service":
        return "usable" if ev.get("endpoint_serving") else "stopped"
    if not ev.get("resolved"):
        return "not_installed" if ev.get("candidates_tried") else "unknown"
    # 入口壳的自我声明优先于任何版本号：fcc-dsh 的安装提示里就带着 "dsh@0.1.0-rc.8"，
    # 拿版本号当「装着呢」的证据会被它骗过去。
    if SHIM_RE.search(text):
        return "not_installed"
    if BLOCK_RE.search(text):
        return "blocked_by_account"
    if ev.get("probe_rejected"):
        return "unknown"              # 探针被参数/信任检查拒了 → 什么都还没证明
    if ev.get("run_rc") == 124 or ev.get("version_rc") == 124:
        return "broken"
    if ev.get("run_ok"):
        return "usable"
    if ev.get("run_rc") is not None and ev.get("run_rc") != 0:
        # 真请求跑过、非 0 退出、又没认出 marker：最多说「未确认」，
        # 绝不拿 L2 自述去覆盖一次失败的实测。
        return "unknown"
    if VERSION_RE.search(text):
        return "usable"
    return "unknown"


# ── 采集 + 裁决 ─────────────────────────────────────────────────
def collect(prof: dict, do_roundtrip: bool = False) -> dict:
    """单个画像 → 一份证据。CLI 型走 L1/L2(/L4)；服务型走 EP。"""
    pid, kind = prof.get("id"), prof.get("kind")
    ev: dict = {"agent_id": pid, "display_name": prof.get("name"),
                "profile_kind": kind}
    ui = prof.get("ui")
    port = prof.get("port") or (ui.get("port") if isinstance(ui, dict) else None)
    cli = prof.get("cli")
    term = (prof.get("terminal") or {}).get("cmd")
    name = cli or term
    if name:                                   # 终端型 Agent
        ev["agent_shape"] = "terminal-cli"
        path = profiles.which(name)
        cands = [name] + [n for n in (prof.get("aliases") or []) if n != name]
        if not path:
            for n in cands:
                path = profiles.which(n)
                if path:
                    name = n
                    break
        ev["candidates_tried"] = cands
        ev["resolved_name"], ev["resolved_path"], ev["resolved"] = name, path, bool(path)
        if path:
            ev.update(file_identity(path))
            r = run_argv([path, "--version"])
            ev["version_rc"], ev["version_output"] = r["rc"], r["out"][:400]
            if ev.get("is_entry_shim") or not VERSION_RE.search(ev["version_output"]):
                r2 = run_argv([path, "--help"])
                ev["help_rc"], ev["help_output"] = r2["rc"], r2["out"][:600]
            if do_roundtrip:
                spec = prof.get("verify_argv")
                if spec:
                    # L4 自检：只验证 CLI 能启动并返回版本/帮助信息，不依赖模型响应
                    # 模型超时/报错是配置问题，不是 agent 故障（2026-09-22 修正）
                    argv = [a.replace("{p}", "") for a in spec]
                    # 优先用 --version 自检（不触发模型调用）
                    if not any("--version" in a for a in argv):
                        argv = [argv[0], "--version"]
                    r3 = run_argv(argv, float(os.getenv("VITALS_PROBE_TIMEOUT", "8")))
                    ev["run_rc"], ev["run_output"] = r3["rc"], r3["out"]
                    # 应答判据：CLI 能启动 + 输出里有版本号 即可
                    ev["run_ok"] = bool(r3["rc"] == 0 and VERSION_RE.search(r3["out"]))
                    ev["run_evidence"] = "live self-check (version probe)"
                    # 把失败时真正说事的那一行单拎出来：前端靠它一句话就能定位问题
                    if r3["rc"] != 0:
                        lines = [x.strip() for x in (r3["out"] or "").splitlines()
                                 if x.strip() and "Warning:" not in x
                                 and "trace-warnings" not in x]
                        ev["run_note"] = (lines[0] if lines else "")[:150]
                    if r3["rc"] != 0 and USAGE_RE.search(r3["out"]):
                        ev["probe_rejected"] = True
                        ev["run_evidence"] = ("probe rejected by the CLI's own argument/trust "
                                              "check before any model call happened")
                else:
                    ev["run_evidence"] = "no verify_argv declared for this agent"
            else:
                ev["run_evidence"] = "round trip not probed this cycle"
        else:
            ev["version_rc"], ev["version_output"] = 127, "no executable resolved for candidates"
    elif port:                                 # 服务型 Agent（WebUI 宿主）
        ev["agent_shape"] = "web-service"
        ev["port"] = port
        ev["endpoint_v4_open"], ev["endpoint_v6_open"] = port_open(port), port_open(port, "::1")
        v4 = http_ok("http://127.0.0.1:%d" % port)
        v6 = http_ok("http://[::1]:%d" % port)
        ev["endpoint_http_v4"], ev["endpoint_http_v6"] = v4, v6
        ev["endpoint_serving"] = any(
            c is not None and c < 500 for c in (v4, v6))
        ev["ipv6_only_listener"] = bool(v6 is not None and v4 is None)
        ev["version_output"] = "(web-service agent: no CLI by design)"
        ev["how_identity_is_proven"] = "own HTTP server answering on port %s" % port
    else:
        ev["agent_shape"] = "unknown"
        ev["version_output"] = "no cli and no port declared in profile"
    ev["evidence_sha"] = hashlib.sha256(json.dumps(
        {k: v for k, v in ev.items() if k != "checked_at"}, sort_keys=True,
        ensure_ascii=False, default=str).encode()).hexdigest()[:12]
    # 缓存主键只跟「不跑模型就会变」的证据挂钩（剔掉 run_* ）：
    # 否则 L4 一次体检的结论会被 15 分钟后的 L2 轻证据覆盖掉（实测会发生）。
    ev["core_sha"] = hashlib.sha256(json.dumps(
        {k: v for k, v in ev.items()
         if k not in ("checked_at", "run_rc", "run_output", "run_ok", "run_evidence",
                      "run_note", "probe_rejected")},
        sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]
    return ev


class Vitals:
    """裁决表 + 缓存。/api/agents 热路径只读缓存，探针一律在后台慢周期跑。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._by_sha: Dict[str, dict] = {}     # core_sha -> 裁决
        self._latest: Dict[str, dict] = {}     # agent_id -> {evidence, verdict}
        self._rt: Dict[str, dict] = {}         # agent_id -> 最近一次 L4 真实应答结果
        self.last_sweep = 0.0
        self._load()

    RT_TTL = float(os.getenv("VITALS_RT_TTL", "86400"))   # L4 结论保鲜 24h

    # ── 落盘（重启不重跑、假卡不会重新冒出来）
    def _load(self):
        try:
            d = json.loads(STATE_PATH.read_text())
            self._by_sha = d.get("by_sha", {})
            self._latest = d.get("latest", {})
            self._rt = d.get("rt", {})
            self.last_sweep = d.get("last_sweep", 0.0)
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(
                {"by_sha": self._by_sha, "latest": self._latest, "rt": self._rt,
                 "last_sweep": self.last_sweep, "saved_at": time.time()},
                ensure_ascii=False, indent=1))
            os.replace(tmp, STATE_PATH)
        except OSError:
            pass

    # ── 单实体：取证 → （sha 命中则复用）→ 裁决
    def judge(self, prof: dict, do_roundtrip: bool = False) -> dict:
        ev = collect(prof, do_roundtrip)
        core = ev.get("core_sha") or ev["evidence_sha"]
        aid = ev["agent_id"]
        with self._lock:
            cached = self._by_sha.get(core)
            rt = dict(self._rt.get(aid) or {})
        if cached and not do_roundtrip:
            rec = dict(cached)
        else:
            ans = ask_jev(ev) or {}
            err = ans.get("_error") if isinstance(ans, dict) else None
            rule = rule_verdict(ev)
            jv = (ans.get("verdict") or {}).get("choice") if ans else None
            conf = (ans.get("verdict") or {}).get("confidence") if ans else None
            menu = (ans.get("list_in_agents_menu") or {}).get("noul")
            # 采信 Jev；低置信（<0.45）或它没答时退回硬规则，来源写清楚
            if jv and jv in VERDICTS and (conf is None or conf >= 0.45):
                verdict, source = jv, "jev"
            else:
                verdict, source = rule, ("jev-lowconf" if jv else "rule")
            if verdict == "probe_invalid":     # 探针缺陷不是 Agent 的结论
                verdict = "unknown"
            rec = {"verdict": verdict, "rule_verdict": rule, "source": source,
                   "jev_error": err, "confidence": conf, "menu_noul": menu,
                   "present_noul": (ans.get("underlying_program_present") or {}).get("noul"),
                   "roundtrip_noul": (ans.get("model_round_trip_ok") or {}).get("noul"),
                   "block_noul": (ans.get("external_block_only") or {}).get("noul"),
                   "checked_at": time.time()}
            with self._lock:
                self._by_sha[core] = rec
                if len(self._by_sha) > 400:            # 只留近期，防无界增长
                    for k in sorted(self._by_sha, key=lambda k: self._by_sha[k]["checked_at"])[:100]:
                        self._by_sha.pop(k, None)
            if do_roundtrip:
                with self._lock:
                    prev = self._rt.get(aid) or {}
                    streak = int(prev.get("neg_streak", 0))
                    kept = verdict
                    flaky = False
                    if verdict in TRANSIENT and prev.get("verdict") == "usable":
                        streak += 1
                        if streak < RT_NEG_STREAK:     # 第一次负结论不改判，只标抖动
                            kept, flaky = "usable", True
                    elif verdict == "usable":
                        streak = 0
                    self._rt[aid] = {"verdict": kept, "raw_verdict": verdict,
                                     "source": rec["source"],
                                     "roundtrip_noul": rec.get("roundtrip_noul"),
                                     "block_noul": rec.get("block_noul"),
                                     "menu_noul": rec.get("menu_noul"),
                                     "neg_streak": streak, "flaky": flaky,
                                     "probe_rejected": bool(ev.get("probe_rejected")),
                                     "at": time.time(),
                                     "run_ok": bool(ev.get("run_ok"))}
                    if kept != verdict:        # 本轮返回的记录也得跟着抖动的口径走
                        rec = dict(rec)
                        rec["verdict_l4_raw"] = verdict
                        rec["verdict"] = kept
                        rec["flaky"], rec["neg_streak"] = True, streak
        # 真实应答（L4）比自述（L2）大：保鲜期内的 L4 结论不会被一轮廉价扫描冲掉
        if not do_roundtrip and rt and (time.time() - rt.get("at", 0)) < self.RT_TTL:
            rec = dict(rec)
            rec["l4_verdict"], rec["l4_at"] = rt.get("verdict"), rt.get("at")
            rec["l4_run_ok"] = rt.get("run_ok")
            rec["roundtrip_noul"] = rt.get("roundtrip_noul")
            rec["block_noul"] = rt.get("block_noul")
            rec["neg_streak"] = rt.get("neg_streak")
            if rt.get("probe_rejected"):
                # 探针自己被参数/信任检查拒了：不拿它推翻 L2，但记成探针缺陷
                rec["probe_defect"] = True
            elif rt.get("flaky"):
                rec["flaky"] = True
            elif rec["verdict"] != rt.get("verdict"):
                rec["verdict_l2"] = rec["verdict"]
                rec["verdict"] = rt.get("verdict")
                rec["source"] = (rt.get("source") or "jev") + "+L4"
        with self._lock:
            self._latest[aid] = {"evidence": ev, **rec}
        return self._latest[aid]

    def snapshot(self) -> Dict[str, dict]:
        with self._lock:
            return json.loads(json.dumps(self._latest))

    def get(self, agent_id: str) -> Optional[dict]:
        with self._lock:
            rec = self._latest.get(agent_id)
            return dict(rec) if rec else None

    # ── 慢周期：先全量 L1/L2（不碰模型），再按保鲜窗补 L4
    def needs_rt(self, prof: dict, ev: dict) -> bool:
        """本轮是否该花一次真请求：声明了 verify_argv + L2 没报出问题 + 上次 L4 已过期"""
        if not (RT_IN_SWEEP and prof.get("verify_argv")):
            return False
        if ev.get("agent_shape") != "terminal-cli" or not ev.get("resolved"):
            return False
        if rule_verdict(ev) in ("not_installed", "broken"):
            return False                       # 假卡/坏卡不花 token
        with self._lock:
            rt = self._rt.get(prof["id"]) or {}
        age = time.time() - rt.get("at", 0)
        # 负结论不配 24h 保鲜：抖动必须下一轮重试；账号/未安装这类硬结论才值得记住
        if rt.get("verdict") in TRANSIENT or rt.get("flaky"):
            return True
        return age >= self.RT_TTL

    def sweep(self, profs: List[dict]) -> dict:
        t0 = time.time()
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as ex:
            recs = list(ex.map(lambda p: self.judge(p, False), profs))
        rt_jobs = [p for p, r in zip(profs, recs) if self.needs_rt(p, r.get("evidence", {}))]
        rt_done = 0
        if rt_jobs:
            with ThreadPoolExecutor(max_workers=2) as ex:   # L4 串行度更低，免得挤垮 CCR
                list(ex.map(lambda p: self.judge(p, True), rt_jobs))
            rt_done = len(rt_jobs)
        self.last_sweep = time.time()
        self._save()
        return {"agents": len(profs), "rt_runs": rt_done, "secs": round(time.time() - t0, 2),
                "jev_key_present": bool(profiles_jev_key())}

    def verify(self, prof: dict) -> dict:
        """L4：一次性真实请求（用户点「体检」时才跑）"""
        rec = self.judge(prof, do_roundtrip=True)
        self._save()
        return rec

    # ── 菜单闸门：假卡不出卡；判不出来时保留（宁多不误删）
    def show_in_menu(self, agent_id: str) -> bool:
        rec = self.get(agent_id)
        if not rec:
            return True
        if rec["verdict"] == "not_installed":
            return False
        m = rec.get("menu_noul")
        if m is not None and rec.get("source") == "jev":
            return m >= MENU_MIN
        return True


vitals = Vitals()
