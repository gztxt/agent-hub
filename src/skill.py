"""技能门面 /api/skill/* —— 只读列出/读取本机磁盘上**真实在用**的 SKILL.md，并对照 TDAI 注册表。

【为什么不建 hub 自己的技能表】
与 `kb.py` 同一口径：hub 只做**门面 + 归并 + 审计**，一个字都不存。技能的权威副本就是
磁盘上那 37 个 `SKILL.md`（pi/claude 直接读它们），在 hub 里建一张 `skills` 表就是
**第二权威副本**——它会腐烂、会与源头不一致，正是 0924 方案被否决的那条路
（见 /fs/1000/ftp/技术文档/PENDING-TASKS.md PT-20260924-08：「本机记忆/技能/文档索引/嵌入
四项权威实现均已存在，hub 只当门面」）。所以本模块**只读、不写、不缓存副本**，
每次请求都重新走一遍磁盘。37 个文件、总量 ~230KB，实测扫描是毫秒级，不需要缓存。

【磁盘侧的四条实测事实（2026-09-24 13:0x，Python 3.11.2，本会话亲自取证）】
① **`Path.rglob()` 会漏报**：`/home/gztxt/.pi/agent/skills/agent-dispatch` 是**软链目录**
   （→ `/fs/1000/ftp/技术文档/skills/agent-dispatch`），`rglob` 默认不跟随软链 ⇒ 只数到 37；
   `os.walk(followlinks=True)` 数到 **38**。所以本模块一律用 `os.walk(..., followlinks=True)`。
② **38 ≠ 37**：多出来那条就是上面的软链，同一 realpath 在 `pi` 与 `techdocs` 两路各出现一次。
   ⇒ 必须**按 realpath 去重**，但**保留别名**（`aliases[]`），否则用户看不到"pi 路是软链"这个事实。
   规范条目取 `path == realpath` 的那一份（真实文件所在路），软链路只当别名。
③ **3 个文件没有 frontmatter**：`claude/agent-ecosystem`、`claude/system-memory`、
   `claude/system-rules`（都是 800~930B 的裸正文）。⇒ 解析器必须容错：`fm=false`、
   `name` 回退成目录名，**绝不因为解析不出 frontmatter 就把整条丢掉**（丢条目＝另一种静默）。
④ **值形态不统一**：`claude/caveman` 写的是 `name: "caveman"`（带引号），最大文件
   `superpowers/subagent-driven-development` 28077B。⇒ 去引号 + 读取上限保护。

【TDAI 注册表这一路】
`tdai_client.skill_list()` 打 `POST /v3/skill/list`，实测 **HTTP 200 但 0 行**。
本门面把它**如实并列报出**（`ok=true, count=0`），不做 ETL、不灌数据——
「技能权威源到底是磁盘还是 TDAI 注册表」是 PT-20260924-08 的待裁项 **T2-1**，未裁前不动。
口径照抄 `kb.py` 对 wigolo 的处理：**禁止用字段缺失表达"不接/为空"**，必须给出
`available` + `why`/`note`，让面板能一眼看出"注册表是空的"而不是"hub 没查"。

【失败表态纪律（承接 P0/P1）】
每路必回 `backends[] = {name, ok, count, ms, error}`（5 键，与 `kb.py:_backend` 同形）；
任何一路挂了都要说得出「挂的是哪路、为什么挂、这次少了什么」。
**禁止 `except: pass`、禁止静默返回空**。所有对外文本（含 `error`、`content`）一律过
`tdai_client.scrub`——技能正文是**凭据高危面**（技能里写 curl 带 key 是本机常见形态），
脱敏只能有一个实现（见 `memory.py:79-81` 与 `tdai_client.scrub` 的注释）。
"""
import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

import db
import runlog
import tdai_client
import writeauth

log = logging.getLogger("hub.skill")
router = APIRouter(prefix="/api/skill", tags=["skill"])

# ── 技能目录白名单（可用 SKILL_DIRS_JSON 整体覆盖；测试靠猴补本字典把根指到 tmp）──
_DEFAULT_DIRS: Dict[str, str] = {
    "claude": "/home/gztxt/.claude/skills",
    "pi": "/home/gztxt/.pi/agent/skills",
    "techdocs": "/fs/1000/ftp/技术文档/skills",
    "superpowers": "/home/gztxt/.pi/agent/git/github.com/obra/superpowers/skills",
    # v0.13.26 批2：56 号文档实测的另外三个技能发现点。~/.agents/skills 是
    # codex 等共享发现点（39 项，含与 claude/superpowers 重名的软链）；
    # ~/.codex/skills 与 ~/.workbuddy/skills 是各自工具链的私有发现点。
    # _dedup 按 realpath 合并同源条目（软链只当别名列），不会重复计数。
    "agents": "/home/gztxt/.agents/skills",
    "codex": "/home/gztxt/.codex/skills",
    "workbuddy": "/home/gztxt/.workbuddy/skills",
}


