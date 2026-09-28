"""各 Agent 默认模型的统一设置后端（v0.13.41：设置 → 模型子菜单）。

口径（2026-09-27 用户裁定，三条）：
  ① **双写**：Hub 侧落 per-agent 模型（拉起终端时按白名单注入 --model）+ 同时写入
     该 agent **自己的**配置文件；
  ② **CCR 网关自身的 Router（default/think/longContext/webSearch/background）
     只读展示、不写** —— 它属三方互斥军规保护面，且 ccr 运行时写 config.json
     会被运行态覆盖（fileConfig 只是落盘投影）；
  ③ 写前预览 + 口令 + 时间戳备份三件套（军规铁律：无备份禁止写入）。

为什么不做"通用配置文件编辑器"：每家 agent 的模型落点形状都不同（json / toml /
yaml，pi 还要顺带补模型清单、grok 要建命名 profile），逐一白名单化才给得出
**可读的 diff** 与**可回滚的落点**。一个通用写手必然退化成"整文件重写"，那正是
09-07 pi 改 CCR 配置时把同文件其它 profile 一起改坏的同款事故面。

落点（本机 09-27 实测，与官方文档互证）：
  claude  ~/.claude/settings.json     model + env.{ANTHROPIC_MODEL,CCR_CLAUDE_CODE_MODEL,CODEXL_CLAUDE_CODE_MODEL}
  jcode   ~/.jcode/config.toml        [provider].default_model（+ [providers.ccr].default_model 若存在）
  codex   ~/.codex/config.toml        CCR 托管块内 model = "..."
  pi      ~/.pi/agent/settings.json   defaultModel / defaultProvider（模型不在清单时补进 models.json）
  grok    ~/.grok/config.toml         [model.ccr-hub] 块 + [models].default
  hermes  ~/.hermes/config.yaml       model.default / model.provider
  codebuddy（WorkBuddy 包内 CLI）    **不支持**：--model 只认自有清单
          （hy4-preview / hy3 …，实测 help 输出），与 CCR 的 provider/model ID 不通用。
"""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import db

# ── 模型 ID 形状闸门 ────────────────────────────────────────────────
# CCR /v1/models 实测形状：alibaba/qwen3.8-max、openrouter/poolside/laguna-xs-2.1:free。
# 收紧到这个字符集：模型 ID 会被写进 JSON/TOML 的**双引号字符串**与终端 argv，
# 放宽字符集等于给引号逃逸与 argv 注入开门。
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,119}$")

#: 终端注入白名单（实测 `<cli> --help` 输出）：值 = 传给该 CLI 的 flag。
#: 只有这里在册的 agent 才会被 hub 拉起时追加 --model；其余仅写配置文件。
MODEL_ARGV: Dict[str, str] = {
    "claude": "--model",
    "jcode": "--model",
    "codex": "--model",
    "grok": "--model",
    "pi": "--model",
    "hermes": "-m",          # hermes help 只给短选项 [-m MODEL]
}

#: pi 走 CCR 时用的 provider（~/.pi/agent/models.json 里 baseUrl = CCR 的那个）
PI_CCR_PROVIDER = "ccr-free"
#: grok 的命名 profile 键（[model.<key>] + [models].default 指向它）
GROK_PROFILE_KEY = "ccr-hub"
#: hermes 走 CCR 的 provider 名（config.yaml 顶层 model.provider）
HERMES_CCR_PROVIDER = "ccr-free"


class ModelCfgError(Exception):
    """带 HTTP 语义的配置错误（message 直接回给前端）。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _ccr_api_key() -> str:
    """CCR 的本地 key 只从网关配置里现读，不在本文件里硬编码任何凭据。

    grok 的 [model.*] 块必须带 api_key 才能打 CCR；优先复用文件里**已有的** CCR
    块（那把 key 是本机已在用的），没有才回落到 CCR config.json 的 APIKEY，
    再没有就留空并在 changes 里如实标注（宁可让用户自己补，也不猜一个假 key）。"""
    try:
        d = json.loads((_home() / ".claude-code-router" / "config.json").read_text(encoding="utf-8"))
        return str(d.get("APIKEY") or "")
    except Exception:  # noqa: BLE001
        return ""


def _home() -> Path:
    """每次调用现取：L0 用例靠改 HOME 造假盘，模块级常量会让夹具失效。"""
    return Path(os.path.expanduser("~"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise ModelCfgError(f"配置文件不存在：{p}", 404) from e
    except OSError as e:  # noqa: BLE001
        raise ModelCfgError(f"配置文件不可读：{p}（{e}）", 500) from e


def _backup(p: Path, tag: str) -> str:
    """军规铁律：落笔前先时间戳备份。返回备份路径（相对 HOME 展示）。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = p.with_name(p.name + f".bak-{stamp}-hub-modelcfg-{tag}")
    # 同一秒内连续落笔（设置页保存刚过、巡检写回就跟上来）会让时间戳撞车 ⇒
    # 后一份把前一份拷成同一个文件名覆盖掉，"留了备份"就成了假象。撞车就加序号。
    n = 1
    while dst.exists():
        n += 1
        dst = p.with_name(p.name + f".bak-{stamp}-{n:02d}-hub-modelcfg-{tag}")
    shutil.copy2(p, dst)
    return str(dst)


