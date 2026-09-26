"""记忆中心（Agent_Manager 三层记忆模型 L1-L3 的轻量移植）

- L1 可检索记忆：结构化条目（fact/decision/constraint/preference），语义检索按需召回
- L2 近30天工作记忆：整文档；content 由 L1 压缩生成（LLM 可用时），manual 用户手写独立保存
- L3 长期 Profile：整文档，人工确认制——与上游一致：只有用户能删改，自动流程不碰 manual 区
- 注入通道：GET /api/memory/context 产出「开局上下文包」（L3+L2+相关 L1）

【v0.13.16 主权收口】记忆的**权威副本在 TDAI（:8420，实测 L1 233 / L0 5967 / L3 persona）**，
不在本模块的 `memories` 表（实测只有 4 行 2026-09-06 的陈旧便签）。因此检索与注入
一律**并联**本地与 TDAI 后 RRF 融合，**不得再写「本地无命中才查权威库」的短路**。
旧实现那一条把路径/方法/两个鉴权头写错、又用 `except: pass` 吞掉，对外只报
`{count:0, engine:"keyword"}` 的 HTTP 200 —— 属「全指标绿而功能层已死」。详见 tdai_client.py 顶部。
"""
import asyncio
import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

import db
import llm
import memfed
import runlog
import tdai_client
import writeauth

router = APIRouter()

CATEGORIES = {"fact", "decision", "constraint", "preference"}

#: RRF 常数。取文献惯例 60：名次差异在前几名被拉开、长尾被压平，
#: 且**不依赖各路原始分**（本地 LIKE 无分、TDAI 是余弦，量纲不可比）。
RRF_K = 60
#: 开局注入包里的 TDAI 调用预算。它坐在会话起始链路上，超了就必须放弃该路，
#: 不能把对话卡在记忆检索后面（v2 设计里唯一被完整采纳的一条约束）。
CONTEXT_TIMEOUT_S = float(os.getenv("MEMORY_CONTEXT_TIMEOUT", "1.2"))
#: 权威 persona 很長（实测数千字），给注入包留固定占比，不要把 L2/L1 挤光。
PROFILE_CHARS = int(os.getenv("MEMORY_PROFILE_CHARS", "1200"))


def _local_l1(q: str, limit: int) -> list:
    # `layer='L1'` 不是多余过滤：`list_l1` 一直按 layer='L1' 取数，而检索路此前把全表混入
    # 融合，两个端点口径不一致（同一个库给两个答案）。09-24 独立复核发现。
    rows = db.query(
        "SELECT * FROM memories WHERE status='active' AND layer='L1' AND content LIKE ? "
        "ORDER BY updated_at DESC LIMIT ?", (f"%{q}%", limit))
    for r in rows:
        r["source"] = "local"
    return rows


# `sources` 只认白名单里的词。用白名单 + 400 而不是「未知词则忽略」，是因为忽略会造出
# **第三种静默零结果**：`?sources=tdaii`（手一抖）→ 两路全跳 → HTTP 200、
# `backends:[]`、无 error、count=0——与本次要修的缺陷对外表现一模一样。
# v0.13.26 批1：白名单动态扩容进 memfed 注册表（claude_mem / pi_sessions /
# codex_sessions / workspace_files / archived_sessions）。仍保持「local,tdai」为默认值：
# 联邦源是 opt-in，前端（批4）与注入通道（批5）点名后再开，避免默认行径突变。
SOURCE_WHITELIST = ("local", "tdai") + tuple(memfed.enabled_ids())
#: 联邦源路由：memfed 搜索结果 → RRF（routes 顺序即权威度顺序，权重从注册表取）
_FED_TAIL = tuple(memfed.enabled_ids())


def _split_sources(sources: str) -> set:
    want = {s.strip() for s in (sources or "").split(",") if s.strip()}
    bad = sorted(want - set(SOURCE_WHITELIST))
    if bad:
        raise HTTPException(
            400, f"未知 sources: {bad}；可用值 {list(SOURCE_WHITELIST)}")
    if not want:
        raise HTTPException(400, "sources 不能为空")
    return want