def _load_dirs() -> Dict[str, str]:
    raw = os.getenv("SKILL_DIRS_JSON")
    if not raw:
        return dict(_DEFAULT_DIRS)
    try:
        d = json.loads(raw)
    except ValueError as e:
        # 不静默回退：日志里必须留下"我忽略了你的覆盖"，否则运维会以为覆盖生效了
        log.warning("SKILL_DIRS_JSON 不是合法 JSON（%s），已忽略，用内置默认目录", e)
        return dict(_DEFAULT_DIRS)
    if not (isinstance(d, dict) and d
            and all(isinstance(k, str) and isinstance(v, str) for k, v in d.items())):
        log.warning("SKILL_DIRS_JSON 形状不对（要 {route: path} 且全字符串），已忽略")
        return dict(_DEFAULT_DIRS)
    return d


SKILL_DIRS: Dict[str, str] = _load_dirs()

TDAI_ROUTE = "tdai"
SCAN_TIMEOUT_S = float(os.getenv("SKILL_SCAN_TIMEOUT", "2.0"))     # 38 个文件实测毫秒级，2s 已极宽
READ_MAX_BYTES = int(os.getenv("SKILL_READ_MAX_BYTES", str(256 * 1024)))  # 实测最大 28077B
SKIP_DIR_NAMES = frozenset({".git", "node_modules", "__pycache__", ".venv"})
_INFO_TTL_S = float(os.getenv("SKILL_STATUS_TTL", "60"))
_STATUS_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}


def disk_routes() -> Tuple[str, ...]:
    """磁盘路名单。**每次调用时现算**，不在 import 期固化成常量——
    否则闸门/测试猴补 `SKILL_DIRS` 之后，白名单还是旧的，400 断言会假绿。
    （`kb.py` 的 `ROUTES` 是静态的因为它那三路不含可变目录；这里含。）"""
    return tuple(SKILL_DIRS)


def all_routes() -> Tuple[str, ...]:
    return disk_routes() + (TDAI_ROUTE,)


# ── frontmatter 解析（容错，见模块 docstring 事实③④）──────────────────────
_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)$")
_FOLDED = {"|", ">", "|-", ">-", "|+", ">+", "|-", ">-"}


def parse_frontmatter(text: str) -> Tuple[Optional[Dict[str, str]], str]:
    """返回 (frontmatter 字典 或 None, 正文)。

    刻意容错而不是抛异常：37 个文件里有 3 个根本没有 frontmatter，解析失败的正确表态是
    `fm=false` + 名字回退目录名，**不是把这条技能从清单里抹掉**（那就是门面自己造静默）。
    支持：CRLF、引号包裹的值、`|`/`>` 折叠块标量（收后续更深缩进的行）、前导 BOM。
    """
    # BOM 必须先剥：utf-8 解码会把 BOM 留成正文首字符，于是首行不再是 `---`，
    # frontmatter 就被判成「没有」—— 元数据静默丢失，正是本项目最禁的那种失败形态。
    # 本机今天 0/38 带 BOM（实测），所以这是潜伏面而非活故障；修它是为了不给未来留静默。
    text = text.lstrip("\ufeff")
    m = _FM_RE.match(text)
    if not m:
        return None, text
    block, body = m.group(1), text[m.end():]
    fm: Dict[str, str] = {}
    lines = block.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip("\r")
        km = _KEY_RE.match(ln)
        if not km:
            i += 1
            continue
        key, val = km.group(1), km.group(2).strip()
        i += 1
        if val in _FOLDED:                      # 折叠/字面块标量：正文在后续缩进行里
            buf: List[str] = []
            while i < len(lines):
                nxt = lines[i].rstrip("\r")
                if nxt.strip() and not nxt.startswith((" ", "\t")):
                    break
                buf.append(nxt.strip())
                i += 1
            val = " ".join(x for x in buf if x).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]                     # 实测存在 name: "caveman" 这种写法
        fm[key] = val
    return (fm or None), body