# ── 定点改写工具（只动目标 key，绝不整文件重写 / 全局替换）────────────
def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


def _section_range(lines: List[str], section: Optional[str]) -> Tuple[int, int]:
    """返回 (正文起, 正文止)：section=None 表示整个文件。"""
    if not section:
        return 0, len(lines)
    head = f"[{section}]"
    start = -1
    for i, ln in enumerate(lines):
        if ln.strip() == head:
            start = i
            break
    if start < 0:
        return -1, -1
    end = len(lines)
    for j in range(start + 1, len(lines)):
        s = lines[j].strip()
        if s.startswith("[") and s.endswith("]"):
            end = j
            break
    return start + 1, end


def _replace_in_range(lines: List[str], lo: int, hi: int, key: str, new_line: str) -> Tuple[bool, str]:
    """在 [lo,hi) 内把 key 的赋值行换成 new_line；不存在则返回插入用的 old=""。"""
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i in range(lo, hi):
        if pat.match(lines[i]):
            old = lines[i].split("=", 1)[1].strip().strip('"')
            lines[i] = new_line
            return True, old
    return False, ""


def _set_kv(text: str, key: str, value: str, section: Optional[str] = None,
            where: Optional[str] = None) -> Tuple[str, dict]:
    """TOML/YAML 通用定点赋值：改得到就改，改不到就插（section 缺失则整段追加）。"""
    new_line = f'{key} = "{_esc(value)}"'
    lines = text.splitlines()
    lo, hi = _section_range(lines, section)
    if lo < 0:
        block = (f"[{section}]\n" if section else "") + new_line + "\n"
        joined = text if text.endswith("\n") or not text else text + "\n"
        return joined + "\n" + block, {"where": where or f"{section}.{key}", "from": "", "to": value,
                                       "op": "add-section"}
    found, old = _replace_in_range(lines, lo, hi, key, new_line)
    if found:
        return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), \
            {"where": where or f"{section}.{key}", "from": old, "to": value, "op": "set"}
    insert_at = hi
    lines.insert(insert_at, new_line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), \
        {"where": where or f"{section}.{key}", "from": "", "to": value, "op": "add-key"}


def _set_blocked_kv(text: str, begin: str, end: str, key: str, value: str,
                    where: str) -> Tuple[str, dict]:
    """在 BEGIN/END 标记块内赋值（codex 的 CCR 托管块就用这个）。"""
    lines = text.splitlines()
    lo = next((i for i, ln in enumerate(lines) if ln.strip().startswith(begin)), -1)
    hi = next((i for i, ln in enumerate(lines) if ln.strip().startswith(end)), len(lines))
    if lo < 0:
        raise ModelCfgError(f"未找到托管块起始标记：{begin}（手写配置已被改形，拒绝落笔）", 409)
    lo += 1
    found, old = _replace_in_range(lines, lo, hi, key, f'{key} = "{_esc(value)}"')
    if not found:
        lines.insert(hi, f'{key} = "{_esc(value)}"')
        old = ""
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), \
        {"where": where, "from": old, "to": value, "op": "set" if old else "add-key"}


def _set_comment_kv(text: str, prefix: str, value: str, where: str) -> Tuple[str, dict]:
    """改注释行里记的那份值（codex 的 '# CCR configured model = "..."'）。"""
    pat = re.compile(rf"^(\s*{re.escape(prefix)}\s*=\s*)\"([^\"]*)\"", re.M)
    m = pat.search(text)
    if not m:
        return text, {"where": where, "from": "", "to": value, "op": "skip"}
    old = m.group(2)
    return pat.sub(lambda _m: f'{_m.group(1)}"{_esc(value)}"', text, count=1), \
        {"where": where, "from": old, "to": value, "op": "set"}


