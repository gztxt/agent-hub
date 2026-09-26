#!/usr/bin/env python3
"""P1 联邦检索门面闸门：src/kb.py 的红/绿成对断言。

跑法：venv/bin/python tests/verify_kb_federation.py
不占端口（TestClient 走 in-process ASGI），不写任何 db，不重启任何服务。
turbovec / ollama / TDAI 都是**本机已有服务**，本闸门只读调用它们，
对每个外部目标探测 ≤2 次（09-23 红线），拿不到结论即如实 FAIL 并停手。

红向断言的意义：证明"坏的时候会说"。只测绿向等于没测——
P0 修的那个缺陷对外就是 HTTP 200 + 空结果，全绿而功能层已死。
"""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import db  # noqa: E402

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="hubkb-"))
db.init_db(_TMP / "agents.db")          # 空库：本地那路 0 行，命中不可能来自本地

from fastapi import FastAPI                      # noqa: E402
from starlette.testclient import TestClient      # noqa: E402
import kb                                        # noqa: E402
import memory                                    # noqa: E402
import tdai_client                               # noqa: E402

_app = FastAPI()
_app.include_router(kb.router)
C = TestClient(_app)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("  ✅ PASS  " if ok else "  ❌ FAIL  ") + name + (f"   {detail}" if detail else ""))


# 2026-09-24 从 `techdocs_mcp.cli search "CCR 端口 配置" -k 3` 抓的**真输出**，一字未改。
# 刁钻点在 preview 自带换行，且第二段预览里嵌着一行 `3. **改完必须端到端验证并附证据**：…`
# —— 它长得像记录行，但没有 `[score]` 与 `#chunkN`，**不得**被解析成第 3 条以上。
REAL_STDOUT = """1. [0.512] agent-hub-hermes-fusion-analysis.md#chunk7
   | 网上 v1 示例全失效。正确入口实测为 `MCPServer(...)` 的 `.streamable_http_app()` / `.sse_app()` 方法 |

> C1+C2 的合力结论：**窗口模式在两个系统之间彻底关闭**
2. [0.455] AGENTS.md#chunk6
   Web 口，token 不可互串。
3. **改完必须端到端验证并附证据**：至少一次真实文本请求 **+ 一次真工具调用**
   （coding agent 的价值在工具环，文本通 ≠ 配置对）；不通过就回滚到本次备份，
   不在故障
3. [0.436] Agent_Manager/README.md#chunk11
   /port.png)

查看当前机器上所有正在监听的端口：
"""


