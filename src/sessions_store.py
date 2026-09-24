"""会话仓库适配层（v0.13.0）：只读把各 agent 自己在磁盘上的历史会话归一成 Item。

安全模型（依据 spec §2 的实测事实）：
- 只读：文件 open(..., errors='ignore')；sqlite 一律 "file:<p>?mode=ro" + uri=True。不写、不删、不 chmod。
- 标题＝用户问题原文（中文），过 mask_title() 遮蔽口令后外发；字母 id 一律不进标题。
- resume argv 由本模块模板拼装：客户端只能提供 session_id，必须过 id_re + 实盘存在双校验。
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

HOME = Path.home()
CACHE_TTL_S = 15          # 菜单 30s 轮询，15s 缓存足够去重
HEAD_CHARS = 262144       # 大 jsonl 只读头部这么多字符找标题（实测 grok 单是 <user_info> 一行就 33KB，64KB 窗口不够）
HARD_BUDGET_S = 1.5       # 单次 list_history 硬预算，超时返回已读到的 + note

UUID_RE = re.compile(r"\A[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z", re.I)
JCODE_RE = re.compile(r"\Asession_[a-z]+_\d{13}_[0-9a-f]{6,16}\Z")
HERMES_RE = re.compile(r"\A\d{8}_\d{6}_[0-9a-f]{6}\Z")
OPENCODE_RE = re.compile(r"\Ases_[0-9a-zA-Z]{6,64}\Z")   # 实测 1.18.32：ses_ + 22 位 base62
OPENCODE_DB = HOME / ".local" / "share" / "opencode" / "opencode.db"

# 实测：grok 把用户真问题包在 <user_query> 里（chat_history.jsonl 第 4 行）；
# 这几个前缀是注入块（拆不到 user_query 时必须跳过，否则标题会变成 system prompt）。
_UQ = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)
_INJECT_PREFIX = ("<user_info>", "<system-reminder>", "<command-name>", "<command-message>",
                  "<local-command", "<task-notification>", "<permissions")

# ── 口令遮蔽 ────────────────────────────────────────────────────────────────
# ① KV 形：允许 key 带前缀（实测本机 CCR 入口是 `?ccr_web_token=<值>`，\b 在 `_` 处不成立，
#    所以不能只写 \btoken\b）；② 28+ 位 base64/hex 裸串且含数字。
# 刻意**不**糊含 '-'/'_' 的长串：那会把 UUID、时间戳备份名一并糊掉（单测 test_uuid_title_not_mangled 锁死）。
_KV = re.compile(r"(?i)([A-Za-z0-9_\-]*(?:token|api[_-]?key|apikey|secret|passwd|password|authorization|bearer))"
                 r"\s*[=:]\s*[^\s,;'\"）)]+")
_BLOB = re.compile(r"\b(?=[A-Za-z0-9+/=]{28,}\b)[A-Za-z0-9+/=]*[0-9][A-Za-z0-9+/=]*\b")


def mask_title(text: Optional[str]) -> str:
    t = _KV.sub(lambda m: m.group(1) + "=<masked>", text or "")
    t = _BLOB.sub("<masked>", t)
    return re.sub(r"\s+", " ", t).strip()[:120]


def _iso(ts: Optional[str]) -> int:
    """ISO8601（grok/jcode 混用 秒/微秒/纳秒 + Z）→ epoch 秒；解析失败返回 0。"""
    if not ts:
        return 0
    s = str(ts).strip().replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)          # 纳秒截到微秒：fromisoformat 不吞 9 位小数
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:  # noqa: BLE001
        return 0


def _head_lines(p: Path, n: int = 400) -> List[dict]:
    """只读文件头 HEAD_CHARS，返回解析成功的 JSON 行（大 jsonl 不整读）"""
    try:
        with open(p, "r", errors="ignore") as f:
            blob = f.read(HEAD_CHARS)
    except Exception:  # noqa: BLE001
        return []
    out: List[dict] = []
    for line in blob.splitlines()[:n]:
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


def _load(p: Path):
    """读整份 JSON 并确保关句柄（单测跑出过 ResourceWarning：一次扫 60 个候选会漏 fd）"""
    with open(p, "r", errors="ignore") as f:
        return json.load(f)


def _text_of(c) -> str:
    """把 message content 拉成纯文本：实测三种形态——str（grok 顶层 / claude message）、
       [{'type':'text','text':…}] 块列表（jcode、部分 claude）、其它形状一律当无文本。"""
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        return " ".join(str(x.get("text", "")).strip() for x in c
                        if isinstance(x, dict) and x.get("text")).strip()
    return ""


def _first_user_text(objs: List[dict], *, jcode: bool = False) -> str:
    """取「用户第一条问题原文」——比 agent 自生成的摘要更合需求（D2）。
       实测三类干扰，逐个定策：
       ① grok：真问题被包在 `<user_query>…</user_query>` 里（实测第 4 行），必须先拆标签；
          而 `<user_info>` / `<system-reminder>` 是注入，拆不到 user_query 就跳过；
       ② claude/qoder：`<command-name>` 等标签内容是命令回显/meta，无 user_query ⇒ 跳过；
       ③ jcode：`display_role == 'system'` 是注入（不过滤就命中 system prompt）。
    """
    for d in objs:
        if jcode:
            if d.get("role") != "user" or d.get("display_role") == "system":
                continue
            s = _text_of(d.get("content"))
        else:
            if d.get("type") != "user" or d.get("isSidechain") or d.get("isMeta"):
                continue
            # 字段位置两家不同：grok 在顶层 content，claude/qoder 在 message.content
            raw = d.get("content") if d.get("content") is not None else (d.get("message") or {}).get("content")
            s = _text_of(raw)
        if not s:
            continue
        m = _UQ.search(s)
        if m:
            got = m.group(1).strip()
            if got:
                return got
            continue
        if s.startswith(_INJECT_PREFIX) or s.startswith("<"):
            continue
        return s
    return ""


# ── 各仓库适配器：统一返回 (items, note)。note 为中文，供前端空态/降级直显 ──────
def _t_grok(cwd: str, limit: int, t0: float) -> Tuple[List[dict], str]:
    """跳目录口径（用户 09-22 裁定）：不按画像 cwd 分桶，取全局时间最近的 limit 条。
       桶结构 ~/.grok/sessions/<URL编码cwd>/<uuid>/summary.json，实测 7 桶共 136 条。"""
    root = HOME / ".grok" / "sessions"
    if not root.is_dir():
        return [], "grok 无会话仓库"
    files = sorted(root.glob("*/*/summary.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return [], "grok 无历史会话"
    items: List[dict] = []
    for f in files:
        if time.time() - t0 > HARD_BUDGET_S:
            return items, "扫描超时，仅显示已读到的条目"
        try:
            d = _load(f)
        except Exception:  # noqa: BLE001
            continue
        sess = f.parent
        info = d.get("info") or {}
        # D2：标题＝用户问题原文优先；session_summary 是 agent 自生成的摘要，实测会出英文
        # （'Agent Hub sidebar menu name status badge layout fixes'），只能当兼底。
        title = _first_user_text(_head_lines(sess / "chat_history.jsonl", 20)) or \
            (d.get("session_summary") or "").strip()
        scwd = info.get("cwd") or unquote(sess.parent.name)      # 缺字段时从桶名反解
        items.append({"agent": "grok", "id": info.get("id") or sess.name,
                      "title": mask_title(title) or "未命名会话", "ts": _iso(d.get("updated_at")),
                      "msgs": d.get("num_messages"), "cwd": scwd})
        if len(items) >= limit:
            break
    return items, ""


def _cwd_of_records(objs: List[dict]) -> str:
    """claude/qoder 的 jsonl 每行都带 cwd（实测），取第一个字符串值即该会话的真实目录"""
    return next((d.get("cwd") for d in objs if isinstance(d.get("cwd"), str)), "")


def _t_jsonl_dir(kind: str, projects_dir: Path, cwd: str, limit: int, t0: float, qoder: bool = False) -> Tuple[List[dict], str]:
    """claude / qoder 共用：~/.<kind>/projects/<cwd 脱敏名>/<sid>.jsonl，**跳目录**按 mtime 取最近。"""
    if not projects_dir.is_dir():
        return [], f"{kind} 无会话仓库"
    files = sorted(projects_dir.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return [], f"{kind} 暂无可续会话（只有目录骨架）"
    items: List[dict] = []
    for p in files:
        if time.time() - t0 > HARD_BUDGET_S:
            return items, "扫描超时，仅显示已读到的条目"
        objs = _head_lines(p)
        title = ""
        if qoder:                       # qoder 实测有 last-prompt 行（取的就是用户自己那句）
            title = next((d.get("lastPrompt", "") for d in objs if d.get("type") == "last-prompt"), "")
        title = title or _first_user_text(objs)
        items.append({"agent": kind, "id": p.stem, "title": mask_title(title) or "未命名会话",
                      "ts": int(p.stat().st_mtime), "msgs": None, "cwd": _cwd_of_records(objs) or cwd})
        if len(items) >= limit:
            break
    return items, ""


def _t_claude(cwd: str, limit: int, t0: float):
    return _t_jsonl_dir("claude", HOME / ".claude" / "projects", cwd, limit, t0)


def _t_qoder(cwd: str, limit: int, t0: float):
    return _t_jsonl_dir("qoder", HOME / ".qoder" / "projects", cwd, limit, t0, qoder=True)


def _t_jcode(cwd: str, limit: int, t0: float) -> Tuple[List[dict], str]:
    """仓库以 ~/.jcode/sessions/*.json 为准（sqlite 的 recent_sessions 会被 prune，只作辅助）。
       .bak 文件名以 .json.bak 结尾，glob('session_*.json') 天然不会命中。
       跳目录口径（用户 09-22 裁定）：不按 working_dir 过滤，取全局 mtime 最近 limit 条。"""
    root = HOME / ".jcode" / "sessions"
    if not root.is_dir():
        return [], "jcode 无会话仓库"
    files = sorted(root.glob("session_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return [], "jcode 无会话仓库"
    items: List[dict] = []
    # 教训保留（v0.13.1）：此仓库是**平铺混所有 cwd** 的目录，绝不可「先取最新 N 个再按 cwd 过滤」
    # ——实测那样会让技术文档目录下 89 条只剩 1 条可见（被当日 10 条探针占满窗口）。
    for p in files:
        if time.time() - t0 > HARD_BUDGET_S:
            return items, "扫描超时，仅显示已读到的条目"
        try:
            d = _load(p)
        except Exception:  # noqa: BLE001
            continue
        msgs = d.get("messages") or []
        title = _first_user_text(msgs, jcode=True) or (d.get("title") or "").strip()   # D2：问题原文优先
        items.append({"agent": "jcode", "id": d.get("id") or p.stem, "title": mask_title(title) or "未命名会话",
                      "ts": _iso(d.get("last_active_at") or d.get("updated_at")),
                      "msgs": len(msgs) or None, "cwd": d.get("working_dir") or cwd})
        if len(items) >= limit:
            break
    return items, ""


def _ro(db: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _t_hermes(cwd: str, limit: int, t0: float) -> Tuple[List[dict], str]:
    """权威仓库是 state.db（~/.hermes/sessions/ 已于 08-05 停写）。152/209 条 cwd 为 NULL ⇒
       不按 cwd 过滤、按 source='cli' 全量（D7），并在 note 里写明口径。"""
    db = HOME / ".hermes" / "state.db"
    if not db.exists():
        return [], "hermes 无 state.db"
    sql = ("select s.id as id, s.title as title, s.last_activity_at as ts, s.cwd as scwd, "
           "(select m.content from messages m where m.session_id=s.id and m.role='user' and m.active=1 "
           " order by m.id limit 1) as first_u "
           "from sessions s where s.source='cli' order by s.last_activity_at desc limit ?")
    try:
        with _ro(db) as c:
            rows = c.execute(sql, (limit,)).fetchall()
    except Exception as e:  # noqa: BLE001
        return [], f"hermes 读取失败：{type(e).__name__}"
    items = [{"agent": "hermes", "id": r["id"],
              "title": mask_title(r["first_u"] or r["title"] or "") or "未命名会话",   # D2：问题原文优先，LLM 标题当兼底
              "ts": int(r["ts"] or 0), "msgs": None, "cwd": r["scwd"] or ""} for r in rows]
    return items, "口径：hermes 按 source=cli 全量跳目录（历史多数条目未记 cwd）"


def _t_codex(cwd: str, limit: int, t0: float) -> Tuple[List[dict], str]:
    """只列 source='cli'（D5，否则把 codex exec 探针当历史），但**跳目录**（用户 09-22 裁定）。
       实测 updated_at/created_at 为 epoch 秒；
       `has_user_event` 实测在唯一真会话上为 0 ⇒ 不可当过滤条件。"""
    db = HOME / ".codex" / "state_5.sqlite"
    if not db.exists():
        return [], "codex 无 state_5.sqlite"
    try:
        with _ro(db) as c:
            rows = c.execute("select id, title, cwd, updated_at from threads "
                             "where source='cli' and archived=0 order by updated_at desc limit ?",
                             (limit,)).fetchall()
    except Exception as e:  # noqa: BLE001
        return [], f"codex 读取失败：{type(e).__name__}"
    items = [{"agent": "codex", "id": r["id"], "title": mask_title(r["title"] or "") or "未命名会话",
              "ts": int(r["updated_at"] or 0), "msgs": None, "cwd": r["cwd"] or ""} for r in rows]
    return items, ("" if items else "codex 无交互式历史（exec 探针不计）")


def _t_opencode(cwd: str, limit: int, t0: float) -> Tuple[List[dict], str]:
    """opencode 1.x 的仓库是 SQLite（实测 1.18.32：~/.local/share/opencode/opencode.db）。
       排除子代理子会话（parent_id 非空）与归档（time_archived 非空）；
       exists(message) 挡掉零消息空会话——它们的标题是 "New session - <ISO>" 占位，无续聊价值。
       跳目录（09-22 裁定同样适用：条目可能来自任何工程，起 pty 用 session_cwd）。time_* 为毫秒。"""
    if not OPENCODE_DB.exists():
        return [], "opencode 无 opencode.db"
    sql = ("select s.id as id, s.title as title, s.directory as dir, s.time_updated as ts, "
           "(select json_extract(p.data,'$.text') from part p join message m on p.message_id = m.id "
           "  where m.session_id = s.id and json_extract(m.data,'$.role') = 'user' "
           "  and json_extract(p.data,'$.type') = 'text' "
           "  and length(json_extract(p.data,'$.text')) > 0 "
           "  order by p.time_created limit 1) as first_u "
           "from session s where (s.parent_id is null or s.parent_id='') "
           "and s.time_archived is null "
           "and exists (select 1 from message m where m.session_id = s.id) "
           "order by s.time_updated desc limit ?")
    try:
        with _ro(OPENCODE_DB) as c:
            rows = c.execute(sql, (limit,)).fetchall()
    except Exception as e:  # noqa: BLE001
        return [], f"opencode 读取失败：{type(e).__name__}"
    items = [{"agent": "opencode", "id": r["id"],
              "title": mask_title((r["title"] or "") if not (r["title"] or "").startswith("New session - ")
                                  else (r["first_u"] or r["title"] or "")) or "未命名会话",
              "ts": int(r["ts"] or 0) // 1000, "msgs": None, "cwd": r["dir"] or ""}
             for r in rows][:limit]
    return items, ("" if items else "opencode 无可续聊历史")


SESSION_STORES: Dict[str, dict] = {
    "grok":     {"kind": "grok_dir",       "id_re": UUID_RE,      "resume": ["grok", "--resume", "{id}"],             "fn": _t_grok},
    "claude":   {"kind": "claude_dir",     "id_re": UUID_RE,      "resume": ["claude", "--resume", "{id}"],           "fn": _t_claude},
    "qoder":    {"kind": "qoder_dir",      "id_re": UUID_RE,      "resume": ["qodercli", "-w", "{cwd}", "-r", "{id}"], "fn": _t_qoder},
    "jcode":    {"kind": "jcode_json",     "id_re": JCODE_RE,     "resume": ["jcode", "--resume", "{id}"],            "fn": _t_jcode},
    "hermes":   {"kind": "hermes_sqlite",  "id_re": HERMES_RE,    "resume": ["hermes", "--resume", "{id}"],           "fn": _t_hermes},
    "codex":    {"kind": "codex_sqlite",   "id_re": UUID_RE,      "resume": ["codex", "resume", "{id}"],              "fn": _t_codex},
    "opencode": {"kind": "opencode_sqlite", "id_re": OPENCODE_RE, "resume": ["opencode", "--session", "{id}"],        "fn": _t_opencode},
}

_CACHE: Dict[tuple, Tuple[float, dict]] = {}


def supports(agent_id: str) -> bool:
    return agent_id in SESSION_STORES


def list_history(agent_id: str, cwd: str, limit: int = 3) -> dict:
    st = SESSION_STORES.get(agent_id)
    if not st:
        return {"items": [], "cwd": cwd, "note": f"{agent_id} 无历史会话仓库"}
    limit = max(1, min(int(limit or 3), 20))
    key = (agent_id, cwd, limit)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    try:
        items, note = st["fn"](cwd, limit, time.time())
    except Exception as e:  # noqa: BLE001
        items, note = [], f"历史读取失败：{type(e).__name__}"
    items.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    out = {"items": items[:limit], "cwd": cwd, "note": note}
    _CACHE[key] = (time.time(), out)
    return out


def known_ids(agent_id: str, cwd: str) -> set:
    """只用于「当前可展示清单」的集合（受 list_history 的 limit≤20 上限制）。
       ⚠ 不要拿它做续聊存在性校验：第 21 条以后的老会话会被误判 404，走 _exists_on_disk()。"""
    return {i["id"] for i in list_history(agent_id, cwd, 20)["items"]}


def _sql_one(db: Path, sql: str, params: tuple):
    try:
        with _ro(db) as c:
            return c.execute(sql, params).fetchone()
    except Exception:  # noqa: BLE001
        return None


def _store_path(agent_id: str, sid: str):
    """按仓库结构定位「那一条」落在哪个文件（跳目录口径下 id 仍是全局唯一：实测各桶不撞名）"""
    if agent_id == "grok":
        return next(iter((HOME / ".grok" / "sessions").glob(f"*/{sid}/summary.json")), None)
    if agent_id in ("claude", "qoder"):
        base = HOME / (".claude" if agent_id == "claude" else ".qoder") / "projects"
        return next(iter(base.glob(f"*/{sid}.jsonl")), None)
    if agent_id == "jcode":
        f = HOME / ".jcode" / "sessions" / f"{sid}.json"
        return f if f.is_file() else None
    return None