def _toml_get(text: str, section: Optional[str], key: str) -> str:
    try:
        import tomllib
        data = tomllib.loads(text)
    except Exception:  # noqa: BLE001 —— 读坏了只影响展示，不许 500 打断设置页
        return ""
    node = data
    if section:
        for part in section.split("."):
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                return ""
    v = node.get(key) if isinstance(node, dict) else None
    return str(v) if v is not None else ""


# ── 各家读写器 ──────────────────────────────────────────────────────
def _claude_paths() -> List[Path]:
    return [_home() / ".claude" / "settings.json"]


def _claude_read() -> dict:
    p = _claude_paths()[0]
    if not p.exists():
        return {"current": "", "files": [], "note": "未找到 ~/.claude/settings.json"}
    try:
        d = json.loads(_read_text(p))
    except json.JSONDecodeError as e:
        return {"current": "", "files": [str(p)], "note": f"settings.json 解析失败：{e}"}
    env = d.get("env") or {}
    return {"current": str(d.get("model") or ""),
            "provider": "ccr",
            "extra": {"env.ANTHROPIC_MODEL": str(env.get("ANTHROPIC_MODEL") or ""),
                      "env.CCR_CLAUDE_CODE_MODEL": str(env.get("CCR_CLAUDE_CODE_MODEL") or "")},
            "files": [str(p)]}


def _claude_write(model: str) -> List[Tuple[Path, str, List[dict]]]:
    p = _claude_paths()[0]
    text = _read_text(p)
    d = json.loads(text)
    changes: List[dict] = []
    old = str(d.get("model") or "")
    d["model"] = model
    changes.append({"where": "settings.json:model", "from": old, "to": model, "op": "set"})
    env = d.setdefault("env", {})
    for k in ("ANTHROPIC_MODEL", "CCR_CLAUDE_CODE_MODEL", "CODEXL_CLAUDE_CODE_MODEL"):
        oldv = str(env.get(k) or "")
        env[k] = model
        changes.append({"where": f"env.{k}", "from": oldv, "to": model, "op": "set"})
    return [(p, json.dumps(d, ensure_ascii=False, indent=2) + "\n", changes)]


def _jcode_read() -> dict:
    p = _home() / ".jcode" / "config.toml"
    if not p.exists():
        return {"current": "", "files": [], "note": "未找到 ~/.jcode/config.toml"}
    text = _read_text(p)
    return {"current": _toml_get(text, "provider", "default_model"),
            "provider": _toml_get(text, "provider", "default_provider"),
            "files": [str(p)]}


def _jcode_write(model: str):
    p = _home() / ".jcode" / "config.toml"
    text = _read_text(p)
    changes = []
    text, c1 = _set_kv(text, "default_model", model, section="provider")
    changes.append(c1)
    text, c2 = _set_kv(text, "default_model", model, section="providers.ccr")
    changes.append(c2)
    return [(p, text, changes)]


def _codex_read() -> dict:
    p = _home() / ".codex" / "config.toml"
    if not p.exists():
        return {"current": "", "files": [], "note": "未找到 ~/.codex/config.toml"}
    text = _read_text(p)
    return {"current": _toml_get(text, None, "model"),
            "provider": _toml_get(text, None, "model_provider"),
            "files": [str(p)]}


def _codex_write(model: str):
    p = _home() / ".codex" / "config.toml"
    text = _read_text(p)
    changes = []
    text, c1 = _set_blocked_kv(text, "# BEGIN CCR managed profile", "# END CCR managed profile",
                               "model", model, "config.toml:model(CCR托管块)")
    changes.append(c1)
    text, c2 = _set_comment_kv(text, "# CCR configured model", model, "CCR configured model 注释")
    changes.append(c2)
    return [(p, text, changes)]


def _pi_read() -> dict:
    sp = _home() / ".pi" / "agent" / "settings.json"
    mp = _home() / ".pi" / "agent" / "models.json"
    if not sp.exists():
        return {"current": "", "files": [], "note": "未找到 ~/.pi/agent/settings.json"}
    try:
        s = json.loads(_read_text(sp))
    except json.JSONDecodeError as e:
        return {"current": "", "files": [str(sp)], "note": f"settings.json 解析失败：{e}"}
    return {"current": str(s.get("defaultModel") or ""),
            "provider": str(s.get("defaultProvider") or ""),
            "files": [str(sp), str(mp) if mp.exists() else str(sp)]}


