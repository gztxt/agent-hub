"""联邦记忆源适配器（v0.13.26 批1：把「本机所有 agent 记忆」接进检索联邦）

背景：hub 的记忆联邦此前只有 local(便签4行)+TDAI(权威库)两路；本机真实存在五处
有检索价值的记忆资产从未接入：claude-mem 观察库、pi/codex 会话 jsonl、工作区文件
记忆（MEMORY.md/memory/agent-knowledge/digest）、归档会话备份。本模块只做**只读检索**，
「收集整理分类」的呈现层在记忆中心页（批4），聚合检索走 memory.py 的 RRF。

安全模型（与 sessions_store.py 同源，一处不留例外）：
- 只读：sqlite 一律 `file:...?mode=ro` + uri=True；WAL 库打不开再试 immutable=1
  （只读快照，宁可读到旧一点的数据，也绝不要求写权限）；
- 脱敏：所有出站的 content/error 必须过 `tdai_client.scrub` + `sessions_store.mask_title`
  两道——rg 匹配行可能带 `token=...`、claude-mem 的 facts 可能带 URL 凭据；
- 预算：每源独立超时（默认 1.8s）+ rg 子进程 timeout，总并发 gather；
  失败不抛错、逐路报 `ok=False`（与 memory.py backends 诊断口径一致）；
- 禁止：启动任何服务、写任何外部库、`find` 全盘递归（用 rg --files / scandir 预算内计数）。

注册表是「整理分类」的骨架：每个源一个 id/label/kind/weight/desc，`fedsources` 端点
逐源报健康与计数，前端按注册表渲染源开关与健康卡。新增源=注册表加一条+一个搜索函数，
不碰 memory.py 的融合逻辑（开闭原则，防止「每加一路改一处融合」的散弹枪修改）。
"""
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import sessions_store
import tdai_client

from fastapi import APIRouter

router = APIRouter()

HOME = Path.home()
#: 工作区根（记忆/知识文件层的位置）。不走 env：这些目录是本机事实，不是部署参数。
WORKSPACE = Path("/fs/1000/ftp/技术文档")

#: rg 类源的单文件大小上限：会话备份里有大 jsonl，防止 rg 在超长行上耗时失控。
RG_MAX_FILESIZE = "20M"
#: rg_text 源每次检索的条目上限（文件级命中粒度）。
RG_ITEM_CAP = 30
#: 出站 content 截断长度：匹配行可能是一整条 jsonl 消息（实测 grok 单行 33KB）。
CONTENT_SNIPPET = 300
#: probe 计数缓存（秒）：源计数是「整理」数据不是实时数据，10 分钟足够新鲜。
PROBE_TTL_S = 600.0


@dataclass
class FedSource:
    id: str
    label: str
    kind: str                      # 'sqlite_fts' | 'rg_text' | 'disabled'
    weight: float                  # RRF 融合权重（权威度排序，见 memory.py 注释的量纲说明）
    desc: str
    timeout_s: float = 1.8
    enabled: bool = True


#: opencode 源：表结构具备（message/part/session）但 09-26 已判「会话写共享库、不合格
#: 作派发对象」；记忆检索上同样降级处理——登记不启用，等用户点名再开（禁顺手启用）。
REGISTRY: Dict[str, FedSource] = {
    "claude_mem": FedSource(
        id="claude_mem", label="claude-mem 观察库", kind="sqlite_fts", weight=0.8,
        desc="~/.claude-mem/claude-mem.db（observations FTS5 + session_summaries）",
        timeout_s=1.5),
    "pi_sessions": FedSource(
        id="pi_sessions", label="pi 会话记录", kind="rg_text", weight=0.5,
        desc="~/.pi/agent/sessions/**/*.jsonl（文件级命中）"),
    "codex_sessions": FedSource(
        id="codex_sessions", label="codex 会话记录", kind="rg_text", weight=0.5,
        desc="~/.codex/sessions/**/*.jsonl（文件级命中）"),
    "workspace_files": FedSource(
        id="workspace_files", label="工作区文件记忆", kind="rg_text", weight=0.7,
        desc=f"{WORKSPACE}/MEMORY.md + memory/ + agent-knowledge/ + digest/"),
    "archived_sessions": FedSource(
        id="archived_sessions", label="归档会话备份", kind="rg_text", weight=0.4,
        desc=f"{WORKSPACE}/会话备份/**（含 claude/pi/codex/grok/hermes 归档）",
        timeout_s=2.5),
    "opencode_sessions": FedSource(
        id="opencode_sessions", label="opencode 会话库", kind="disabled", weight=0.0,
        desc="~/.local/share/opencode/opencode.db；已判不合格作派发对象，登记不启用",
        enabled=False),
}