def _exists_on_disk(agent_id: str, sid: str, cwd: str = "") -> bool:
    """续聊前的「实盘存在」硬校验——按仓库结构直接定位那一条，不看 mtime 排名。
       教训（v0.13.1）：早先用 known_ids() 校验，而它走 list_history(limit<=20)
       ⇒ 第 21 条以后的真会话会被误判「不在实盘清单」→ 404 续不了。"""
    if agent_id == "hermes":
        return bool(_sql_one(HOME / ".hermes" / "state.db",
                             "select 1 from sessions where id=? and source='cli'", (sid,)))
    if agent_id == "codex":
        return bool(_sql_one(HOME / ".codex" / "state_5.sqlite",
                             "select 1 from threads where id=? and source='cli' and archived=0", (sid,)))
    if agent_id == "opencode":
        return bool(_sql_one(OPENCODE_DB,
                             "select 1 from session where id=? and (parent_id is null or parent_id='')"
                             " and time_archived is null", (sid,)))
    return _store_path(agent_id, sid) is not None


def session_cwd(agent_id: str, sid: str, fallback: str = "") -> str:
    """会话自己的 cwd。跳目录之后必须按这一条来起 pty / 拼 qoder 的 -w，
       否则会「在技术文档目录里打开一条 agent-hub 的会话」。目录不存在则退回 fallback。"""
    c = ""
    try:
        f = _store_path(agent_id, sid)
        if agent_id == "grok" and f:
            info = _load(f).get("info") or {}
            c = info.get("cwd") or unquote(f.parent.parent.name)
        elif agent_id in ("claude", "qoder") and f:
            c = _cwd_of_records(_head_lines(f, 5))
        elif agent_id == "jcode" and f:
            c = _load(f).get("working_dir") or ""
        elif agent_id == "hermes":
            r = _sql_one(HOME / ".hermes" / "state.db", "select cwd from sessions where id=?", (sid,))
            c = (r["cwd"] or "") if r else ""
        elif agent_id == "codex":
            r = _sql_one(HOME / ".codex" / "state_5.sqlite", "select cwd from threads where id=?", (sid,))
            c = (r["cwd"] or "") if r else ""
        elif agent_id == "opencode":
            r = _sql_one(OPENCODE_DB, "select directory from session where id=?", (sid,))
            c = (r["directory"] or "") if r else ""
    except Exception:  # noqa: BLE001
        c = ""
    c = (c or "").strip()
    if c and Path(c).is_dir():
        return c
    return fallback


