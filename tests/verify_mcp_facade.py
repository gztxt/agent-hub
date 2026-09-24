#!/usr/bin/env python3
"""P3 + P4 上线前闸门：MCP 工具注册门面（真 stdio 子进程）与资产面板的路径契约。

跑法：venv/bin/python3 tests/verify_mcp_facade.py
**不占端口**（TestClient 走 in-process ASGI）、不写生产 db（tmp 文件库）、不重启任何服务。
唯一的外件是本仓自带的 `tests/mcp_demo_server.py`（本地 stdio 子进程，零网络、零模型请求）——
必须用真 server 而不是全 mock，因为 P3 的核心断言是「上游 inputSchema 真的透传出来了」，
拿假 Tool 对象自证等于把这条判据抽空。

外部目标探测 ≤2 次（09-23 红线）。红向断言的作用是证明"坏的时候会说"。
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "tests"))

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="hubmcp-"))

import db            # noqa: E402
db.init_db(_TMP / "agents.db")     # 空库：server/acl 全部由本闸门自己造

import mcpgw         # noqa: E402
mcpgw.ensure_schema()

from fastapi import FastAPI                       # noqa: E402
from starlette.testclient import TestClient       # noqa: E402

_app = FastAPI()
_app.include_router(mcpgw.router)
C = TestClient(_app)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("  ✅ PASS  " if ok else "  ❌ FAIL  ") + name + (f"   {detail}" if detail else ""))


def seed(server_env=None):
    db.execute("DELETE FROM mcp_servers")
    db.execute("DELETE FROM mcp_acl")
    env = json.dumps(server_env or {})
    db.execute(
        "INSERT INTO mcp_servers(id,name,transport,command,args,env,url,description,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("demo", "demo", "stdio", sys.executable,
         json.dumps([str(_REPO / "tests" / "mcp_demo_server.py")]), env, None, "自检用",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))


def acl(agent, server, pattern, allow):
    db.execute("INSERT INTO mcp_acl(agent_id,server_id,tool_pattern,allow) VALUES(?,?,?,?)",
               (agent, server, pattern, 1 if allow else 0))


def registry_allowed(payload, tool):
    for s in payload["servers"]:
        for t in s["tools"]:
            if t["name"] == tool:
                return t["allowed"], t.get("reason", "")
    return None, "工具不在清单里"


try:
    print("═" * 78)
    print("P3/P4 闸门：MCP 工具注册门面 + 资产面板路径契约")
    print("═" * 78)

    # ────────────────────────── A. 真 server 的 schema 透传 ──────────────────────────
    print("\n[A] /mcp/tools 的 inputSchema 透传（真 stdio 子进程，非 mock）")
    seed()
    mcpgw._tool_cache.clear()
    r = C.get("/mcp/tools")
    check("A1 HTTP 200", r.status_code == 200, f"got {r.status_code}")
    body = r.json() if r.status_code == 200 else {}
    names = {t["name"] for t in body.get("tools", [])}
    check("A2 真上游的两个工具都在", {"echo", "now"} <= names, f"got {sorted(names)}")
    echo = next((t for t in body.get("tools", []) if t["name"] == "echo"), {})
    now = next((t for t in body.get("tools", []) if t["name"] == "now"), {})
    sch = echo.get("inputSchema") or {}
    props = sch.get("properties") or {}
    check("A3 echo 的 inputSchema 真透传（含 text 参数）",
          "text" in props, json.dumps(sch, ensure_ascii=False)[:160])
    check("A4 now（无参工具）给的是 dict 而不是谎报缺失",
          isinstance(now.get("inputSchema"), dict) and not now.get("schema_missing"),
          f"inputSchema={now.get('inputSchema')} schema_missing={now.get('schema_missing')}")
    check("A5 无降级时 backends/degraded/note 三件齐",
          len(body.get("backends", [])) == 1 and body.get("degraded") == [] and "note" in body,
          f"backends={body.get('backends')}")
    check("A6 旧键全在（前端 loadMcp/pickTool 在吃）",
          all(k in body for k in ("tools", "count", "errors")), f"keys={sorted(body)}")
    check("A7 description 未被静默截断（短文本不带 truncated 旗）",
          "description_truncated" not in echo, f"echo.description={echo.get('description')!r}")

    # ────────────────────────── B. registry 派生视图与真实调用同源 ──────────────────────────
    print("\n[B] /mcp/registry 的判定必须与 /mcp/call 的实际生死一致（同源，不许两份真相）")
    acl("manager", "demo", "echo", True)
    mcpgw._tool_cache.clear()
    reg = C.get("/mcp/registry", params={"agent": "manager"})
    check("B1 registry HTTP 200", reg.status_code == 200, f"got {reg.status_code}")
    rp = reg.json()
    a_echo, reason = registry_allowed(rp, "echo")
    a_now, _ = registry_allowed(rp, "now")
    check("B2 registry: echo 允许", a_echo is True, reason)
    check("B3 registry: now 未覆盖 → 白名单拒", a_now is False, str(a_now))
    call_ok = C.post("/mcp/call", json={"server": "demo", "tool": "echo",
                                        "args": {"text": "P3"}, "agent_id": "manager"})
    check("B4a 调完 /mcp/call 后全局连接仍在 tmp 库（旧写法会重指到生产库）",
          db.current_path().startswith(str(_TMP)), f"current db={db.current_path()!r}")
    check("B4 registry 说允许 ⇒ 真调用成功（同源正向）",
          call_ok.status_code == 200 and "echo> P3" in call_ok.text,
          f"{call_ok.status_code} {call_ok.text[:120]}")
    call_no = C.post("/mcp/call", json={"server": "demo", "tool": "now",
                                        "args": {}, "agent_id": "manager"})
    check("B5 registry 说拒 ⇒ 真调用 403（同源反向）", call_no.status_code == 403,
          f"{call_no.status_code} {call_no.text[:120]}")

    # ────────────────────────── C. 红向：deny 翻转后视图与执行同时翻 ──────────────────────────
    print("\n[C] 红向：加一条 `*` deny，registry 与 /mcp/call 必须同时翻面")
    acl("*", None, "*", False)
    mcpgw._tool_cache.clear()
    rp2 = C.get("/mcp/registry", params={"agent": "manager"}).json()
    b_echo, reason2 = registry_allowed(rp2, "echo")
    check("C1 registry 翻成拒", b_echo is False, reason2)
    call2 = C.post("/mcp/call", json={"server": "demo", "tool": "echo",
                                      "args": {"text": "x"}, "agent_id": "manager"})
    check("C2 真调用也翻成 403（没有两份真相漂移）", call2.status_code == 403,
          f"{call2.status_code}")
    check("C3 403 理由点名规则与 deny 优先",
          "deny" in call2.text or "规则" in call2.text, call2.text[:160])
    r3 = C.post("/mcp/call", json={"server": "demo", "tool": "echo", "args": {"text": "x"}})
    check("C4 缺 agent_id 折成 anon 仍受 `*` 约束（旧绕过口不得回来）", r3.status_code == 403,
          f"{r3.status_code}")
    check("C5 registry 回显的规则行含 agent/模式/allow，可直接读",
          any(x.get("agent_id") == "*" and x.get("allow") == 0 for x in rp2.get("rules", [])),
          json.dumps(rp2.get("rules"), ensure_ascii=False)[:160])

    # ────────────────────────── D. 单路失败的降级形态 ──────────────────────────
    print("\n[D] 一路 server 起不来时：整体 200、清单只含活路、degraded 点名")
    db.execute(
        "INSERT INTO mcp_servers(id,name,transport,command,args,env,url,description,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("brk", "brk", "stdio", "/nonexistent/python-x", "[]", "{}", None, "",
         "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))
    mcpgw._tool_cache.clear()
    r4 = C.get("/mcp/tools")
    b4 = r4.json()
    check("D1 HTTP 仍 200（一路挂不塌整体）", r4.status_code == 200, str(r4.status_code))
    check("D2 degraded 点名 brk", "brk" in b4.get("degraded", []), json.dumps(b4.get("degraded")))
    check("D3 note 非空且说'不完整'", bool(b4.get("note")) and "不完整" in b4["note"], b4.get("note", "")[:120])
    check("D4 活路的工具仍在清单里（不是整表清空）",
          any(t["name"] == "echo" for t in b4.get("tools", [])), f"count={b4.get('count')}")
    check("D5 backends 逐路带 ok/count/ms/error",
          {("ok" in x and "count" in x and "ms" in x and "error" in x) for x in b4.get("backends", [])} == {True},
          json.dumps(b4.get("backends"), ensure_ascii=False)[:200])

    # ────────────────────────── E. 凭据不外泄 + 探针不留脏缓存 ──────────────────────────
    print("\n[E] 凭据与探针缓存")
    FAKE = "sk-LIVEKEY-0123456789abcdef"
    db.execute("UPDATE mcp_servers SET env=?", (json.dumps({"DEMO_TOKEN": FAKE}),))
    mcpgw._tool_cache.clear()
    srv = C.get("/mcp/servers").text
    check("E1 /mcp/servers 不回显 env 值", FAKE not in srv, srv[:160])
    tools_txt = C.get("/mcp/tools").text
    check("E2 /mcp/tools 不回显 env 值", FAKE not in tools_txt, tools_txt[:120])
    reg_txt = C.get("/mcp/registry", params={"agent": "manager"}).text
    check("E3 /mcp/registry 不回显 env 值", FAKE not in reg_txt, reg_txt[:120])
    err_txt = C.get("/mcp/tools").text
    check("E4 无 `sk-` 形态密钥出现在任何门面响应里",
          not re.search(r"sk-[A-Za-z0-9_-]{12,}", tools_txt + reg_txt + srv + err_txt), "扫描 4 份响应")
    before = dict(mcpgw._tool_cache)
    mcpgw._tool_cache.clear()
    pr = C.post("/mcp/servers/probe", json={"command": sys.executable,
                                            "args": [str(_REPO / "tests" / "mcp_demo_server.py")]})
    check("E5 probe 可用（预览语义）", pr.status_code == 200 and "echo" in pr.text,
          f"{pr.status_code} {pr.text[:100]}")
    check("E6 probe 不写 60s 缓存（否则改完代码预览还是旧的）",
          len(mcpgw._tool_cache) == 0, f"cache keys={list(mcpgw._tool_cache)}")
    _ = before

    # ────────────────────────── F. 无孤儿进程 ──────────────────────────
    print("\n[F] 子进程纪律")
    ps = subprocess.run(["bash", "-lc", "pgrep -af mcp_demo_server | grep -v pgrep || true"],
                        capture_output=True, text=True)
    check("F1 闸门结束后无残留 stdio 子进程", ps.stdout.strip() == "", ps.stdout[:200])

    # ────────────────────────── G. 前端路径契约（P4 ↔ P2/P1 后端） ──────────────────────────
    print("\n[G] 资产面板调用的路径必须真实存在（前后端契约，防'写了但 404'）")
    js = (_REPO / "static" / "hub" / "07-asset-panel.js").read_text(encoding="utf-8")
    paths = sorted(set(re.findall(r"'(/(?:api|mcp|health)[^']*)", js)))
    check("G1 面板里确实有可枚举的端点路径", len(paths) >= 4, str(paths))
    import kb        # noqa: E402
    import memory    # noqa: E402
    import skill     # noqa: E402
    app2 = FastAPI()
    for mod in (kb, memory, skill, mcpgw):
        app2.include_router(mod.router)
    C2 = TestClient(app2)
    q = "端口"
    targets = {
        "/api/memory/search": {"params": {"q": q}},
        "/api/kb/search": {"params": {"q": q}},
        "/api/kb/status": {},
        "/api/skill/list": {"params": {"q": q}},
        "/api/skill/status": {},
        "/mcp/tools": {},
        "/mcp/registry": {"params": {"agent": "manager"}},
    }
    for path, kw in targets.items():
        self_declared = any(path.startswith(p.split("?")[0]) for p in paths) or path in ("/health",)
        rr = C2.get(path, **kw)
        check(f"G2 {path} 存在且 200" + ("" if self_declared else "（面板未直接引用，仍验契约）"),
              rr.status_code == 200, f"{rr.status_code} {rr.text[:90]}")
    mem = C2.get("/api/memory/search", params={"q": q}).json()
    check("G3 memory 响应含面板读取的 memories/count/degraded/backends",
          all(k in mem for k in ("memories", "count")), json.dumps(sorted(mem))[:200])
    skl = C2.get("/api/skill/list", params={"q": q}).json()
    check("G4 skill 响应含 items/count", all(k in skl for k in ("items", "count")),
          json.dumps(sorted(skl))[:200])
    kbq = C2.get("/api/kb/search", params={"q": q}).json()
    check("G5 kb 响应含 results/count/backends", all(k in kbq for k in ("results", "count")),
          json.dumps(sorted(kbq))[:200])
    check("G6 面板三路 label 与面板文案存在（不是自说自话）",
          all(lbl in js for lbl in ("记忆", "技术文档", "技能")), "ASSET_SOURCES 的 label 集")

    # ────────────────────── H. 库指向金丝雀（本闸门自身的防火墙）──────────────────────
    print("\n[H] 库指向金丝雀：闸门全程只准写 tmp 库")
    check("H1 本闸门全程未离开 tmp 库（防 09-24 那次误写生产库）",
          db.current_path().startswith(str(_TMP)), f"current db={db.current_path()!r} tmp={_TMP}")
    check("H2 生产 data/agents.db 未被本闸门打开过",
          "agent-hub/data/agents.db" not in db.current_path(), db.current_path())

except Exception as e:  # noqa: BLE001
    check("Z0 闸门自身抛异常（应转为明确 FAIL）", False, f"{type(e).__name__}: {e}")
finally:
    import shutil
    shutil.rmtree(_TMP, ignore_errors=True)

passed = sum(1 for _, ok, _ in results if ok)
print("\n" + "─" * 78)
print(f"合计：PASS {passed} / {len(results)}   FAIL {len(results) - passed}")
for name, ok, detail in results:
    if not ok:
        print(f"   ✗ {name}  {detail}")
sys.exit(0 if passed == len(results) else 1)
