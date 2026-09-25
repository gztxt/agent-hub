"""本地记忆便签的 staleness **观测**（件 3 / spec §5 的 B-2 改判）。

为什么是"观测"而不是"清理"（实测依据，2026-09-25，生产库只读 mode=ro）：
    memories rows=4  status={'active':4}  最老=2026-09-06T03:51  最新=2026-09-06T03:57
    memory_docs rows=1   db size=1.27MB
⇒ 4 行全 active、零软删行、19 天没长过一行 ⇒ 后台清理任务会永远空转；
而"后台自动重建 L2"这条路有事故前例（writeauth.py docstring：09-23 rebuild 真的重写了
用户手写 L2 记忆，被迫按 09-06 在册副本逐字回滚）；且记忆权威副本在 TDAI(:8420)，
本地表在 KB 融合里权重只有 0.2（src/kb.py 的 W）—— 删它零收益，**报告它腐烂**才是净收益。

★ 本模块**绝不** DELETE / UPDATE / INSERT / 调 LLM / 起后台任务。
  L0 闸门把这条钉死（tests/test_memstats.py::TestModuleCannotMutate）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import db

#: 阈值理由：与本机"结论必须绑定取证日期"的口径同族 —— 超过两周没动过的本地便签，
#: 在 KB 融合里权重只有 0.2，实际上已不参与决策 ⇒ 报 stale（可见）而不是删（不可逆）。
STALE_DAYS = 14


def age_days(iso: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    """ISO 时间串 → 距今天数。**绝不抛**（拿不到就 None）。

    无时区形态按 UTC 兜：kb.py 已经在 turbovec 的 `built_at` 上栽过一次 ——
    naive 与 aware datetime 相减直接 TypeError，而闸门第一版只捕了 ValueError。
    """
    if not iso or not isinstance(iso, str):
        return None
    now = now or datetime.now(timezone.utc)
    try:
        t = datetime.fromisoformat(iso.strip())
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    try:
        return round(max(0.0, (now - t).total_seconds() / 86400.0), 1)
    except (TypeError, ValueError):
        return None


def local_stats(rows: List[Dict[str, Any]], docs: List[Dict[str, Any]],
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """纯函数：吃行集出统计。不碰 db ⇒ L0 可在空 HOME 下判卷。"""
    now = now or datetime.now(timezone.utc)
    ages = [a for a in (age_days(r.get("created_at"), now) for r in (rows or []))
            if a is not None]
    by_status: Dict[str, int] = {}
    for r in (rows or []):
        k = str(r.get("status") or "?")
        by_status[k] = by_status.get(k, 0) + 1
    out: Dict[str, Any] = {
        "rows": len(rows or []),
        "by_status": by_status,
        "oldest_age_days": max(ages) if ages else None,
        "newest_age_days": min(ages) if ages else None,
        "docs": {},
    }
    for d in (docs or []):
        layer = str(d.get("layer") or "?")
        manual = d.get("manual") or ""
        content = d.get("content") or ""
        out["docs"][layer] = {
            "age_days": age_days(d.get("updated_at"), now),
            "chars": len(content),
            "manual_chars": len(manual),
            "has_manual": bool(manual.strip()),
        }
    return out


def verdict(stats: Dict[str, Any]) -> Dict[str, str]:
    """纯判定。文案口径：**只报告，绝不清理** —— 不许出现会被读成"我会删"的字样。"""
    n = int(stats.get("rows") or 0)
    if n == 0:
        return {"state": "empty",
                "reason": "本地便签 0 行。权威记忆在 TDAI(:8420)，本地为空不是故障；"
                          "本模块只观测，不清理。"}
    newest = stats.get("newest_age_days")
    if newest is not None and newest >= STALE_DAYS:
        return {"state": "stale",
                "reason": "共 %d 行，最新一行已 %s 天未更新（阈值 %d 天）。本地表在 KB 融合里"
                          "权重只有 0.2，实际上已不参与决策 ⇒ 报 stale 而不是删。"
                          % (n, newest, STALE_DAYS)}
    return {"state": "fresh",
            "reason": "共 %d 行，最新一行 %s 天前更新。" % (n, newest)}


def collect() -> Dict[str, Any]:
    """唯一碰 db 的入口（**只读 SELECT**）。失败不抛：由调用方按"逐路表态"降级。"""
    rows = db.query("SELECT status, created_at FROM memories")
    docs = db.query("SELECT layer, content, manual, updated_at FROM memory_docs")
    s = local_stats(rows, docs)
    s.update(verdict(s))
    s["authoritative_source"] = "TDAI(:8420)"
    s["policy"] = "observe-only（不删除、不重建、不调 LLM）"
    return s