def resume_argv(agent_id: str, session_id: str, cwd: str) -> List[str]:
    st = SESSION_STORES.get(agent_id)
    if not st:
        raise ValueError(f"{agent_id} 不支持历史续聊")
    sid = str(session_id or "")
    if len(sid) > 128 or not st["id_re"].match(sid):
        raise ValueError("session_id 形状非法")
    if not _exists_on_disk(agent_id, sid, cwd):
        raise ValueError("session_id 不在实盘清单内")
    # {cwd} 用会话自己的目录（跳目录后不能用画像 cwd 硬套，否则 qoder 会开错工程）
    real_cwd = session_cwd(agent_id, sid, cwd)
    argv = [t.replace("{id}", sid).replace("{cwd}", real_cwd) for t in st["resume"]]
    for t in argv:                                  # 兜底栅栏：id 已过 ^…\Z，这里护住 cwd
        if any(c in t for c in "\x00\n;|&$`"):
            raise ValueError("拼装结果含可疑字符")
    return argv


# ── 活会话 pid → 中文标题（顶栏芯片去字母用；各 agent 登记表结构均实测）────────
def live_titles(agent_id: str) -> Dict[int, str]:
    out: Dict[int, str] = {}
    try:
        if agent_id == "grok":            # [{"session_id","pid","cwd","opened_at"}]
            for e in _load(HOME / ".grok" / "active_sessions.json"):
                t = _title_of_session("grok", e.get("session_id"))
                if e.get("pid") and t:
                    out[int(e["pid"])] = t
        elif agent_id == "claude":        # ~/.claude/sessions/<pid>.json
            for p in (HOME / ".claude" / "sessions").glob("[0-9]*.json"):
                try:
                    d = _load(p)
                except Exception:  # noqa: BLE001
                    continue
                t = _title_of_session("claude", d.get("sessionId"), d.get("cwd"))
                if d.get("pid") and t:
                    out[int(d["pid"])] = t
        elif agent_id == "hermes":        # {"entries":[{"pid","session_id",...}]}
            db = HOME / ".hermes" / "state.db"
            try:
                entries = _load(HOME / ".hermes" / "runtime" / "active_sessions.json").get("entries", [])
            except Exception:  # noqa: BLE001
                entries = []
            if db.exists():
                for e in entries:
                    if not (e.get("pid") and e.get("session_id")):
                        continue
                    try:
                        with _ro(db) as c:
                            r = c.execute("select title from sessions where id=?", (e["session_id"],)).fetchone()
                    except Exception:  # noqa: BLE001
                        continue
                    if r and r["title"]:
                        out[int(e["pid"])] = mask_title(r["title"])
        elif agent_id == "jcode":         # session json 的 last_pid（实测字段存在）
            for p in (HOME / ".jcode" / "sessions").glob("session_*.json"):
                try:
                    d = _load(p)
                except Exception:  # noqa: BLE001
                    continue
                t = _first_user_text(d.get("messages") or [], jcode=True) or (d.get("title") or "").strip()
                if d.get("last_pid") and t:
                    out[int(d["last_pid"])] = mask_title(t)
    except Exception:  # noqa: BLE001
        return out
    return out