#: 各 rg_text 源的搜索根 + glob。列表顺序即 rg 扫描顺序（都很浅，无所谓优先级）。
_RG_TARGETS: Dict[str, tuple] = {
    "pi_sessions": ([str(HOME / ".pi" / "agent" / "sessions")], "*.jsonl"),
    "codex_sessions": ([str(HOME / ".codex" / "sessions")], "*.jsonl"),
    "workspace_files": ([str(WORKSPACE / "MEMORY.md"),
                         str(WORKSPACE / "memory"),
                         str(WORKSPACE / "agent-knowledge"),
                         str(WORKSPACE / "digest")], "*.md"),
    "archived_sessions": ([str(WORKSPACE / "会话备份")], None),   # 无 glob：目录内 md/jsonl/txt 全扫
}

_CLAUDE_MEM_DB = HOME / ".claude-mem" / "claude-mem.db"


def enabled_ids() -> List[str]:
    """可参与联邦的源 id 列表（memory.py 的 SOURCE_WHITELIST 由此动态生成）。"""
    return [s.id for s in REGISTRY.values() if s.enabled]


def _sanitize_content(text: str) -> str:
    """出站双道脱敏 + 截断。顺序：scrub（凭据模式）→ mask_title（token=KV）→ 截断。

    为什么两道都要：scrub 管 `Authorization: Bearer xxx` 这类成套凭据，
    mask_title 管 `?ccr_web_token=xxx` 这类 KV 形（sessions_store 实测口径），
    两者模式集不完全重合，少一道就留一条漏。
    """
    s = tdai_client.scrub(text or "")
    s = sessions_store.mask_title(s)
    return s[:CONTENT_SNIPPET]


def _fts_term(q: str) -> str:
    """用户词 → FTS5 安全 MATCH 项：整个包成带引号短语，内部引号剔除。

    为什么不裸传：`q=a-b OR c` 会被 FTS5 当表达式解析，轻则语法错（ok=False 假降级），
    重则被拆词后命中膨胀。短语包裹后语义=字面包含，与 rg -F 字面匹配对齐。
    """
    inner = re.sub(r'["\s]+', " ", q or "").strip()
    if not inner:
        return '""'
    return f'"{inner}"'


# ── sqlite_fts 源：claude-mem ──────────────────────────────────────────

def _ro_connect(db_path: Path, timeout: float = 1.0) -> sqlite3.Connection:
    """只读连接。WAL 库（.db-wal 存在）用普通 ro 打不开时退 immutable=1 只读快照。

    immutable=1 的语义是「我保证这个库不会被并发写」——对本机 claude-mem（低频写）
    代价是可能读到稍旧数据；收益是**绝不要求写权限、绝不参与 WAL 恢复**（军规：
    只读检索不得把外部库弄脏）。两档都失败才报错。
    """
    try:
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=timeout)
    except sqlite3.OperationalError:
        return sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=timeout)


def _search_claude_mem(q: str, limit: int) -> Dict:
    t0 = time.monotonic()
    if not _CLAUDE_MEM_DB.is_file():
        return {"ok": False, "count": 0, "items": [], "error": "claude-mem.db 不存在"}
    items: List[dict] = []
    term = _fts_term(q)
    try:
        con = _ro_connect(_CLAUDE_MEM_DB)
        try:
            for r in con.execute(
                "SELECT o.id, o.title, o.subtitle, o.facts, o.type, o.created_at "
                "FROM observations_fts f JOIN observations o ON o.id = f.rowid "
                "WHERE observations_fts MATCH ? ORDER BY o.created_at DESC LIMIT ?",
                (term, limit)):
                items.append({
                    "source": "claude_mem", "id": f"obs:{r[0]}",
                    "title": _sanitize_content(str(r[1] or r[2] or ""))[:120],
                    "content": _sanitize_content(f"{r[1] or ''}｜{r[2] or ''}｜{(r[3] or '')[:400]}"),
                    "category": f"observation/{r[4] or 'discovery'}",
                    "updated_at": r[5],
                })
            if len(items) < limit:
                for r in con.execute(
                    "SELECT s.id, s.request, s.investigated, s.learned, s.created_at "
                    "FROM session_summaries_fts f JOIN session_summaries s ON s.id = f.rowid "
                    "WHERE session_summaries_fts MATCH ? LIMIT ?",
                    (term, limit - len(items))):
                    items.append({
                        "source": "claude_mem", "id": f"sum:{r[0]}",
                        "title": _sanitize_content(str(r[1] or ""))[:120],
                        "content": _sanitize_content(f"{r[1] or ''}｜{r[2] or ''}｜{r[3] or ''}"),
                        "category": "observation/summary",
                        "updated_at": r[4],
                    })
        finally:
            con.close()
        return {"ok": True, "count": len(items), "items": items,
                "ms": round((time.monotonic() - t0) * 1000, 1)}
    except Exception as e:  # noqa: BLE001 —— 逐路报错，不炸整条联邦链
        return {"ok": False, "count": 0, "items": [],
                "error": tdai_client.scrub(f"{type(e).__name__}: {e}")[:160],
                "ms": round((time.monotonic() - t0) * 1000, 1)}