def main():
    try:
        real = json.loads((pathlib.Path.home() / ".pi/agent/memory-tencentdb.json")
                          .read_text())["server"]
    except (OSError, ValueError) as e:
        print(f"读不到 TDAI 凭据（{type(e).__name__}）⇒ 本闸门需要本机 TDAI 在跑")
        return 1
    os.environ["TDAI_URL"] = real["endpoint"]
    os.environ["TDAI_API_KEY"] = real["apiKey"]
    os.environ["TDAI_SERVICE_ID"] = real["serviceId"]
    KEY = real["apiKey"]

    print("\n[G1 解析器] 真输出 → 结构化，且不被伪记录行骗到")
    rows = kb.parse_turbovec_stdout(REAL_STDOUT)
    check("G1a 恰好 3 条（伪记录行没造出第 4 条）", len(rows) == 3, f"得 {len(rows)} 条")
    check("G1b 字段齐：score/path/chunk/preview",
          all({"score", "path", "chunk", "preview", "source"} <= set(r) for r in rows),
          f"{[list(r) for r in rows[:1]]}")
    check("G1c 多行 preview 完整归位（含被切断的那段）",
          rows[1]["path"] == "AGENTS.md" and "改完必须端到端验证" in rows[1]["preview"],
          f"preview={rows[1]['preview'][:36]!r}")
    check("G1d score 解析为 float 且保留三位语义", rows[0]["score"] == 0.512,
          f"score={rows[0]['score']}")
    check("G1e '(无结果)' 解析成 0 条而不是报错", kb.parse_turbovec_stdout("(无结果)\n") == [])

    print("\n[G2 红向] 坏的时候必须说得出原因（不许静默成空结果）")
    r = kb._turbovec_search_run if False else None            # 占位，避免误用
    import asyncio
    _bak_cwd = kb.TURBOVEC_CWD
    kb.TURBOVEC_CWD = "/tmp"                                   # 故意让模块找不到
    bad = asyncio.run(kb._turbovec_search("端口", 3))
    kb.TURBOVEC_CWD = _bak_cwd
    check("G2a 解释器在但 cwd 错 → ok=False 且 error 非空",
          bad["ok"] is False and bool(bad["error"]), f"error={str(bad['error'])[:64]!r}")
    check("G2b 退出码被如实带出（不谎报成'没命中'）",
          "退出码" in (bad["error"] or "") or "起不来" in (bad["error"] or ""),
          f"error={str(bad['error'])[:60]!r}")

    _bak_to = kb.TV_TIMEOUT_S
    kb.TV_TIMEOUT_S = 0.001                                    # 预算压到 1ms，必然超时
    slow = asyncio.run(kb._turbovec_search("端口", 3))
    kb.TV_TIMEOUT_S = _bak_to
    check("G2c 超时有专属文案且承诺已杀进程（不会留孤儿）",
          slow["ok"] is False and "超时" in (slow["error"] or ""),
          f"error={str(slow['error'])[:52]!r}")
    check("G2d 超时后仍给出 ms 观测（供容量分析，不是丢弃计时）",
          isinstance(slow.get("ms"), (int, float)) and slow["ms"] >= 1, f"ms={slow.get('ms')}")
    _orph = [p for p in os.listdir("/proc") if p.isdigit()]
    check("G2e 本机没有残留的 techdocs 孤儿进程",
          _exec_count_ok(), f"计数法见 _exec_count_ok()")

    print("\n[G3 白名单] routes 不许被写成静默空结果")
    for rv, tag in (("zzz", "未知值"), ("", "空值")):
        rr = C.get("/api/kb/search", params={"q": "端口", "routes": rv})
        check(f"G3 {tag} routes → HTTP 400", rr.status_code == 400, f"HTTP {rr.status_code}")
    rr = C.get("/api/kb/search", params={"q": "端口", "routes": "zzz"})
    check("G3c 400 里点名坏值与可用值", "zzz" in rr.text and "turbovec" in rr.text)

    print("\n[G4 绿向] 三路真并发，文档路真出内容")
    d = C.get("/api/kb/search", params={"q": "CCR 端口 配置", "k": 6, "routes": "tdai,turbovec"}).json()
    b = {x["name"]: x for x in d.get("backends", [])}
    check("G4a count > 0", d.get("count", 0) > 0, f"count={d.get('count')}")
    check("G4b 逐路 backends 齐（tdai_l1 + turbovec）", {"tdai_l1", "turbovec"} <= set(b),
          f"backends={list(b)}")
    check("G4c turbovec 真命中且有 path/chunk（调用方能回原文）",
          b.get("turbovec", {}).get("ok") is True and
          any(r.get("path") and r.get("chunk") is not None for r in d.get("results", [])),
          f"tv={b.get('turbovec')}")
    check("G4d 文档路耗时落在实测口径内（0.25s 固定开销 + embed，<2s）",
          (b.get("turbovec") or {}).get("ms") is not None
          and (b.get("turbovec") or {}).get("ms") < 2000,
          f"ms={(b.get('turbovec') or {}).get('ms')}")
    check("G4e 全路通时 note 为 None（不虚报'结果不完整'）", d.get("note") is None,
          f"note={d.get('note')}")

    print("\n[G5 关键红→绿] 中文改写查询：raw FTS5 unicode61 在这条上召回为 0")
    d2 = C.get("/api/kb/search", params={"q": "部署端口冲突", "k": 5}).json()
    check("G5a 语义路对改写查询有召回（这正是本机 FTS5 失败的那一类）",
          d2.get("count", 0) > 0, f"count={d2.get('count')} engine={d2.get('engine')}")
    srcs = {r.get("source") for r in d2.get("results", [])}
    check("G5b 结果里能看到来源路（不许混成一锅）", bool(srcs), f"sources={sorted(srcs)}")

    print("\n[G6 降级表态] 单路挂掉不得拖死整体，且必须点名")
    _bak = kb.TURBOVEC_PY
    kb.TURBOVEC_PY = "/nonexistent/python"
    d3 = C.get("/api/kb/search", params={"q": "端口", "k": 4}).json()
    kb.TURBOVEC_PY = _bak
    b3 = {x["name"]: x for x in d3.get("backends", [])}
    check("G6a HTTP 仍 200（一路挂不打断调用方）",
          C.get("/api/kb/search", params={"q": "端口", "k": 4}).status_code == 200)
    check("G6b degraded 点名 turbovec", "turbovec" in (d3.get("degraded") or []),
          f"degraded={d3.get('degraded')}")
    check("G6c note 明说结果不完整（防'零命中'被误读成'没有资料'）",
          bool(d3.get("note")) and "turbovec" in d3["note"], f"note={d3.get('note')}")
    check("G6d 其余路照出（tdai 仍 ok）", b3.get("tdai_l1", {}).get("ok") is True,
          f"tdai={b3.get('tdai_l1')}")
    check("G6e 挂掉那路带回了具体原因（不是空串、不是 'error'）",
          len((b3.get("turbovec") or {}).get("error") or "") > 8,
          f"err={str((b3.get('turbovec') or {}).get('error'))[:50]!r}")

    print("\n[G7 status] 面板不许用沉默表达'不接'")
    st = C.get("/api/kb/status").json()
    tv = st.get("turbovec") or {}
    check("G7a turbovec 实测元信息（chunks/dim/model/built_at 均来自 info 子命令）",
          tv.get("available") is True and (tv.get("chunks") or 0) > 1000
          and tv.get("dim") == 384, f"{ {k: tv.get(k) for k in ('chunks','dim','model')} }")
    check("G7b 新鲜度用索引 mtime 算天数（上游 built_at 是无时区串，不得拿去相减）",
          isinstance(tv.get("index_mtime_age_days"), (int, float)) and tv["index_mtime_age_days"] >= 0
          and "mtime" in (tv.get("age_source") or ""),
          f"age_days={tv.get('index_mtime_age_days')} built_at={tv.get('built_at')}")
    wg = st.get("wigolo") or {}
    check("G7c wigolo 显式 available=False **且带 why**（禁止用字段缺失表达不接）",
          wg.get("available") is False and len(wg.get("why") or "") > 30,
          f"why={str(wg.get('why'))[:44]!r}")
    check("G7d info 有 TTL 缓存（第二次调不再起子进程）",
          C.get("/api/kb/status").json()["turbovec"].get("cached") is True,
          "第二次 cached=True")

    print("\n[G8 不变式] 凭据不进任何 kb 响应")
    for p in ("/api/kb/search?q=端口", "/api/kb/search?q=端口&routes=zzz", "/api/kb/status"):
        t = C.get(p).text
        check(f"G8 响应不含 apiKey（{p.split('?')[0]}?…）", KEY not in t)

    print("\n[G9 MCP 层] 门面不许在 MCP 出口又被剥掉诊断（P0 的教训复刻点）")
    import hubmcp
    _bak_get = hubmcp._get
    try:
        hubmcp._get = lambda path, **kw: {
            "results": [{"source": "turbovec", "path": "AGENTS.md", "chunk": 6,
                         "preview": "军规", "score": 0.455},
                        {"source": "tdai_l1", "content": "端口基线", "type": "fact"}],
            "count": 2, "engine": "tdai_l1+turbovec|rrf",
            "backends": [{"name": "turbovec", "ok": True, "count": 1, "ms": 320.0, "error": None}],
            "degraded": [], "note": None, "took_ms": 330.0}
        out = json.loads(hubmcp.hub_kb_search("端口"))
        check("G9a MCP 出口保留 backends/degraded/note",
              all(k in out for k in ("backends", "degraded", "note")), f"keys={list(out)}")
        check("G9b 文档命中带 path/chunk 出去（外部 agent 能回原文）",
              out["results"][0].get("path") == "AGENTS.md" and out["results"][0]["chunk"] == 6)
        check("G9c 记忆命中把 content 映射到位（不留 null）",
              out["results"][1].get("content") == "端口基线")
    finally:
        hubmcp._get = _bak_get

    # ─────────────────────────────────────────────────────────────────────
    # 批3（v0.13.26）：workspace / archived 两路 rg 全文接入 + 假接入护栏
    # ─────────────────────────────────────────────────────────────────────
    r3 = C.get("/api/kb/search", params={
        "q": "CCR", "routes": "workspace,archived", "k": 10})
    d3 = r3.json()
    bk3 = {b["name"]: b for b in d3.get("backends") or []}
    check("B3a 两路 backends 表态齐全且都 ok",
          "workspace" in bk3 and "archived" in bk3
          and bk3["workspace"]["ok"] and bk3["archived"]["ok"],
          f"{[(b['name'], b['ok']) for b in d3.get('backends') or []]}")
    check("B3b workspace 真命中（5350+ 文件面，验证 rg --no-ignore --hidden 链路）",
          (bk3.get("workspace") or {}).get("count", 0) > 0,
          f"workspace={bk3.get('workspace')}")
    check("B3c archived 真命中（会话备份 5353 文件）",
          (bk3.get("archived") or {}).get("count", 0) > 0,
          f"archived={bk3.get('archived')}")
    froms3 = set()
    for it in d3.get("results") or []:
        froms3.update(it.get("from") or [])
        froms3.add(it.get("source") or "")
    check("B3d 融合结果真出现两源条目（假接入护栏：backends 绿但融合不可见=摆设）",
          {"workspace_files", "archived_sessions"} <= froms3, f"froms={froms3}")
    r4 = C.get("/api/kb/search", params={"q": "CCR", "routes": "ghost,workspace"})
    check("B3e 未知 routes 400 且回显可用值（含两个新路名）",
          r4.status_code == 400 and "workspace" in r4.text and "archived" in r4.text,
          r4.text[:120])
    st = C.get("/api/kb/status").json()
    check("B3f status 带两段且 available/count 有值",
          "workspace" in st and "archived" in st
          and st["workspace"].get("available") and st["archived"].get("available"),
          f"ws={st.get('workspace')} ar={st.get('archived')}")

    bad = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 58)
    print("总计 %d 项：PASS %d / FAIL %d" % (len(results), len(results) - len(bad), len(bad)))
    if bad:
        print("FAIL 明细: " + ", ".join(bad))
    print("=" * 58)
    return 1 if bad else 0


def _exec_count_ok() -> bool:
    """数一下还有没有在跑的 techdocs 子进程。超时分支必须真把子进程带走。"""
    n = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cl = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "replace")
        except OSError:
            continue
        if "techdocs_mcp" in cl:
            n += 1
    return n == 0


if __name__ == "__main__":
    sys.exit(main())