# RRF 权重：权威库满权，原始会话略降，本地便签压到 0.2。
# 为何本地不是 0：它记的是「hub 自己看到的本机事实」（端口/部署），TDAI 里没有；
# 为何不是 1.0：只有 4 行且与权威库同权时，`sorted` 的稳定排序会让它**恰好压过
# TDAI 第一名**（rank0 双方同为 1/(K+1)），4 命中 3 条即占 3 席。
# 09-24 独立复核指出 `weights` 形参全仓无人传（死旋钮），已用真代码复现。
W_LOCAL, W_TDAI_L1, W_TDAI_L0 = 0.2, 1.0, 0.6


def _exc(e: BaseException) -> str:
    """异常 → 可外发的短字符串。**必须过 scrub**：上游异常文本里可能带 URL/头片段，
    直接 `str()` 进 `backends[].error` 是一条绕过脱敏的出口（09-24 复核发现的真漏 1）。
    与 `tdai_client` 共用同一个 scrub 实现，脱敏只能有一处。"""
    return tdai_client.scrub(f"{type(e).__name__}: {e}")[:160]


def _rrf_fuse(rankings, weights=None, limit=10):
    """Reciprocal Rank Fusion：score = Σ weight/(RRF_K + rank + 1)。

    为什么不用原分加权：本地那路是 LIKE，根本没有可比的分数；一旦拿
    `0.76 > 1` 这种比较去排序，就是在拿两侧量纲开源。只用名次。
    去重键带 source，避免本地整型 id 与 TDAI 的 `m_xxx` 字符串 id 撞车。
    """
    pool: dict = {}
    n = len(rankings)
    w = weights or [1.0] * n
    for ri, lst in enumerate(rankings):
        for rank, it in enumerate(lst or []):
            key = f"{it.get('source','')}|{it.get('id') or (it.get('content') or '')[:80]}"
            e = pool.get(key)
            if e is None:
                e = dict(it)
                e["rrf"] = 0.0
                e["from"] = []
                pool[key] = e
            e["rrf"] += w[ri] / (RRF_K + rank + 1)
            src = it.get("source")
            if src and src not in e["from"]:
                e["from"].append(src)
    out = sorted(pool.values(), key=lambda x: -x["rrf"])[:limit]
    for o in out:
        o["rrf"] = round(o["rrf"], 5)
    return out


def _backend(name: str, r: dict) -> dict:
    """把一次各路调度的结果压成可读诊断字段。**ok=false 时必须带 error**。"""
    return {"name": name, "ok": bool(r.get("ok")), "count": int(r.get("count") or 0),
            "ms": r.get("ms"), "error": r.get("error") if not r.get("ok") else None}


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    category: str = "fact"
    source: str = "manual"
    session_id: Optional[str] = None


class MemoryBatchIn(BaseModel):
    items: list[MemoryIn] = Field(max_length=200)


class DocIn(BaseModel):
    content: Optional[str] = None
    manual: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── L1 ────────────────────────────────────────────────────────────────

@router.get("/api/memory/l1")
async def list_l1(category: Optional[str] = None, q: Optional[str] = None,
                  limit: int = Query(default=50, le=200)):
    sql = "SELECT * FROM memories WHERE status='active' AND layer='L1'"
    params: list = []
    if category:
        sql += " AND category=?"
        params.append(category)
    if q:
        sql += " AND content LIKE ?"
        params.append(f"%{q}%")
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, tuple(params))
    return {"memories": rows, "count": len(rows)}


@router.post("/api/memory/l1")
async def create_l1(body: MemoryIn, request: Request):
    if body.category not in CATEGORIES:
        raise HTTPException(400, f"category must be one of {sorted(CATEGORIES)}")
    mid = db.add_memory(body.content, body.category, body.source, body.session_id)
    # detail 不记正文（正文已在 memories 表；重复记 = 体积翻倍且多一处凭据面）
    db.log_asset_event("memory_l1", str(mid), "create", writeauth.actor_of(request),
                       {"category": body.category, "source": body.source,
                        "chars": len(body.content or ""), "session_id": body.session_id})
    return {"id": mid, "status": "created"}


@router.post("/api/memory/l1/batch")
async def create_l1_batch(body: MemoryBatchIn, request: Request):
    ids = []
    for item in body.items:
        cat = item.category if item.category in CATEGORIES else "fact"
        ids.append(db.add_memory(item.content, cat, item.source or "extract", item.session_id))
    db.log_asset_event("memory_l1", "batch", "create", writeauth.actor_of(request),
                       {"count": len(ids), "ids": ids[:50]})   # 汇总一行；ids 截 50 防单行爆体积
    return {"ids": ids, "count": len(ids)}