def _pi_write(model: str):
    sp = _home() / ".pi" / "agent" / "settings.json"
    mp = _home() / ".pi" / "agent" / "models.json"
    out = []
    s = json.loads(_read_text(sp))
    changes = []
    for k, v in (("defaultModel", model), ("defaultProvider", PI_CCR_PROVIDER)):
        old = str(s.get(k) or "")
        s[k] = v
        changes.append({"where": f"settings.json:{k}", "from": old, "to": v, "op": "set"})
    out.append((sp, json.dumps(s, ensure_ascii=False, indent=2) + "\n", changes))
    # 模型不在 ccr-free 清单里时补一条：pi 只认清单内的 id（实测 --model 传清单外 id 会报 unknown model）
    if mp.exists():
        m = json.loads(_read_text(mp))
        prov = (m.get("providers") or {}).get(PI_CCR_PROVIDER)
        ch2: List[dict] = []
        if prov is None:
            prov = {"api": "openai-completions", "baseUrl": "http://127.0.0.1:3456/v1",
                    "apiKey": _ccr_api_key(), "models": []}
            m.setdefault("providers", {})[PI_CCR_PROVIDER] = prov
            ch2.append({"where": f"models.json:providers.{PI_CCR_PROVIDER}", "from": "",
                        "to": "新建 provider（CCR :3456/v1）", "op": "add-section"})
        ids = [str(x.get("id") or "") for x in (prov.get("models") or [])]
        if model not in ids:
            prov.setdefault("models", []).append({"id": model, "name": model})
            ch2.append({"where": f"models.json:providers.{PI_CCR_PROVIDER}.models",
                        "from": f"{len(ids)} 条", "to": f"{len(ids) + 1} 条（+ {model}）", "op": "add-key"})
        if ch2:
            out.append((mp, json.dumps(m, ensure_ascii=False, indent=2) + "\n", ch2))
    return out


def _grok_existing_api_key(text: str) -> str:
    """取文件里**已指向 CCR** 的某个 [model.*] 块的 api_key（复用本机在用的那把）。"""
    sec_bases: Dict[str, str] = {}
    cur = ""
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
            continue
        if cur.startswith("model.") and s.startswith("base_url"):
            sec_bases[cur] = s.split("=", 1)[1].strip().strip('"')
    cur = ""
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
            continue
        if "3456" in sec_bases.get(cur, "") and s.startswith("api_key"):
            return s.split("=", 1)[1].strip().strip('"')
    return ""


def _grok_read() -> dict:
    p = _home() / ".grok" / "config.toml"
    if not p.exists():
        return {"current": "", "files": [], "note": "未找到 ~/.grok/config.toml"}
    text = _read_text(p)
    key = _toml_get(text, "models", "default")
    cur = _toml_get(text, f"model.{key}", "model") if key else ""
    return {"current": cur, "provider": key, "files": [str(p)]}


def _grok_write(model: str):
    p = _home() / ".grok" / "config.toml"
    text = _read_text(p)
    changes: List[dict] = []
    sec = f"model.{GROK_PROFILE_KEY}"
    # key 先复用文件里已有的 CCR 块（本机正在用的那把），再回落 CCR config.json
    existing = _toml_get(text, sec, "api_key") or ""
    if not existing:
        for ln in text.splitlines():
            s = ln.strip()
            if s.startswith("api_key") and "3456" in text[max(0, text.find(ln) - 400):text.find(ln)]:
                existing = s.split("=", 1)[1].strip().strip('"')
                break
    api_key = existing or _ccr_api_key()
    for k, v in (("model", model), ("base_url", "http://127.0.0.1:3456/v1"),
                 ("name", f"CCR {model}"), ("api_key", api_key),
                 ("api_backend", "chat_completions"), ("auth_scheme", "bearer")):
        text, c = _set_kv(text, k, v, section=sec)
        changes.append(c)
    text, c = _set_kv(text, "default", GROK_PROFILE_KEY, section="models")
    changes.append(c)
    return [(p, text, changes)]


