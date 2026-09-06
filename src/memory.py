"""记忆中心（Agent_Manager 三层记忆模型 L1-L3 的轻量移植）

- L1 可检索记忆：结构化条目（fact/decision/constraint/preference），语义检索按需召回
- L2 近30天工作记忆：整文档；content 由 L1 压缩生成（LLM 可用时），manual 用户手写独立保存
- L3 长期 Profile：整文档，人工确认制——与上游一致：只有用户能删改，自动流程不碰 manual 区
- 注入通道：GET /api/memory/context 产出「开局上下文包」（L3+L2+相关 L1）
"""
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import db
import llm

router = APIRouter()

CATEGORIES = {"fact", "decision", "constraint", "preference"}


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
async def create_l1(body: MemoryIn):
    if body.category not in CATEGORIES:
        raise HTTPException(400, f"category must be one of {sorted(CATEGORIES)}")
    mid = db.add_memory(body.content, body.category, body.source, body.session_id)
    return {"id": mid, "status": "created"}


@router.post("/api/memory/l1/batch")
async def create_l1_batch(body: MemoryBatchIn):
    ids = []
    for item in body.items:
        cat = item.category if item.category in CATEGORIES else "fact"
        ids.append(db.add_memory(item.content, cat, item.source or "extract", item.session_id))
    return {"ids": ids, "count": len(ids)}


@router.delete("/api/memory/l1/{mid}")
async def delete_l1(mid: int):
    n = db.execute("UPDATE memories SET status='deleted', updated_at=? WHERE id=?", (_now(), mid))
    if not n:
        raise HTTPException(404, "memory not found")
    return {"status": "deleted", "id": mid}


@router.get("/api/memory/search")
async def search_memory(q: str = Query(min_length=1), limit: int = Query(default=10, le=50)):
    """关键词检索（上游为 BGE 语义检索，此处轻量版；接 TDAI/embedding 留扩展位）"""
    rows = db.query(
        "SELECT * FROM memories WHERE status='active' AND content LIKE ? "
        "ORDER BY updated_at DESC LIMIT ?", (f"%{q}%", limit))
    result = {"memories": rows, "count": len(rows), "engine": "keyword"}
    # 透传 TDAI（若在跑且本地无命中）
    if not rows:
        try:
            import aiohttp

            async with aiohttp.ClientSession() as s:
                async with s.get(f"{os.getenv('TDAI_URL', 'http://127.0.0.1:8420')}/memory/search",
                                 params={"q": q, "limit": limit},
                                 timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result["tdai"] = data
                        result["engine"] = "keyword+tdai"
        except Exception:  # noqa: BLE001
            pass
    return result


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


@router.get("/api/memory/l2")
async def get_l2():
    return _get_doc("L2")


@router.put("/api/memory/l2")
async def put_l2(body: DocIn):
    return _put_doc("L2", body.content, body.manual)


@router.get("/api/memory/l3")
async def get_l3():
    return _get_doc("L3")


@router.put("/api/memory/l3")
async def put_l3(body: DocIn):
    return _put_doc("L3", body.content, body.manual)


@router.post("/api/memory/l2/rebuild")
async def rebuild_l2():
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
    return {"status": "rebuilt", "items": len(rows), "llm": llm_used,
            "updated_at": doc["updated_at"]}


# ── 注入通道 ──────────────────────────────────────────────────────────

@router.get("/api/memory/context")
async def injection_context(q: Optional[str] = None, max_chars: int = Query(default=6000, le=20000)):
    """SessionStart 注入包：L3 + L2(content+manual) + 相关 L1"""
    l3 = _get_doc("L3")
    l2 = _get_doc("L2")
    parts = []
    if l3["content"] or l3["manual"]:
        parts.append(f"## 长期 Profile\n{l3['content']}\n{l3['manual']}")
    if l2["content"] or l2["manual"]:
        parts.append(f"## 近30天工作记忆\n{l2['content']}\n{l2['manual']}")
    if q:
        rows = db.query("SELECT category,content FROM memories "
                        "WHERE status='active' AND content LIKE ? ORDER BY id DESC LIMIT 10",
                        (f"%{q}%",))
        if rows:
            parts.append("## 相关记忆\n" + "\n".join(f"- [{r['category']}] {r['content']}" for r in rows))
    text = "\n\n".join(parts)
    truncated = len(text) > max_chars
    return {"context": text[:max_chars], "truncated": truncated, "chars": len(text[:max_chars])}