# ── rg_text 源：pi/codex/工作区/归档 ───────────────────────────────────

def _rg_lines(paths: List[str], glob: Optional[str], q: str,
              cap: int, timeout: float) -> List[str]:
    """一次 rg 子进程拿「文件级命中 + 首个匹配行」。`-m1` 每文件一行、`-l` 不够用
    （拿不到行内容做摘要）、`--max-filesize` 拒超大 jsonl。行输出格式 `path:line:content`。

    用 `-F`（字面匹配）而不是正则：用户查询词里的 `.` `/` `(` 在正则里全是元字符，
    语义漂移还可能炸 pattern 语法错。字面包含也是与 claude-mem FTS 短语一致的口径。
    `--no-ignore --hidden` 必须显式：会话备份/ 在技术文档 .gitignore 里（实测 123 行），
    记忆资产是数据不是 git 工作树对象，尊重 ignore 会系统性漏掉 99.96% 内容
    （实测 2/5350 个文件——verify 闸门 B3 红向抓的就是这个）。\.codex 同样中招。
    """
    cmd = ["rg", "-F", "-i", "-n", "-m", "1", "--no-heading",
           "--no-ignore", "--hidden", "--max-filesize", RG_MAX_FILESIZE]
    if glob:
        cmd += ["-g", glob]
    cmd += ["--", q] + [p for p in paths if os.path.exists(p)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"rg 超时（{timeout}s）")
    if r.returncode not in (0, 1):    # 1=无命中是正常态，不是错误
        raise RuntimeError(f"rg rc={r.returncode}: {r.stderr[:80]}")
    return [ln for ln in (r.stdout or "").splitlines() if ln.strip()][:cap]


def _rg_item(source_id: str, line: str) -> Optional[dict]:
    """`path:line:content` → 统一 item。path 用绝对路径（前端直接可溯源），行号做 id。"""
    m = re.match(r"^(.*?):(\d+):(.*)$", line, re.S)
    if not m:
        return None
    path, lineno, content = m.group(1), int(m.group(2)), m.group(3)
    name = os.path.basename(path)
    return {
        "source": source_id, "id": f"{path}:{lineno}",
        "title": _sanitize_content(name)[:120],
        "content": _sanitize_content(content),
        "category": "session" if source_id.endswith("sessions") else "knowledge",
        "updated_at": None,
    }


def _search_rg_text(source_id: str, q: str, limit: int, timeout: float) -> Dict:
    t0 = time.monotonic()
    paths, glob = _RG_TARGETS[source_id]
    if not any(os.path.exists(p) for p in paths):
        return {"ok": False, "count": 0, "items": [], "error": "搜索根不存在"}
    try:
        lines = _rg_lines(paths, glob, q, min(limit, RG_ITEM_CAP), timeout)
        items = [it for it in (_rg_item(source_id, ln) for ln in lines) if it]
        return {"ok": True, "count": len(items), "items": items,
                "ms": round((time.monotonic() - t0) * 1000, 1)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "count": 0, "items": [],
                "error": tdai_client.scrub(f"{type(e).__name__}: {e}")[:160],
                "ms": round((time.monotonic() - t0) * 1000, 1)}


# ── probe（健康 + 计数，带 TTL 缓存） ─────────────────────────────────

_probe_cache: Dict[str, tuple] = {}      # {sid: (monotonic_ts, result)}