@router.delete("/api/memory/l1/{mid}")
async def delete_l1(mid: int, request: Request):
    n = db.execute("UPDATE memories SET status='deleted', updated_at=? WHERE id=?", (_now(), mid))
    if not n:
        raise HTTPException(404, "memory not found")
    db.log_asset_event("memory_l1", str(mid), "delete", writeauth.actor_of(request),
                       {"soft": True})          # 软删（status='deleted'），不是物理删除
    return {"status": "deleted", "id": mid}


@router.get("/api/memory/search")
@runlog.track("mem.search")
async def search_memory(request: Request,
                        q: str = Query(min_length=1), limit: int = Query(default=10, le=50),
                        sources: str = Query(default="local,tdai"),
                        episodic: int = Query(default=0, ge=0, le=20)):
    """记忆检索：本地 L1（LIKE）与 TDAI 权威库（语义）**并联**后 RRF 融合。

    为什么不保留 v0.13.15 及以前的「本地无命中才打 TDAI」短路：
    本地 `memories` 实测只有 4 行 09-06 陈旧便签，一旦它命中就会把权威库整个屏蔽；
    更重的是原实现的 TDAI 分支路径/方法/两个鉴权头全错且被 `except: pass` 吞掉，
    导致对外只报 `{count:0, engine:"keyword"}` 的 HTTP 200——「全指标绿而功能层已死」。

    `episodic>0` 时额外并一路 L0 原始会话检索（TDAI）。
    失败不抛错、不静默：`backends[]` 逐路报 ok/count/ms/error，前端与闸门都能断言。
    """
    t0 = time.monotonic()
    want = _split_sources(sources)
    backends = []
    routes = []                      # [(name, items, weight)] 顺序即权威度顺序

    local = _local_l1(q, limit) if "local" in want else []
    if "local" in want:
        routes.append(("local", local, W_LOCAL))
        backends.append({"name": "local", "ok": True, "count": len(local),
                         "ms": None, "error": None})

    tdai_hits = []
    if "tdai" in want:
        tasks = [tdai_client.search_memories(q, limit, timeout_s=CONTEXT_TIMEOUT_S * 2.5)]
        if episodic:
            tasks.append(tdai_client.search_conversations(q, episodic,
                                                          timeout_s=CONTEXT_TIMEOUT_S * 2.5))
        res = await asyncio.gather(*tasks, return_exceptions=True)
        c = res[0]
        if isinstance(c, Exception):        # gather 兼容：客户端本不应抛，抛了也不能择掉
            c = {"ok": False, "count": 0, "error": _exc(c)}
        tdai_hits = c.get("items") or []
        backends.append(_backend("tdai_l1", c))
        # L1 先入 rankings、L0 后入：旧写法把 L0 排在 L1 前面，同一名次下
        # **原始会话会压过结构化记忆**，方向反了（09-24 独立复核）。
        routes.append(("tdai_l1", tdai_hits, W_TDAI_L1))
        if episodic:
            e = res[1] if len(res) > 1 else {"ok": False, "count": 0, "error": "未执行"}
            if isinstance(e, Exception):
                e = {"ok": False, "count": 0, "error": _exc(e)}
            backends.append(_backend("tdai_l0", e))
            if e.get("ok"):
                routes.append(("tdai_l0", e.get("items") or [], W_TDAI_L0))

    # 联邦外部源（claude-mem / 各 CLI 会话 / 工作区文件 / 归档）：与 TDAI 并联、
    # 各自独立超时、失败逐路报 degraded。权重低、粒度是「文件/观察级」，
    # 在 RRF 里天然排在结构化记忆之后——不压过权威库，但不再缺席。
    fed_want = want & set(_FED_TAIL)
    fed_results = await memfed.search_fed(q, limit, fed_want) if fed_want else {}
    for sid in _FED_TAIL:
        if sid not in fed_want:
            continue
        r = fed_results.get(sid) or {"ok": False, "count": 0, "items": [],
                                     "error": "未执行"}
        backends.append(_backend(sid, r))
        if r.get("ok") and r.get("items"):
            routes.append((sid, r["items"], memfed.fed_weight(sid)))

    fused = _rrf_fuse([r[1] for r in routes],
                      weights=[r[2] for r in routes], limit=limit)
    engines = [b["name"] for b in backends if b["ok"] and b["count"]]
    degraded = [b["name"] for b in backends if not b["ok"]]
    return {
        "memories": fused,
        "count": len(fused),
        # 保留 `engine` 与 `tdai` 两个旧字段：hubmcp.hub_memory_search 还在读。
        # 旧实现里它拿 `bool(data.get("tdai"))` 把 TDAI 结果整个丢弃，所以即使透传通了
        # 也会回 count=0；现在 TDAI 条目已入 fused，count 自然对了。
        "engine": "+".join(engines) + "|rrf" if engines else "none",
        "tdai": bool(tdai_hits),
        "backends": backends,
        "degraded": degraded,
        "took_ms": round((time.monotonic() - t0) * 1000, 1),
    }