def _title_of_session(agent: str, sid: Optional[str], cwd: Optional[str] = None) -> str:
    st = SESSION_STORES.get(agent or "")
    if not sid or not st or not st["id_re"].match(sid):   # id 会进 glob / SQL，先过形状门
        return ""
    if agent == "grok":
        hit = next(iter((HOME / ".grok" / "sessions").glob(f"*/{sid}/summary.json")), None)
        if not hit:
            return ""
        try:
            d = _load(hit)
        except Exception:  # noqa: BLE001
            return ""
        return mask_title(_first_user_text(_head_lines(hit.parent / "chat_history.jsonl", 20)) or
                          (d.get("session_summary") or "").strip())
    if agent == "claude":
        # id 是 UUID、跨桶唯一 ⇒ 不按 cwd 定位（跳目录口径下条目可能来自任何工程）
        f = next(iter((HOME / ".claude" / "projects").glob(f"*/{sid}.jsonl")), None)
        if f:
            return mask_title(_first_user_text(_head_lines(f, 60)))
    if agent == "jcode":
        f = HOME / ".jcode" / "sessions" / f"{sid}.json"
        if not f.exists():
            return ""
        try:
            d = _load(f)
        except Exception:  # noqa: BLE001
            return ""
        return mask_title(_first_user_text(d.get("messages") or [], jcode=True)
                          or (d.get("title") or "").strip())
    if agent == "hermes":
        try:
            with _ro(HOME / ".hermes" / "state.db") as c:
                r = c.execute("select s.title as t, (select m.content from messages m "
                              "where m.session_id=s.id and m.role='user' and m.active=1 "
                              "order by m.id limit 1) as u from sessions s where s.id=?", (sid,)).fetchone()
        except Exception:  # noqa: BLE001
            return ""
        return mask_title((r["u"] or r["t"]) if r else "")
    if agent == "codex":
        try:
            with _ro(HOME / ".codex" / "state_5.sqlite") as c:
                r = c.execute("select title from threads where id=?", (sid,)).fetchone()
        except Exception:  # noqa: BLE001
            return ""
        return mask_title(r["title"] if r else "")
    if agent == "opencode":
        try:
            with _ro(OPENCODE_DB) as c:
                r = c.execute("select s.title as t, "
                              "(select json_extract(p.data,'$.text') from part p join message m on p.message_id = m.id "
                              "  where m.session_id = s.id and json_extract(m.data,'$.role') = 'user' "
                              "  and json_extract(p.data,'$.type') = 'text' "
                              "  and length(json_extract(p.data,'$.text')) > 0 "
                              "  order by p.time_created limit 1) as u "
                              "from session s where s.id=?", (sid,)).fetchone()
        except Exception:  # noqa: BLE001
            return ""
        t = (r["t"] or "") if r else ""
        return mask_title(t if not t.startswith("New session - ") else ((r["u"] if r else "") or t))
    if agent == "qoder":
        hit = next(iter((HOME / ".qoder" / "projects").glob(f"*/{sid}.jsonl")), None)
        if not hit:
            return ""
        objs = _head_lines(hit, 400)
        p = next((d.get("lastPrompt", "") for d in objs if d.get("type") == "last-prompt"), "")
        return mask_title(p or _first_user_text(objs))
    return ""


def title_for(agent_id: str, session_id: str, cwd: str = "") -> str:
    """按 id 直查盘上标题。续聊会话本来就知道自己 resume 了哪条（`resume_of`），
       所以不需要依赖 pid 反查——实测 jcode/codex/qoder 的 pid 映射要么只在退出时写、
       要么根本不存在，只靠 live_titles() 会回退成 sid 前缀（正是本次要消除的东西）。"""
    return _title_of_session(agent_id, session_id, cwd)