def _probe_one(source_id: str) -> Dict:
    """源健康探针：返回 {ok, count, note, ms}。count=可检索条目/文件数（估计值）。

    rg 类计数用 `rg --files`（比 find 快且天然吃 -g）；claude_mem 用 COUNT(*)。
    计数超时不算源挂（ok 仍 True，note 注明）——「整理」数据缺失不该拖垮「检索」判定。
    """
    t0 = time.monotonic()
    src = REGISTRY[source_id]
    if source_id == "claude_mem":
        if not _CLAUDE_MEM_DB.is_file():
            return {"ok": False, "count": 0, "note": "claude-mem.db 不存在",
                    "ms": round((time.monotonic() - t0) * 1000, 1)}
        try:
            con = _ro_connect(_CLAUDE_MEM_DB, timeout=1.0)
            try:
                n = con.execute("SELECT (SELECT COUNT(*) FROM observations) "
                                "+ (SELECT COUNT(*) FROM session_summaries)").fetchone()[0]
            finally:
                con.close()
            return {"ok": True, "count": int(n), "note": "observations+summaries",
                    "ms": round((time.monotonic() - t0) * 1000, 1)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "count": 0,
                    "note": tdai_client.scrub(f"{type(e).__name__}")[:80],
                    "ms": round((time.monotonic() - t0) * 1000, 1)}
    paths, glob = _RG_TARGETS.get(source_id, ([], None))
    alive = [p for p in paths if os.path.exists(p)]
    if not alive:
        return {"ok": False, "count": 0, "note": "路径不存在",
                "ms": round((time.monotonic() - t0) * 1000, 1)}
    cmd = ["rg", "--files", "--no-ignore", "--hidden",
           "--max-filesize", RG_MAX_FILESIZE]
    if glob:
        cmd += ["-g", glob]
    cmd += alive
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                           timeout=src.timeout_s)
        n = len([ln for ln in (r.stdout or "").splitlines() if ln.strip()])
        return {"ok": True, "count": n, "note": f"{len(alive)}/{len(paths)} 根存在",
                "ms": round((time.monotonic() - t0) * 1000, 1)}
    except subprocess.TimeoutExpired:
        return {"ok": True, "count": 0, "note": "计数超时（检索不受影响）",
                "ms": round((time.monotonic() - t0) * 1000, 1)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "count": 0, "note": type(e).__name__,
                "ms": round((time.monotonic() - t0) * 1000, 1)}


# ── 对外入口：并发检索 / 源清单 ───────────────────────────────────────

async def search_fed(q: str, limit: int, want: set) -> Dict[str, Dict]:
    """并发调各启用源。返回 {source_id: result}；调用方（memory.py）把 ok 的进 RRF。

    同步实现 + asyncio.to_thread 并发：rg/sqlite 都是阻塞 IO，事件循环不能被卡死。
    每源超时由各自实现承担（rg subprocess timeout / sqlite connect timeout），
    这里不再叠一层 wait_for——两层超时会让超时原因变模糊（是 rg 慢还是 gather 慢）。
    """
    tasks = {}
    for sid in want:
        src = REGISTRY.get(sid)
        if not src or not src.enabled:
            continue
        if src.kind == "sqlite_fts":
            tasks[sid] = asyncio.to_thread(_search_claude_mem, q, limit)
        elif src.kind == "rg_text":
            tasks[sid] = asyncio.to_thread(_search_rg_text, sid, q, limit, src.timeout_s)
    if not tasks:
        return {}
    keys = list(tasks.keys())
    results = await asyncio.gather(*[tasks[k] for k in keys], return_exceptions=True)
    out: Dict[str, Dict] = {}
    for k, r in zip(keys, results):
        if isinstance(r, BaseException):
            out[k] = {"ok": False, "count": 0, "items": [],
                      "error": tdai_client.scrub(f"{type(r).__name__}: {r}")[:160]}
        else:
            out[k] = r
    return out


@router.get("/api/memory/fedsources")
async def list_fed_sources():
    """联邦源清单 + 逐源健康/计数（前端「整理分类」面板的数据源）。probe 带 TTL 缓存。"""
    now = time.monotonic()
    fresh: Dict[str, Dict] = {}
    stale: List[str] = []
    for sid, src in REGISTRY.items():
        hit = _probe_cache.get(sid)
        if hit and (now - hit[0]) < PROBE_TTL_S:
            fresh[sid] = hit[1]
        else:
            stale.append(sid)
    if stale:
        probed = await asyncio.gather(
            *[asyncio.to_thread(_probe_one, sid) for sid in stale],
            return_exceptions=True)
        for sid, r in zip(stale, probed):
            if isinstance(r, BaseException):
                r = {"ok": False, "count": 0, "note": type(r).__name__, "ms": None}
            _probe_cache[sid] = (now, r)
            fresh[sid] = r
    sources = []
    for sid, src in REGISTRY.items():
        p = fresh.get(sid) or {}
        sources.append({
            "id": sid, "label": src.label, "kind": src.kind, "enabled": src.enabled,
            "weight": src.weight, "desc": src.desc,
            "probe": {"ok": bool(p.get("ok")), "count": p.get("count"),
                      "note": p.get("note"), "ms": p.get("ms")},
        })
    return {"sources": sources, "count": len(sources),
            "enabled": [s["id"] for s in sources if s["enabled"]]}


def fed_weight(sid: str) -> float:
    return REGISTRY[sid].weight