# ── L2 / L3 文档 ──────────────────────────────────────────────────────

def _get_doc(layer: str) -> dict:
    rows = db.query("SELECT * FROM memory_docs WHERE layer=?", (layer,))
    if rows:
        return rows[0]
    return {"layer": layer, "content": "", "manual": "", "updated_at": None}


def _put_doc(layer: str, content: Optional[str], manual: Optional[str]) -> dict:
    cur = _get_doc(layer)
    new_content = content if content is not None else cur["content"]
    new_manual = manual if manual is not None else cur["manual"]
    db.execute(
        """INSERT INTO memory_docs(layer,content,manual,updated_at) VALUES(?,?,?,?)
           ON CONFLICT(layer) DO UPDATE SET content=excluded.content,
             manual=excluded.manual, updated_at=excluded.updated_at""",
        (layer, new_content, new_manual, _now()))
    return _get_doc(layer)


def _audit_doc(layer: str, body: "DocIn", request) -> None:
    """L2/L3 变更审计。touched 必须点名动了哪个字段 —— `manual` 是用户手写补充，
    09-23 曾被 rebuild 静默覆盖过（见 writeauth.py docstring 记录的事故），它被动过要单独留痕。
    只记字符数不记正文：正文在 memory_docs 表里，审计不是第二份副本。"""
    touched = [k for k, v in (("content", body.content), ("manual", body.manual))
               if v is not None]
    db.log_asset_event("memory_doc", layer, "update", writeauth.actor_of(request),
                       {"touched": touched,
                        "content_chars": len(body.content) if body.content is not None else None,
                        "manual_chars": len(body.manual) if body.manual is not None else None})



@router.get("/api/memory/l2")
async def get_l2():
    return _get_doc("L2")


@router.put("/api/memory/l2")
async def put_l2(body: DocIn, request: Request):
    _audit_doc("L2", body, request)
    return _put_doc("L2", body.content, body.manual)


@router.get("/api/memory/l3")
async def get_l3():
    return _get_doc("L3")


@router.put("/api/memory/l3")
async def put_l3(body: DocIn, request: Request):
    _audit_doc("L3", body, request)
    return _put_doc("L3", body.content, body.manual)


@router.post("/api/memory/l2/rebuild")
async def rebuild_l2(request: Request):
    """用近 30 天 L1 压缩生成 L2.content 草稿（LLM 不可用时返回降级拼接）"""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rows = db.query(
        "SELECT category,content,created_at FROM memories "
        "WHERE status='active' AND layer='L1' AND created_at>=? ORDER BY id DESC LIMIT 300",
        (since,))
    if not rows:
        raise HTTPException(400, "近30天无 L1 记忆可压缩")
    raw = "\n".join(f"- [{r['category']}] {r['content']} ({r['created_at'][:10]})" for r in rows)
    llm_used = False
    if llm.configured():
        try:
            content, _ = await llm.chat_tools_loop(
                [{"role": "system", "content":
                  "你是记忆整理器。把下面的记忆条目压缩为按主题分组的中文要点文档（Markdown，"
                  "去重合并，保留精确数值/路径/ID），不要添加条目中没有的内容。直接输出文档正文。"},
                 {"role": "user", "content": raw}],
                tools=[], max_rounds=1)
            llm_used = True
        except Exception:  # noqa: BLE001
            content = f"# 近30天工作记忆（机械压缩 {len(rows)} 条）\n\n{raw}"
    else:
        content = f"# 近30天工作记忆（机械压缩 {len(rows)} 条）\n\n{raw}"
    doc = _put_doc("L2", content, None)  # manual 不动
    db.log_asset_event("memory_doc", "L2", "rebuild", writeauth.actor_of(request),
                       {"items": len(rows), "llm": llm_used,
                        "manual_untouched": True})   # _put_doc(…, None) ⇒ manual 一字未动，如实记

    return {"status": "rebuilt", "items": len(rows), "llm": llm_used,
            "updated_at": doc["updated_at"]}