def _hermes_read() -> dict:
    p = _home() / ".hermes" / "config.yaml"
    if not p.exists():
        return {"current": "", "files": [], "note": "未找到 ~/.hermes/config.yaml"}
    cur = prov = ""
    in_block = False
    for ln in _read_text(p).splitlines():
        if ln.rstrip() == "model:":
            in_block = True
            continue
        if in_block:
            if ln and not ln.startswith((" ", "\t")):
                break
            s = ln.strip()
            if s.startswith("default:"):
                cur = s.split(":", 1)[1].strip()
            elif s.startswith("provider:"):
                prov = s.split(":", 1)[1].strip()
    return {"current": cur, "provider": prov, "files": [str(p)]}


def _hermes_write(model: str):
    p = _home() / ".hermes" / "config.yaml"
    text = _read_text(p)
    lines = text.splitlines()
    lo = next((i for i, ln in enumerate(lines) if ln.rstrip() == "model:"), -1)
    if lo < 0:
        raise ModelCfgError("config.yaml 顶层没有 model: 块（手写配置已被改形，拒绝落笔）", 409)
    hi = len(lines)
    for j in range(lo + 1, len(lines)):
        if lines[j] and not lines[j].startswith((" ", "\t")):
            hi = j
            break
    body = "\n".join(lines[lo + 1:hi])
    changes: List[dict] = []
    for key, val in (("default", model), ("provider", HERMES_CCR_PROVIDER)):
        pat = re.compile(rf"^(\s*{key}:\s*)(\S.*)$", re.M)
        m = pat.search(body)
        if m:
            old = m.group(2).strip()
            body = pat.sub(lambda _m, v=val: f"{_m.group(1)}{v}", body, count=1)
            op = "set"
        else:
            old = ""
            body = body.rstrip("\n") + f"\n  {key}: {val}\n"
            op = "add-key"
        changes.append({"where": f"config.yaml:model.{key}", "from": old, "to": val, "op": op})
    new_lines = lines[:lo + 1] + body.split("\n") + lines[hi:]
    return [(p, "\n".join(new_lines) + "\n", changes)]


#: agent_id -> {name, read, write, argv}
SPECS: Dict[str, dict] = {
    "claude": {"name": "Claude Code", "read": _claude_read, "write": _claude_write},
    "jcode": {"name": "JCode", "read": _jcode_read, "write": _jcode_write},
    "codex": {"name": "Codex CLI", "read": _codex_read, "write": _codex_write},
    "pi": {"name": "Pi Agent", "read": _pi_read, "write": _pi_write},
    "grok": {"name": "Grok CLI", "read": _grok_read, "write": _grok_write},
    "hermes": {"name": "Hermes", "read": _hermes_read, "write": _hermes_write},
}

#: 在册但**不可统一设置**的 agent（理由必须写清，前端据此置灰）
UNSUPPORTED: Dict[str, str] = {
    "codebuddy": "WorkBuddy 包内 CLI：--model 只认自有清单（hy4-preview/hy3/…），与 CCR 的 provider/model ID 不通用",
    "qwenpaw": "纯 Web 型，无 CLI 与本机模型配置文件",
}


def validate_model(model: str) -> str:
    m = (model or "").strip()
    if not m:
        raise ModelCfgError("模型 ID 为空")
    if not MODEL_ID_RE.match(m):
        raise ModelCfgError("模型 ID 形状不合法（只允许字母数字与 . _ : / + -，≤120 字符）")
    return m


# ── Hub 侧 per-agent 模型（拉起终端时注入用）────────────────────────
def _ensure_table() -> None:
    db.execute("CREATE TABLE IF NOT EXISTS agent_models ("
               "agent_id TEXT PRIMARY KEY, model TEXT NOT NULL, updated_at TEXT NOT NULL)")


def hub_model(agent_id: str) -> str:
    try:
        _ensure_table()
        rows = db.query("SELECT model FROM agent_models WHERE agent_id=?", (agent_id,))
    except Exception:  # noqa: BLE001 —— 表没建好只影响注入，不许拖垮设置页
        return ""
    return str(rows[0]["model"]) if rows else ""


def set_hub_model(agent_id: str, model: str) -> None:
    _ensure_table()
    db.execute("INSERT INTO agent_models(agent_id, model, updated_at) VALUES(?,?,?) "
               "ON CONFLICT(agent_id) DO UPDATE SET model=excluded.model, updated_at=excluded.updated_at",
               (agent_id, model, _now()))