def _read_text(path: str) -> Tuple[str, str]:
    """读文件；返回 (文本, 编码说明)。非 UTF-8 不抛、不丢，用 replace 兜住并**如实标注**——
    口径同 `sessions_store.py`：只读、errors 兜底、绝不写/删/chmod。"""
    with open(path, "rb") as f:                 # 显式关闭：`open().read()` 这种写法在 unittest 下
        raw = f.read()                          # 会刷 ResourceWarning（实测一次跑刷 45 条）；
                                                # noqa 只压得住 linter，压不住解释器
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace"), "utf-8(replace：含非 UTF-8 字节)"


def _allowed_roots() -> List[str]:
    """白名单根的 realpath。**每次调用现算**（与 disk_routes() 同理：猴补 SKILL_DIRS 后必须跟着变）。"""
    return [os.path.realpath(d) for d in SKILL_DIRS.values() if d and os.path.isdir(d)]


def _inside(rp: str, allowed: List[str]) -> bool:
    """realpath 是否落在白名单根之内。用 realpath 而不是 normpath：
    技能目录里本来就允许软链（事实②，pi 路经软链指到 techdocs），
    所以判据必须是「解完软链后还在白名单里」，而不是「字面路径看着像」。"""
    return any(rp == a or rp.startswith(a + os.sep) for a in allowed)


def _scan_one(route: str, root: str) -> Dict[str, Any]:
    """扫一个技能目录。同步实现（放子线程跑）。任何失败都变成结构化 error，不抛。"""
    t0 = time.perf_counter()

    def ms() -> float:
        return round((time.perf_counter() - t0) * 1000, 1)

    if not root:
        return {"ok": False, "items": [], "ms": ms(), "error": "该路未配置目录（SKILL_DIRS 里是空串）"}
    if not os.path.isdir(root):
        return {"ok": False, "items": [], "ms": ms(),
                "error": tdai_client.scrub(f"目录不存在或不可读：{root}")[:200]}

    items: List[Dict[str, Any]] = []
    errs: List[str] = []
    skipped: List[Dict[str, Any]] = []
    allowed = _allowed_roots()
    try:
        for cur, dirs, files in os.walk(root, followlinks=True):   # ← followlinks 是关键，见事实①
            dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
            if "SKILL.md" not in files:
                continue
            p = os.path.join(cur, "SKILL.md")
            rp = os.path.realpath(p)
            # 越界判定必须在**读之前**：解析 frontmatter 需要读文件内容，一个指向
            # /etc/passwd 的软链 SKILL.md 会让门面读到白名单外的文件（/list 虽不回正文，
            # 但「读」这个动作已经发生）。拒读的条目如实列出，不静默丢——
            # 「没看见」与「看见但拒读」是两件必须能区分的事。
            if not _inside(rp, allowed):
                skipped.append({"dir": os.path.basename(cur), "path": tdai_client.scrub(p),
                                "realpath": tdai_client.scrub(rp),
                                "why": "realpath 落在技能目录白名单外，拒读（防软链越界）"})
                continue
            try:
                text, enc = _read_text(p)
                st = os.stat(p)
            except OSError as e:
                errs.append(f"{tdai_client.scrub(p)}: {type(e).__name__}")
                continue
            fm, _body = parse_frontmatter(text)
            dirname = os.path.basename(cur)
            items.append({
                "name": tdai_client.scrub((fm or {}).get("name") or dirname),
                "description": tdai_client.scrub((fm or {}).get("description") or ""),
                "route": route,
                "path": p,
                "realpath": rp,
                # path != realpath ⇒ 这一份是经软链到达的（事实②的去重依据）
                "via_symlink": p != rp,
                "fm": fm is not None,            # false 也要出现在清单里（事实③）
                "fm_keys": sorted(fm) if fm else [],
                "encoding": enc,
                "bytes": st.st_size,
                "mtime": round(st.st_mtime, 1),
                "source": "disk",
            })
    except OSError as e:                          # 整个根走不下去（权限/断链）
        return {"ok": False, "items": items, "ms": ms(), "skipped_outside": skipped,
                "error": tdai_client.scrub(f"{type(e).__name__}: {e}")[:200]}

    err = None
    if errs:
        # 部分文件读不了 ≠ 整路失败：路仍报 ok，但把读不了的文件逐条说出来（不许静默少条目）
        err = tdai_client.scrub("部分文件读取失败：" + "; ".join(errs))[:400]
    return {"ok": True, "items": items, "ms": ms(), "error": err, "skipped_outside": skipped}