# ── 注入通道 ──────────────────────────────────────────────────────────

@router.get("/api/memory/context")
@runlog.track("mem.context")
async def injection_context(request: Request, q: Optional[str] = None,
                            max_chars: int = Query(default=6000, le=20000),
                            sources: str = Query(default="local,tdai"),
                            scenes: int = Query(default=0, ge=0, le=20)):
    """SessionStart 注入包：L3 + L2(content+manual) + 相关 L1（本地与权威库融合）。

    为什么 L3 要回落到 TDAI：hub 自己的 `memory_docs` 实测只有 L2 一行，**L3 为空**，
    所以旧版返回的开局包里根本不会出现「## 长期 Profile」段——实测只有 357 字、
    且是 18 天前的 L2 旧稿。权威 L3（persona）在 TDAI `/v2/core/read`。

    时间预算：本函数坐在会话起始链路上，TDAI 各路给 `MEMORY_CONTEXT_TIMEOUT`（默认 1.2s），
    超时即**弃该路并记入 degraded**，绝不把对话卡在记忆检索后面。
    """
    t0 = time.monotonic()
    want = _split_sources(sources)
    l3 = _get_doc("L3")
    l2 = _get_doc("L2")

    # 一开始就把所有 TDAI 调用并发发出去，不串行等
    calls = {}
    if "tdai" in want and not (l3["content"] or l3["manual"]):
        calls["profile"] = tdai_client.core_read(timeout_s=CONTEXT_TIMEOUT_S)
    if "tdai" in want and q:
        calls["l1"] = tdai_client.search_memories(q, 10, timeout_s=CONTEXT_TIMEOUT_S)
    if "tdai" in want and scenes:
        calls["scenes"] = tdai_client.scenario_ls(timeout_s=CONTEXT_TIMEOUT_S)
    results = dict(zip(calls.keys(), await asyncio.gather(*calls.values(),
                                                         return_exceptions=True))) \
        if calls else {}
    backends = []
    for name, r in results.items():
        if isinstance(r, Exception):
            r = {"ok": False, "count": 0, "error": _exc(r)}
            results[name] = r
        if name == "profile" and r.get("ok"):
            # /v2/core/read 的契约里没有 items，不补这一刀会出现
            # 「ok:true 但 count:0」这个歧义信号——而歧义信号正是本次要消除的东西。
            r["count"] = 1 if (r.get("content") or "").strip() else 0
        backends.append(_backend(f"tdai_{name}", r))
    if "local" in want:
        backends.append({"name": "local", "ok": True,
                         "count": int(bool(l3["content"] or l3["manual"]))
                         + int(bool(l2["content"] or l2["manual"])),
                         "ms": None, "error": None})

    parts = []
    if l3["content"] or l3["manual"]:
        parts.append(f"## 长期 Profile\n{l3['content']}\n{l3['manual']}")
    else:
        pr = results.get("profile") or {}
        if pr.get("ok") and pr.get("content"):
            parts.append("## 长期 Profile（TDAI 权威源）\n"
                         + pr["content"][:PROFILE_CHARS])
    if l2["content"] or l2["manual"]:
        parts.append(f"## 近30天工作记忆\n{l2['content']}\n{l2['manual']}")

    if q:
        local_rows = _local_l1(q, 10)
        tdai_rows = (results.get("l1") or {}).get("items") or []
        fused = _rrf_fuse([local_rows, tdai_rows],
                          weights=[W_LOCAL, W_TDAI_L1], limit=10)
        if fused:
            parts.append("## 相关记忆\n" + "\n".join(
                f"- [{m.get('category') or m.get('type') or 'l1'}] {m.get('content','')}"
                for m in fused))

    if scenes:
        ent = (results.get("scenes") or {}).get("entries") or []
        if ent:
            parts.append("## 场景索引（TDAI L2）\n"
                         + "\n".join(f"- {e.get('path','')}" for e in ent[:scenes]))

    text = "\n\n".join(parts)
    truncated = len(text) > max_chars
    body = text[:max_chars]
    degraded = [b["name"] for b in backends if not b["ok"]]
    return {"context": body, "truncated": truncated, "chars": len(body),
            "backends": backends, "degraded": degraded,
            "took_ms": round((time.monotonic() - t0) * 1000, 1)}