def chat_model(agent_id: str, requested: Optional[str] = None) -> str:
    """对话 / 任务通道（`POST /api/agents/{id}/chat`）的模型取值。

    v0.13.50：这条通道以前**压根不知道 hub 侧的持久化模型** —— adapter 的
    default_model 是**服务启动时**由 config 算出来的常量（.env 未配 CLAUDE_CHAT_MODEL
    就写死 "qwen3.8-flash"），于是设置页改了模型，协同子任务 / 定时任务 / 对话页
    照旧把旧模型发出去（09-28 实弹：POST /api/agents/claude/chat 打给 CCR 的仍是
    qwen3.8-flash，而 agent_models 里存的是 agnes/agnes-2.5-flash）。
    取值顺序：请求显式带 > 本 Agent 的持久化默认值 > adapter 自兜。"""
    req = (requested or "").strip()
    if req:
        return req
    return hub_model(agent_id)


def drift_report() -> List[dict]:
    """只读体检：列出「配置文件现值 ≠ hub 持久化值」的 agent（**不落笔**）。

    为什么要这个（09-28 实取证）：CCR 每次启动都会重写 ~/.claude/settings.json 的
    env.ANTHROPIC_MODEL / CCR_CLAUDE_CODE_MODEL / CODEXL_CLAUDE_CODE_MODEL 三兄弟
    （07:06:28 把 agnes 改回 alibaba/qwen3.8-flash[1m]，顶层 `model` 反而不动），
    ⇒ 凡是**不带 --model** 的 claude 启动（续聊、用户在别处直接敲 claude）重启后
    就退回旧模型 —— 这就是用户报的「重启之前是对的、重启之后又不对」。写回配置
    属共享配置写入（受保护面，须用户授权），这里先把漂移摆到明面上。"""
    out: List[dict] = []
    for aid in SPECS:
        want = hub_model(aid)
        if not want:
            continue
        try:
            st = SPECS[aid]["read"]()
        except ModelCfgError as e:
            out.append({"id": aid, "hub_model": want, "current": "",
                        "extra": {}, "files": [], "note": e.args[0]})
            continue
        owned = {"current": str(st.get("current") or "")}
        for k, v in (st.get("extra") or {}).items():
            owned[str(k)] = str(v)
        # 判据是「**任一**落点与持久化值不一致就算漂移」，而不是"还剩下某个落点对得上就算没事"：
        # CCR 重启只改 env 三兄弟、顶层 `model` 原样留着 —— 而真正决定这次调用用哪个模型的
        # 恰好是被改掉的 env。所以这里比对的是**全部**落点，并把不一致的那几个摆出来。
        bad = {k: v for k, v in owned.items() if v and v != want}
        if bad:
            out.append({"id": aid, "hub_model": want,
                        "current": str(st.get("current") or ""),
                        "extra": st.get("extra") or {}, "owned": bad,
                        "files": st.get("files") or []})
    return out


def repair_drift(only: Optional[List[str]] = None, dry_run: bool = False) -> dict:
    """把漂移的 agent 配置文件写回 hub 持久化值（用户 09-28 授权，默认开启）。

    为什么需要它：CCR 每次启动都会重写 ~/.claude/settings.json 的 env 三兄弟
    （今早 07:06:28 实测把 agnes 改回 qwen），凡是**不带 --model** 的启动就退回旧
    模型 —— 这就是「设置里是 Agens、重启之后又变 qwen」。终端注入只覆盖 hub 拉起的
    会话，用户在别处直接敲 claude 覆盖不到，所以要按持久化值把文件修回去。

    三条护栏（共享配置写入铁律）：
      ① 落笔前 `_backup()` 时间戳备份（apply_model 内自带，无法绕过）；
      ② 文件不存在 / 不可写 / 模型 ID 形状不合法 ⇒ **跳过并记原因**，绝不硬写；
      ③ `dry_run=True` 只算不做，给体检与自测用。
    返回 {"repaired": [...], "skipped": [...]}，每一项都带得出来证据的字段。"""
    out: dict = {"repaired": [], "skipped": []}
    for d in drift_report():
        aid = d["id"]
        if only is not None and aid not in only:
            continue
        want = d["hub_model"]
        files = d.get("files") or []
        if not files or not all(Path(f).exists() for f in files):
            out["skipped"].append({"id": aid, "reason": "配置文件缺失"})
            continue
        unwritable = [f for f in files if not os.access(f, os.W_OK)]
        if unwritable:
            out["skipped"].append({"id": aid, "reason": "文件不可写（常见 chattr +i）",
                                   "files": unwritable})
            continue
        try:
            validate_model(want)
        except ModelCfgError as e:
            out["skipped"].append({"id": aid, "reason": f"持久化模型 ID 不合法：{e.args[0]}"})
            continue
        if dry_run:
            out["repaired"].append({"id": aid, "model": want, "dry_run": True,
                                    "owned": d.get("owned") or {}})
            continue
        try:
            res = apply_model(aid, want)
        except ModelCfgError as e:
            out["skipped"].append({"id": aid, "reason": f"写回失败：{e.args[0]}"})
            continue
        out["repaired"].append({"id": aid, "model": want,
                                "backups": [a["backup"] for a in res["applied"]],
                                "owned": d.get("owned") or {}})
    return out


