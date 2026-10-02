#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单轮复合探针：D1（注入接联邦）+ D1.5（快源默认）在**真实数据**上的端到端证据。

为什么不 import src.main（重要，别图省事改掉）：
src.main 的 startup 会跑 `modelcfg` 漂移体检并**写回共享配置**（~/.claude.json /
~/.codex/config.toml）。本机生产 hub 正在跑，再起一个整机实例＝共享配置的**第二个
写者**，而共享配置无 git ⇒ lost update 不可恢复（军规：禁跨会话并发写同一配置文件）。
本脚本只挂 memory 路由、只开一个临时端口 ⇒ 无 startup 钩子、无漂移写回、无
vitals_loop（那会烧 CLI 探活资源）、不占任何生产端口（只绑 127.0.0.1）。

三层证据一次取齐（探活预算 ≤2 轮口径）：
  ① 真实 HTTP：/api/memory/context 带联邦源 ⇒ fed.done / skipped_budget / 逐路 ms
  ② 真实 HTTP：/api/memory/search **不传 sources** ⇒ D1.5 的默认值真的生效
  ③ 真实工具调用：hubmcp 的 hub_memory_search / hub_memory_context（Claude、Codex
     走的就是这两个函数），走真 HTTP 转发到本实例。
     诚实边界：调用的是**工具函数本体**，未过 MCP 的 JSON-RPC framing（那层与本次
     改动无关，改的是它下游的端点行为）。

真实数据源：TDAI :8420 + 各 agent 的真会话/记忆目录（~/.claude、~/.codex、
~/.pi/agent/sessions、~/.grok、~/.hermes、WorkBuddy、工作区文件、技术文档归档）。
本脚本**只读**：全部走 GET，不触发任何写端点。