async def _scan_async(route: str, root: str) -> Dict[str, Any]:
    """磁盘 IO 放子线程 + 超时闸门。超时按"该路本次弃用"表态，不拖垮整个请求。"""
    t0 = time.perf_counter()
    try:
        return await asyncio.wait_for(asyncio.to_thread(_scan_one, route, root),
                                      timeout=SCAN_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"ok": False, "items": [],
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": f"扫描超时 {SCAN_TIMEOUT_S}s（该路本次弃用）"}
    except Exception as e:  # noqa: BLE001 — 兜底也要表态，绝不静默
        return {"ok": False, "items": [],
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": tdai_client.scrub(f"{type(e).__name__}: {e}")[:200]}


async def _tdai_async() -> Dict[str, Any]:
    """TDAI 技能注册表这一路。`skill_list()` 已把字段收敛过，这里只做门面侧再归一。"""
    t0 = time.perf_counter()
    try:
        r = await tdai_client.skill_list(limit=100)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "items": [],
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": tdai_client.scrub(f"{type(e).__name__}: {e}")[:200]}
    if not r.get("ok"):
        return {"ok": False, "items": [], "ms": r.get("ms"),
                "error": tdai_client.scrub(r.get("error") or "未知失败")[:200]}
    items = [{"name": tdai_client.scrub(h.get("name") or h.get("id") or "(无名)"),
              "description": tdai_client.scrub(h.get("description") or ""),
              "route": TDAI_ROUTE, "path": None, "realpath": None, "via_symlink": False,
              "fm": None, "fm_keys": [], "encoding": None, "bytes": None, "mtime": None,
              "id": h.get("id"), "version": h.get("version"), "status": h.get("status"),
              "owner_agent_id": h.get("owner_agent_id"), "source": "tdai"}
             for h in (r.get("items") or [])]
    return {"ok": True, "items": items, "ms": r.get("ms"), "error": None,
            "total": r.get("total")}


def _backend(name: str, r: Dict[str, Any]) -> Dict[str, Any]:
    """与 `kb.py:_backend` **同形 5 键**。⚠ count 的算法必须与各路返回的形状配套：
    本模块各路回 `items`（不回 `count`），所以这里数 `len(items)`——
    抄 `memory.py:116` 那种 `int(r.get("count") or 0)` 会让 count 恒为 0，
    于是 `engines` 空、`engine="none"`、HTTP 200 + 空结果，正是要消灭的静默形态。"""
    return {"name": name, "ok": bool(r.get("ok")), "count": len(r.get("items") or []),
            "ms": r.get("ms"),
            "error": tdai_client.scrub(r.get("error"))[:300] if r.get("error") else None}


def _dedup(items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按 realpath 去重，返回 (去重后清单, 别名表)。见模块 docstring 事实②。"""
    by_rp: Dict[str, Dict[str, Any]] = {}
    for it in items:
        rp = it.get("realpath") or it.get("path") or f"__nopath__{it.get('name')}"
        cur = by_rp.get(rp)
        if cur is None:
            by_rp[rp] = dict(it, routes=[it["route"]])
            continue
        if it["route"] not in cur["routes"]:
            cur["routes"].append(it["route"])
        # 规范条目优先取「真实文件那一路」（path == realpath），软链路只当别名
        if it.get("path") == rp and cur.get("path") != rp:
            by_rp[rp] = dict(it, routes=cur["routes"])
    out = sorted(by_rp.values(), key=lambda x: (x.get("route") or "", x.get("name") or ""))
    aliases = [{"name": x["name"], "canonical_route": x["route"], "routes": x["routes"],
                "realpath": x.get("realpath"),
                "why": "同一 realpath 经软链在多个技能目录出现，只列一次，别名如实保留"}
               for x in out if len(x.get("routes") or []) > 1]
    return out, aliases


def _match_q(it: Dict[str, Any], ql: str) -> bool:
    if not ql:
        return True
    hay = " ".join(str(it.get(k) or "") for k in ("name", "description", "path", "route")).lower()
    return ql in hay


@router.get("/list")
@runlog.track("skill.list")
async def skill_list(request: Request,
                     q: str = Query(default="", max_length=200,
                                   description="对 name/description/path/route 做不区分大小写子串过滤"),
                     limit: int = Query(default=200, ge=1, le=500),
                     routes: Optional[str] = Query(
                         default=None,
                         description="逗号分隔；不传＝全部路。传空串＝400（与 /api/kb 同口径）")):
    """列出本机技能资产：磁盘四路（真实在用）+ TDAI 注册表一路（当前 0 行），逐路表态。

    `routes` 走白名单，未知值直接 400 —— 这不是防攻击，是防**手一抖打出静默空结果**
    （P0 的 `sources=tdaii`、P1 的 `routes=nope` 都是这个形态）。
    """
    t0 = time.monotonic()
    allowed = set(all_routes())
    if routes is None:
        want = set(allowed)
    else:
        want = {x.strip() for x in routes.split(",") if x.strip()}
        bad = sorted(want - allowed)
        if bad:
            raise HTTPException(400, f"未知 routes: {bad}；可用值 {sorted(allowed)}")
        if not want:
            raise HTTPException(400, "routes 不能为空")

    names: List[str] = []
    tasks: List[Any] = []
    for r in disk_routes():
        if r in want:
            names.append(r)
            tasks.append(_scan_async(r, SKILL_DIRS.get(r, "")))
    if TDAI_ROUTE in want:
        names.append(TDAI_ROUTE)
        tasks.append(_tdai_async())

    res = await asyncio.gather(*tasks, return_exceptions=True)
    backends: List[Dict[str, Any]] = []
    disk_items: List[Dict[str, Any]] = []
    tdai_items: List[Dict[str, Any]] = []
    skipped_all: List[Dict[str, Any]] = []
    walked = 0
    for name, r in zip(names, res):
        if isinstance(r, Exception):
            r = {"ok": False, "items": [], "ms": None,
                 "error": tdai_client.scrub(f"{type(r).__name__}: {r}")[:200]}
        backends.append(_backend(name, r))
        walked += len(r.get("items") or [])
        for s in (r.get("skipped_outside") or []):
            skipped_all.append({"route": name, **s})
        if r.get("ok"):
            (tdai_items if name == TDAI_ROUTE else disk_items).extend(r.get("items") or [])

    unique, aliases = _dedup(disk_items)
    ql = (q or "").strip().lower()
    merged = [x for x in unique + tdai_items if _match_q(x, ql)]
    merged = merged[:limit]

    engines = [b["name"] for b in backends if b["ok"] and b["count"]]
    degraded = [b["name"] for b in backends if not b["ok"]]
    notes: List[str] = []
    if degraded:
        notes.append(f"{degraded} 本次弃用，结果不完整")
    if not engines and not degraded:
        notes.append("全部可用路都返回零命中")
    if ql and not merged and not degraded:
        notes.append(f"过滤词 {q!r} 未命中任何技能（不是路挂了）")
    # 注册表空但磁盘有货 ⇒ 必须说出来，否则面板会读成"hub 没查到技能"
    tdai_b = next((b for b in backends if b["name"] == TDAI_ROUTE), None)
    if tdai_b and tdai_b["ok"] and tdai_b["count"] == 0 and len(unique):
        notes.append(f"TDAI 技能注册表 0 行，而磁盘有 {len(unique)} 条在用："
                     "权威源未裁（PT-20260924-08 T2-1），本门面只读不灌")
    if skipped_all:
        notes.append(f"{len(skipped_all)} 个 SKILL.md 因 realpath 越界被拒读（明细见 skipped_outside）")
    return {
        "items": merged,
        "count": len(merged),
        "engine": "+".join(engines) if engines else "none",
        "backends": backends,
        "degraded": degraded,
        "note": "；".join(notes) if notes else None,
        "dedup": {"walked": walked, "unique": len(unique), "aliases": aliases,
                  "why": "os.walk(followlinks=True) 会把软链目录里的技能重复数到，按 realpath 归一"},
        "skipped_outside": skipped_all,
        "took_ms": round((time.monotonic() - t0) * 1000, 1),
    }


def _find_disk(name: str, route: Optional[str]) -> List[Dict[str, Any]]:
    """在磁盘路上按技能名找候选（同步；调用方负责放子线程）。"""
    cands: List[Dict[str, Any]] = []
    for r, root in SKILL_DIRS.items():
        if route and r != route:
            continue
        if not root or not os.path.isdir(root):
            continue
        scan = _scan_one(r, root)
        for it in scan.get("items") or []:
            if (it.get("name") or "").lower() == name.strip().lower():
                cands.append(it)
    return cands


def _guard_inside_whitelist(path: str) -> str:
    """路径穿越闸门：realpath 必须落在白名单目录之内，否则 400。

    为什么必须有：`/api/skill/read` 会把**文件全文**回出去，比 kb 的 preview 截断危险得多；
    而技能目录里本来就允许软链（事实②），软链是可以指向任何地方的。
    判据用 realpath（不是 normpath），这样"软链指向白名单内"合法、"软链指向 /etc"被拒。
    """
    rp = os.path.realpath(path)
    if not _inside(rp, _allowed_roots()):
        raise HTTPException(400, "路径越界：realpath 不在任何技能目录内（拒绝读取）")
    return rp


@router.get("/read")
@runlog.track("skill.read")
async def skill_read(request: Request,
                     name: str = Query(min_length=1, max_length=200),
                     route: Optional[str] = Query(default=None),
                     with_body: bool = Query(default=True, description="false 则只回元信息不回正文")):
    """读一个技能的全文（脱敏后）。同名多路 ⇒ 409 报候选，**不静默挑一个**。"""
    t0 = time.monotonic()
    if route is not None and route not in SKILL_DIRS:
        raise HTTPException(400, f"未知 route: {route!r}；可用值 {sorted(SKILL_DIRS)}")
    try:
        cands = await asyncio.wait_for(asyncio.to_thread(_find_disk, name, route),
                                       timeout=SCAN_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"扫描超时 {SCAN_TIMEOUT_S}s，请重试或缩小 route")

    uniq = {}
    for c in cands:
        uniq.setdefault(c["realpath"], c)
    if not uniq:
        raise HTTPException(404, f"未找到技能 {name!r}（route={route or '全部'}）；可用名见 /api/skill/list")
    if len(uniq) > 1:
        raise HTTPException(409, {
            "error": f"技能名 {name!r} 在多个路下指向不同文件，必须带 route 指明",
            "candidates": [{"route": c["route"], "path": c["path"], "realpath": c["realpath"],
                            "via_symlink": c["via_symlink"], "bytes": c["bytes"]}
                           for c in uniq.values()]})

    it = next(iter(uniq.values()))
    rp = _guard_inside_whitelist(it["path"])
    out: Dict[str, Any] = {k: v for k, v in it.items()}
    out["realpath"] = rp
    if with_body:
        try:
            text, enc = await asyncio.to_thread(_read_text, rp)
        except OSError as e:
            raise HTTPException(500, tdai_client.scrub(f"读取失败：{type(e).__name__}: {e}")[:200])
        raw = text.encode("utf-8")
        truncated = len(raw) > READ_MAX_BYTES
        body = raw[:READ_MAX_BYTES].decode("utf-8", "replace") if truncated else text
        out["content"] = tdai_client.scrub(body)
        out["encoding"] = enc
        out["truncated"] = truncated
        if truncated:
            # 截断必须显式说出来（bytes_total 给全量大小），否则就是又一次静默少内容
            out["bytes_total"] = len(raw)
            out["note"] = (f"正文 {len(raw)}B 超过读取上限 {READ_MAX_BYTES}B，已截断；"
                           "需要全文请直接读磁盘路径")
    out["took_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return out


@router.get("/status")
async def skill_status(force: bool = Query(default=False)):
    """资产面板用：每路是否可用、条目数、frontmatter 缺失清单、软链别名、注册表空不空。

    全实测不猜；带 60s TTL 缓存（技能文件是人工编辑的，不需要每次请求都重扫磁盘）。
    返回形状是**按后端名分组的 dict**（与 `/api/kb/status` 同族），不是 `backends[]`——
    面板要按路取字段，别让它自己去数组里找。
    """
    now = time.monotonic()
    if not force and _STATUS_CACHE["data"] is not None and (now - _STATUS_CACHE["ts"]) < _INFO_TTL_S:
        return {**_STATUS_CACHE["data"], "cached": True}

    t0 = time.monotonic()
    names = list(disk_routes()) + [TDAI_ROUTE]
    tasks = [_scan_async(r, SKILL_DIRS.get(r, "")) for r in disk_routes()] + [_tdai_async()]
    res = await asyncio.gather(*tasks, return_exceptions=True)

    disk_out: Dict[str, Any] = {}
    all_items: List[Dict[str, Any]] = []
    tdai_raw: Dict[str, Any] = {}
    for name, r in zip(names, res):
        if isinstance(r, Exception):
            r = {"ok": False, "items": [], "ms": None,
                 "error": tdai_client.scrub(f"{type(r).__name__}: {r}")[:200]}
        if name == TDAI_ROUTE:
            tdai_raw = r
            continue
        items = r.get("items") or []
        all_items.extend(items)
        disk_out[name] = {
            "available": bool(r.get("ok")),
            "dir": tdai_client.scrub(SKILL_DIRS.get(name, "")),
            "entries": len(items),
            "fm_missing": sorted(i["name"] for i in items if not i.get("fm")),
            "via_symlink": sorted(i["name"] for i in items if i.get("via_symlink")),
            "skipped_outside": r.get("skipped_outside") or [],
            "total_bytes": sum(int(i.get("bytes") or 0) for i in items),
            "newest_mtime": max([i.get("mtime") or 0 for i in items], default=None),
            "ms": r.get("ms"),
            "error": _backend(name, r)["error"],
        }
    unique, aliases = _dedup(all_items)
    tdai_items = tdai_raw.get("items") or []
    data = {
        "disk": disk_out,
        "dedup": {"walked": len(all_items), "unique": len(unique), "aliases": aliases},
        "tdai": {
            "available": bool(tdai_raw.get("ok")),
            "rows": len(tdai_items),
            "total": tdai_raw.get("total"),
            "ms": tdai_raw.get("ms"),
            "error": _backend(TDAI_ROUTE, tdai_raw)["error"],
            "backend": tdai_client.backend_status(),
            "why": ("注册表 HTTP 200 但 0 行：技能实际以磁盘 SKILL.md 形态在用，"
                    "hub 不做 ETL（权威源未裁，PT-20260924-08 T2-1）"
                    if tdai_raw.get("ok") and not tdai_items else
                    "注册表有数据" if tdai_items else "注册表这一路本次不可用，原因见 error"),
        },
        "gap": {
            "disk_unique": len(unique),
            "tdai_rows": len(tdai_items),
            "note": ("磁盘 %d 条在用 vs 注册表 %d 行；两者未打通是**已知待裁项**，不是故障。"
                     "本门面只读、不写任何一侧，故不构成第二权威副本。"
                     % (len(unique), len(tdai_items))),
        },
        "took_ms": round((time.monotonic() - t0) * 1000, 1),
        "cached": False,
    }
    _STATUS_CACHE.update(ts=time.monotonic(), data=data)
    return data


# ── v0.13.26 批2：技能安装管理（软链双发现点）+ 预算化清单 ───────────────
#
# 设计依据（56 号文档实测）：各 CLI 的技能发现点互不相通（claude 只读 ~/.claude/skills，
# codex 等共享 ~/.agents/skills，workbuddy 读 ~/.workbuddy/skills）。让一个技能对多个
# agent 可见的唯一手段＝把**权威副本目录**软链到各发现点——本机已有 39 条这样的软链。
#
# 两条安全铁律：
# 1. **只建/删软链，不复制文件**：同源唯一副本（09-19「唯一权威副本」主权原则），
#    重复副本必然漂移；
# 2. **remove 只删软链**（islink 才动手）：真目录是权威本体，从 HTTP 端点删本体
#    属不可逆破坏，一律 409 拒绝（无主副本处置另行裁定，不在本端点顺手做）。
# 鉴权：POST/DELETE 自动过 writeauth 全局中间件（x-hub-token）；审计入 asset_audit
# （action=bind/unbind，asset_type=skill）。

_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\Z")


class InstallRequest(BaseModel):
    name: str
    from_route: str                      # 源发现点（软链指向它的真实目录）
    targets: List[str]                    # 目标发现点列表


@router.post("/install")
async def skill_install(req: InstallRequest, request: Request):
    """把 from_route 发现点的技能软链到 targets 各发现点（幂等：同 realpath 已存在则 no-op）。"""
    if not _NAME_RE.match(req.name):
        raise HTTPException(400, f"技能名不合法（只允许字母数字._-，且不以 . 开头）：{req.name!r}")
    if req.from_route not in SKILL_DIRS:
        raise HTTPException(400, f"未知 from_route: {req.from_route!r}；可用 {sorted(SKILL_DIRS)}")
    bad = [t for t in req.targets if t not in SKILL_DIRS]
    if bad:
        raise HTTPException(400, f"未知目标发现点: {bad}；可用 {sorted(SKILL_DIRS)}")
    if req.from_route in req.targets:
        raise HTTPException(400, "from_route 不能同时是目标（自链无意义）")

    src_root = SKILL_DIRS[req.from_route]
    src = os.path.join(src_root, req.name)
    if not os.path.isdir(src):
        raise HTTPException(404, f"源技能不存在：{req.from_route}/{req.name}")
    if not os.path.isfile(os.path.join(src, "SKILL.md")):
        raise HTTPException(400, f"{req.from_route}/{req.name} 没有 SKILL.md，不是可安装技能")
    src_rp = os.path.realpath(src)

    created: List[str] = []
    existed: List[str] = []
    for t in req.targets:
        dst = os.path.join(SKILL_DIRS[t], req.name)
        if os.path.lexists(dst):
            if os.path.realpath(dst) == src_rp:
                existed.append(t)          # 幂等：同源软链已就位
                continue
            raise HTTPException(409, f"目标已存在且指向不同的副本：{t}/{req.name} → "
                                     f"{os.path.realpath(dst)}（拒绝覆盖，先人工处置）")
        os.symlink(src_rp, dst)
        created.append(t)
    db.log_asset_event("skill", req.name, "bind", writeauth.actor_of(request), {
        "from_route": req.from_route, "created": created, "existed": existed,
        "src_realpath": src_rp})
    return {"status": "installed", "name": req.name, "created": created,
            "existed": existed, "src": src_rp}


@router.delete("/remove")
async def skill_remove(name: str = Query(min_length=1, max_length=100),
                       route: str = Query(min_length=1, max_length=40),
                       request: Request = None):
    """删指定发现点上的**软链**（islink 才删）。真目录=权威本体，409 拒绝。"""
    if route not in SKILL_DIRS:
        raise HTTPException(400, f"未知 route: {route!r}；可用 {sorted(SKILL_DIRS)}")
    if not _NAME_RE.match(name):
        raise HTTPException(400, f"技能名不合法：{name!r}")
    target = os.path.join(SKILL_DIRS[route], name)
    if not os.path.lexists(target):
        raise HTTPException(404, f"{route}/{name} 不存在")
    if not os.path.islink(target):
        raise HTTPException(409, f"{route}/{name} 是真实目录（权威副本），不从本端点删除；"
                                 f"只删软链（软链的删除不伤本体）")
    os.unlink(target)
    db.log_asset_event("skill", name, "unbind", writeauth.actor_of(request), {
        "route": route, "realpath": os.path.realpath(target)})
    return {"status": "removed", "name": name, "route": route}


#: 预算化清单的 token 估算：CJK 与英文混排取字符/2.5 的粗估。这个数只影响装填顺序，
#: 不影响正确性（超估=保守装填，宁少勿膨胀）。
_CHARS_PER_TOKEN = 2.5


@router.get("/budget")
async def skill_budget(max_tokens: int = Query(default=800, ge=50, le=8000)):
    """按 token 预算裁剪的技能清单（批5 注入通道 hub-facade 的数据源）。

    56 号文档结论：把全部技能描述注入上下文会吃掉可观预算（39 技能 desc 全量约数
    千 token）；agent 侧需要的是「花 N 个 token 知道有哪些技能」。策略：
    - 全条目（name+desc）贪心装填到预算的 70%；
    - 装不下的降级为 name-only（一行一个名字，最便宜）；
    - name-only 也装不下则截断，返回 truncated 与 total——如实说「被裁了」。
    """
    t0 = time.monotonic()
    tasks = [_scan_async(r, SKILL_DIRS.get(r, "")) for r in disk_routes()]
    scans = await asyncio.gather(*tasks)
    items: List[Dict[str, Any]] = []
    for s in scans:
        items.extend(s.get("items") or [])
    unique, _aliases = _dedup(items)

    def est(s: str) -> int:
        return int(len(s) / _CHARS_PER_TOKEN) + 1

    full_cap = int(max_tokens * 0.7)
    full, dropped, used = [], [], 0
    for it in sorted(unique, key=lambda x: (x.get("name") or "").lower()):
        name = str(it.get("name") or "")
        desc = str(it.get("description") or "").strip()
        c = est(f"{name}: {desc}")
        if used + c <= full_cap:
            full.append({"name": name, "description": desc, "routes": it.get("routes"),
                         "est_tokens": c})
            used += c
        else:
            dropped.append(name)
    name_only: List[str] = []
    for n in dropped:
        c = est(n)
        if used + c <= max_tokens:
            name_only.append(n)
            used += c
        else:
            break
    return {"budget": max_tokens, "used_est": used, "full": full,
            "name_only": name_only,
            "truncated": len(dropped) - len(name_only),
            "total": len(unique), "took_ms": round((time.monotonic() - t0) * 1000, 1)}