def terminal_argv(agent_id: str, model: str) -> List[str]:
    """拉起终端时追加的 argv；不在白名单或没设模型则返回 []（绝不猜 flag）。

    调用点两处（v0.13.51）：新会话与**续聊**都要加 —— 续聊命令 `claude --resume <id>`
    不带 --model 时用 agent 自己的配置文件，而那份文件会被 CCR 启动改写（见
    drift_report），等于"设置页改了、重启后照样退回旧模型"。追加位置固定在模板
    末尾，不动 `--resume <id>` 这类位置参数。"""
    flag = MODEL_ARGV.get(agent_id)
    if not flag or not model:
        return []
    return [flag, model]


# ── 对外：状态 / 预览 / 落笔 ────────────────────────────────────────
def agent_state(agent_id: str) -> dict:
    spec = SPECS.get(agent_id)
    if not spec:
        return {"id": agent_id, "supported": False, "writable": False,
                "reason": UNSUPPORTED.get(agent_id, "不在模型设置白名单内"),
                "current": "", "hub_model": hub_model(agent_id)}
    try:
        st = spec["read"]()
    except ModelCfgError as e:
        st = {"current": "", "files": [], "note": e.args[0]}
    files = st.get("files") or []
    return {"id": agent_id, "name": spec["name"], "supported": True,
            "writable": bool(files) and all(Path(f).exists() for f in files),
            "current": st.get("current", ""), "provider": st.get("provider", ""),
            "extra": st.get("extra", {}), "note": st.get("note", ""),
            "files": files, "argv": MODEL_ARGV.get(agent_id, ""),
            "hub_model": hub_model(agent_id)}


def list_agents() -> List[dict]:
    ids = list(SPECS.keys()) + list(UNSUPPORTED.keys())
    return [agent_state(i) for i in ids]


def ccr_router_view() -> dict:
    """CCR Router 五场景：只读（军规：不写网关配置）。"""
    p = _home() / ".claude-code-router" / "config.json"
    out = {"file": str(p), "router": {}, "fallback": {}, "read_only": True}
    if not p.exists():
        out["note"] = "未找到 ~/.claude-code-router/config.json"
        return out
    try:
        d = json.loads(_read_text(p))
    except json.JSONDecodeError as e:
        out["note"] = f"config.json 解析失败：{e}"
        return out
    out["router"] = {k: str(v) for k, v in (d.get("Router") or {}).items()}
    out["fallback"] = {k: list(v or []) for k, v in (d.get("fallback") or {}).items()}
    return out


def preview(agent_id: str, model: str) -> dict:
    """写前预览：只算 diff，落零字节。"""
    model = validate_model(model)
    spec = SPECS.get(agent_id)
    if not spec:
        raise ModelCfgError(UNSUPPORTED.get(agent_id, f"{agent_id} 不在模型设置白名单内"), 400)
    try:
        writes = spec["write"](model)
    except ModelCfgError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ModelCfgError(f"生成变更失败：{e}", 500) from e
    files = []
    for p, _new, changes in writes:
        files.append({"file": str(p), "exists": p.exists(),
                      # v0.13.44：预览就把"能不能写"摆出来（09-27 报障：pi 的
                      # settings.json 被 chattr +i 设了不可变，保存一路走到 500，
                      # 用户只看到一句 4 秒就消失的 toast ⇒ 观感「保存不生效」）。
                      "writable": os.access(p, os.W_OK),
                      "backup": str(p) + ".bak-<时间戳>-hub-modelcfg-" + agent_id,
                      "changes": [c for c in changes if c.get("op") != "skip"]})
    return {"agent_id": agent_id, "model": model,
            "hub_model_from": hub_model(agent_id), "hub_model_to": model,
            "argv": terminal_argv(agent_id, model), "files": files}


