#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸门：联邦外部源（memfed）真源冒烟——批1 验收判据（v0.13.26）。

背景：hub 记忆联邦此前 local+TDAI 两路；批1 接入 claude-mem 观察库 / pi·codex 会话
jsonl / 工作区文件记忆 / 归档会话备份五路只读源。宣告能力 ≠ 实际能力（见
verify_memory_federation.py 头注的 TDAI 404 教训）⇒ 本闸门对本机**真实数据**各路冒烟：

  A. /api/memory/fedsources：每个启用源 probe ok（路径/库真实存在且可数）
  B. 联邦检索：sources=全五源 → backends 逐路 ok（缺席=登记失败，count=0 也算通过——
     「不缺席、如实报数」）；claude-mem / 工作区 / 归档三路对「CCR」必须 count>0
     （实测底数：claude-mem FTS 'CCR' 6+1 命中、工作区/归档是 CCR 高频词）。
  C. 融合层：RRF 条目带 source=外部源 id，证明联邦条目真的进了融合而非旁路展示。
  D. 红向：未知源名 400（白名单动态扩容后仍守门）；disabled 源不可点名执行。

隔离性：不 import src.main（不起 vitals_loop）、不占端口（in-process ASGI）、
db 指向临时空库、**全程不打网络**（TDAI 不在 sources 里，claude-mem/sqlite 与 rg 都是本机文件）。
生产 agents.db / claude-mem.db / 各 sessions 目录全程只读（mode=ro / rg 只读）。

用法：cd /home/gztxt/agent-hub && venv/bin/python tests/verify_memfed_federation.py
退出码 0 = 全绿；非 0 = 有 FAIL。
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import db  # noqa: E402

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="memfedgate-"))
db.init_db(_TMP / "agents.db")          # 空库：本地 0 行，命中不可能来自本地便签

from fastapi import FastAPI            # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
import memory                           # noqa: E402
import memfed                           # noqa: E402

_app = FastAPI()
_app.include_router(memory.router)
_app.include_router(memfed.router)
C = TestClient(_app)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


FED_ALL = ",".join(["local", "tdai"] + memfed.enabled_ids())


def main():
    print("\n[A] 源注册表 + probe（真源健康与计数）")
    d = C.get("/api/memory/fedsources").json()
    check("A1 注册表齐（含 disabled opencode）",
          d.get("count") == len(memfed.REGISTRY), f"count={d.get('count')}")
    probes = {s["id"]: s for s in d.get("sources", [])}
    for sid in memfed.enabled_ids():
        p = probes.get(sid, {}).get("probe") or {}
        check(f"A2 probe ok: {sid}", p.get("ok") is True, f"probe={p}")
    check("A3 opencode 登记 disabled",
          probes.get("opencode_sessions", {}).get("enabled") is False
          and "opencode_sessions" not in (d.get("enabled") or []))
    check("A4 probe 带计数与耗时（整理面板数据）",
          all(("count" in (probes[s]["probe"] or {})) and ("ms" in (probes[s]["probe"] or {}))
              for s in memfed.enabled_ids() if s in probes))

    print("\n[B] 联邦检索真源（q=CCR，全程不打网络：sources 不含 tdai）")
    r = C.get("/api/memory/search", params={
        "q": "CCR", "limit": 12,
        "sources": ",".join(["local"] + memfed.enabled_ids())})
    check("B1 HTTP 200", r.status_code == 200, f"rc={r.status_code}")
    d = r.json()
    b = {x["name"]: x for x in d.get("backends", [])}
    for sid in memfed.enabled_ids():
        check(f"B2 路在并 ok: {sid}",
              sid in b and b[sid].get("ok") is True, f"backend={b.get(sid)}")
    # 本机实测底数（2026-09-26）：claude-mem FTS 'CCR'=6+1；工作区/归档 CCR 高频。
    for sid in ("claude_mem", "workspace_files", "archived_sessions"):
        check(f"B3 真实命中: {sid}",
              b.get(sid, {}).get("count", 0) > 0, f"count={b.get(sid, {}).get('count')}")

    print("\n[C] 融合层（联邦条目进入 RRF，不是旁路展示）")
    mems = d.get("memories", [])
    check("C1 count>0", d.get("count", 0) > 0, f"count={d.get('count')}")
    fed_hits = [m for m in mems if m.get("source") in memfed.enabled_ids()]
    check("C2 融合条目里有外部源", len(fed_hits) > 0,
          f"sources={[m.get('source') for m in mems[:8]]}")
    check("C3 条目带 rrf 与脱敏 content",
          all(("rrf" in m) and ("content" in m) for m in fed_hits[:5]))
    blob = " ".join(str(m.get("content", "")) for m in fed_hits)
    check("C4 无凭据外流（token/api_key KV 被遮）",
          "ccr_web_token=" not in blob and "api_key=" not in blob and "sk-" not in blob)

    print("\n[D] 红向：白名单守门与 disabled 拒执行")
    r = C.get("/api/memory/search", params={"q": "CCR", "sources": "local,bogus_src"})
    check("D1 未知源名 400", r.status_code == 400, f"rc={r.status_code}")
    r = C.get("/api/memory/search", params={"q": "CCR",
                                            "sources": "local,opencode_sessions"})
    check("D2 disabled 源点名也不 400（在白名单外才是 400——它根本不在白名单）",
          r.status_code == 400, f"rc={r.status_code}")

    n_fail = sum(1 for _, ok, _ in results if not ok)
    print(f"\n[闸门] ran={len(results)} failures={n_fail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