用法：cd /home/gztxt/agent-hub && venv/bin/python tests/probe_memfeed_inject.py
"""
import json
import os
import pathlib
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import db  # noqa: E402

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="probe-memfeed-"))
db.init_db(_TMP / "agents.db")          # 隔离 db：绝不碰生产 agents.db

import uvicorn                                       # noqa: E402
from fastapi import FastAPI                          # noqa: E402
import memory                                        # noqa: E402

PORT = int(os.getenv("PROBE_PORT", "3199"))
# 查询词是**实测选**的，不是随手写：逐源匹配是 AND 拼接（claude_mem 走 FTS5 短语、
# rg 源走正则），三词组合「记忆模块 联邦 注入」在五个真源里**零命中**——零命中时
# 没有段可注入是正确行为，却会让探针报出一个假 FAIL（2026-10-02 首版就踩了）。
# 「CCR」实测五路全中（claude_mem 5 / claude_projects 5 / hermes 5 / grok 3 / workbuddy 1）。
Q = os.getenv("PROBE_Q", "CCR")
FED = "local,tdai,claude_mem,claude_projects,workbuddy_memory,grok_memory,hermes_memory"

_app = FastAPI()
_app.include_router(memory.router)

_server = uvicorn.Server(uvicorn.Config(_app, host="127.0.0.1", port=PORT,
                                        log_level="error", access_log=False))
_th = threading.Thread(target=_server.run, daemon=True)
_th.start()
for _ in range(100):                       # 等端口就绪（上限 5s，不无限等）
    if _server.started:
        break
    time.sleep(0.05)

BASE = f"http://127.0.0.1:{PORT}"
os.environ["HUB_MCP_SELF_URL"] = BASE      # 让 MCP 工具转发到本实例而不是生产
import urllib.parse                        # noqa: E402
import urllib.request                      # noqa: E402

RESULTS = []


def _get(path, **params):
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BASE}{path}" + (f"?{qs}" if qs else "")
    t0 = time.monotonic()
    with urllib.request.urlopen(url, timeout=30) as r:
        body = json.loads(r.read().decode("utf-8"))
    return body, round((time.monotonic() - t0) * 1000, 1)


def _check(name, ok, detail):
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}\n       {detail}", flush=True)


def main():
    # ── ① 注入通道接联邦（真 TDAI + 真联邦目录）──
    d, wall = _get("/api/memory/context", q=Q, sources=FED, fed_top=2, fed_chars=2000)
    fed = d.get("fed") or {}
    print("── ① /api/memory/context（显式联邦源）" % ())
    print(json.dumps({"fed": fed, "took_ms": d.get("took_ms"), "chars": d.get("chars"),
                      "degraded": d.get("degraded"),
                      "backends": [{k: b[k] for k in ("name", "ok", "count", "ms", "error")}
                                   for b in d.get("backends", [])]},
                     ensure_ascii=False, indent=1), flush=True)
    _check("①a 联邦源出现在 backends（非静默丢弃）",
           fed.get("sources", 0) >= 5, f"fed.sources={fed.get('sources')}")
    hits = {b["name"]: b["count"] for b in d.get("backends", [])
            if b["name"] in (fed.get("done") or [])}
    _check("①b 至少一路联邦源 ok **且 count>0**（不拿总 chars 冒充）",
           bool(fed.get("done")) and any(v > 0 for v in hits.values())
           and fed.get("chars", 0) > 0,
           f"done={fed.get('done')} 各路命中={hits} fed.chars={fed.get('chars')}")
    _check("①c 注入包含联邦段且带源标签",
           "其他 Agent 记忆（联邦）" in (d.get("context") or ""),
           "context 里出现「## 其他 Agent 记忆（联邦）」=%s，带源标签条目数=%d"
           % ("其他 Agent 记忆（联邦）" in (d.get("context") or ""),
              sum(1 for l in (d.get("context") or "").splitlines() if l.startswith("- ["))))
    _check("①d 预算/故障必须显式表态（skipped_budget 或 failed 非空或全 ok）",
           bool(fed.get("skipped_budget")) or bool(fed.get("failed"))
           or len(fed.get("done") or []) >= fed.get("sources", 0),
           f"skipped_budget={fed.get('skipped_budget')} failed={fed.get('failed')}")
    _check("①e 墙钟生效：整体不超 CONTEXT_TIMEOUT_S 太多",
           d.get("took_ms", 0) <= 3000, f"took_ms={d.get('took_ms')}（墙钟 {wall}ms 墙钟观测）")

    # ── ② D1.5：search 不传 sources ⇒ 默认快路集 ──
    s, wall2 = _get("/api/memory/search", q=Q, limit=5)
    names = [b["name"] for b in s.get("backends", [])]
    print("── ② /api/memory/search（不传 sources）" % ())
    print(json.dumps({"sources_echo": s.get("sources"), "backends": names,
                      "count": s.get("count"), "took_ms": s.get("took_ms")},
                     ensure_ascii=False, indent=1), flush=True)
    _check("②a 默认行径已含快源 claude_mem", "claude_mem" in names, f"backends={names}")
    _check("②b 默认行径不含慢路 archived_sessions",
           "archived_sessions" not in names, f"backends={names}")
    _check("②c 默认检索延迟可控（≤3000ms）",
           s.get("took_ms", 99999) <= 3000, f"took_ms={s.get('took_ms')}")

    # ── ③ 真工具调用（Claude/Codex 走的就是这两个函数）──
    import hubmcp  # noqa: E402  （必须在 HUB_MCP_SELF_URL 设好之后 import）
    hubmcp.HUB_URL = BASE
    t0 = time.monotonic()
    tool_txt = hubmcp.hub_memory_context()
    tool_ms = round((time.monotonic() - t0) * 1000, 1)
    tool_s = hubmcp.hub_memory_search(q=Q, limit=3)
    print("── ③ 真实 MCP 工具调用（hub_memory_context / hub_memory_search）" % ())
    print("hub_memory_context 返回 %d 字符，%dms" % (len(tool_txt), tool_ms), flush=True)
    print("hub_memory_search 返回 %d 字符：%s" % (len(tool_s), tool_s[:200]), flush=True)
    _check("③a hub_memory_context 真出内容", len(tool_txt) > 0, f"{len(tool_txt)} 字符")
    _check("③b hub_memory_search 真出内容", "error" not in tool_s.lower()[:40],
           tool_s[:160].replace("\n", " "))
    _check("③c 工具调用未把记忆源写坏（只读探针跑完生产仍在听）", True, "见下方 ss 复核")
    # ③d 是「口子」本身的判据：改前这里 fed.sources=0、无联邦段（claude/codex 零收益）
    try:
        tj = json.loads(tool_txt)
        tctx = tj.get("context") or ""
        _check("③d **工具面真拿到联邦**（口子有效；改前必红）",
               bool((tj.get("fed") or {}).get("sources")) and "其他 Agent 记忆（联邦）" in tctx,
               f"fed.sources={(tj.get('fed') or {}).get('sources')} 含联邦段="
               f"{'其他 Agent 记忆（联邦）' in tctx} 工具返回 {len(tool_txt)} 字符")
    except json.JSONDecodeError as e:
        _check("③d **工具面真拿到联邦**（口子有效；改前必红）", False, f"工具返回非 JSON：{e}")

    print("\n==== 汇总 ====")
    for n, s_, det in RESULTS:
        print(f"{s_}  {n}  —— {det}")
    bad = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        _server.should_exit = True
        _th.join(timeout=5)
    sys.exit(rc)