def apply_model(agent_id: str, model: str) -> dict:
    """落笔：先备份 → 再写 → 再存 Hub 侧。任一步失败即抛错（不半写）。"""
    model = validate_model(model)
    spec = SPECS.get(agent_id)
    if not spec:
        raise ModelCfgError(UNSUPPORTED.get(agent_id, f"{agent_id} 不在模型设置白名单内"), 400)
    writes = spec["write"](model)
    for p, _new, _c in writes:
        if not p.exists():
            raise ModelCfgError(f"配置文件不存在，拒绝新建：{p}", 404)
        # v0.13.44 预检：先问"能不能写"再落备份。顺序反过来的话（09-27 pi 事故面）
        # 会在不可写的文件旁留下一堆备份、再抛 500，用户既没改成也多出一堆垃圾。
        if not os.access(p, os.W_OK):
            raise ModelCfgError(
                f"配置文件不可写：{p}（常见原因是被设了不可变属性或只读挂载；"
                f"`lsattr {p}` 若见 i 标记，需 `chattr -i {p}` 后才能保存）", 409)
    backups = [_backup(p, agent_id) for p, _n, _c in writes]
    applied = []
    try:
        for (p, new, changes), bak in zip(writes, backups):
            p.write_text(new, encoding="utf-8")
            applied.append({"file": str(p), "backup": bak,
                            "changes": [c for c in changes if c.get("op") != "skip"]})
    except OSError as e:  # noqa: BLE001
        # 半写止损：把已写的从备份还原回去，不让配置文件停在半截状态
        for (p, _n, _c), bak in zip(writes, backups):
            try:
                shutil.copy2(bak, p)
            except OSError:
                pass
        raise ModelCfgError(f"写入失败已回滚：{e}", 500) from e
    set_hub_model(agent_id, model)
    return {"agent_id": agent_id, "model": model, "applied": applied,
            "argv": terminal_argv(agent_id, model), "at": _now()}


# ── HTTP 层 ────────────────────────────────────────────────────────
# 三个端点两个读一个写。写端点自带 HUB_PASSCODE 校验（与 /api/settings/term-token
# 同口径：口令错 401、服务端没配口令 503 —— "没设口令"不等于"不用口令"），
# 并在 writeauth.EXEMPT_PREFIXES 里登记理由，避免"先要 token 又要口令"的双重门。
from fastapi import APIRouter, HTTPException, Request  # noqa: E402
from pydantic import BaseModel  # noqa: E402

router = APIRouter(tags=["modelcfg"])


class ApplyIn(BaseModel):
    agent_id: str
    model: str
    passcode: str = ""


def _check_passcode(pc: str, request) -> None:
    want = os.getenv("HUB_PASSCODE", "")
    if not want:
        raise HTTPException(503, "未设置 HUB_PASSCODE，模型设置已停用（fail-closed）")
    if not hmac.compare_digest(pc or "", want):
        ip = request.client.host if request and request.client else "?"
        print(f"[settings] 模型设置口令错误：{ip}", flush=True)
        raise HTTPException(401, "口令错误")


def _guard(e: ModelCfgError) -> HTTPException:
    return HTTPException(e.status, e.args[0])


@router.get("/api/settings/models")
async def settings_models():
    """设置→模型子菜单的首屏数据：各 agent 现值 + CCR Router 只读视图 + 漂移体检。"""
    return {"agents": list_agents(), "ccr": ccr_router_view(), "drift": drift_report()}


@router.get("/api/settings/model/preview")
async def settings_model_preview(agent_id: str, model: str):
    try:
        return preview(agent_id, model)
    except ModelCfgError as e:
        raise _guard(e) from e


@router.post("/api/settings/model/apply")
async def settings_model_apply(body: ApplyIn, request: Request):
    _check_passcode(body.passcode, request)
    try:
        out = apply_model(body.agent_id, body.model)
    except ModelCfgError as e:
        raise _guard(e) from e
    print(f"[settings] 模型设置落笔：{body.agent_id} → {body.model}", flush=True)
    return out
