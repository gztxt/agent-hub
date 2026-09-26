#!/usr/bin/env python3
"""「GitHub 项目」页真渲染闸门（L2 live）：列远端仓库 → fork 开关 →
点仓库 → 选 agent → 新建会话 → 终端页；本地无的仓库走「克隆并开会话」。

跑法（打生产 :3102；克隆 E2E 默认关闭——生产 CLONE_BASE 不许探针写）：
    HUB_PROBE_TERM_TOKEN=<生产 token> venv/bin/python tests/verify_github_projects.py
    # 克隆链路（影子实例 + 沙箱 CLONE_BASE 才开）：
    #   PORT=3199 GITHUB_CLONE_BASE=/tmp/gh-probe-sandbox ... uvicorn 影子
    #   GITHUB_PROBE_CLONE=1 HUB_PROBE_TERM_TOKEN=probe-token \
    #     venv/bin/python tests/verify_github_projects.py http://127.0.0.1:3199

判据全部走真鼠标事件 + 服务端对账，不以截图交差：
  · R1 侧栏「GitHub 项目」按钮位于「本机项目」与 #navTree 之间
  · R2 点按钮 → #page-github 在屏、仓库行渲染（>0 行，非 fork 默认显示）
  · R3 fork 开关：勾选后可见行数 ≥ 未勾选（34 个 fork 从隐藏变可见）
  · R4 点 gztxt/agent-hub 行 → 选中态 .on + #ghSelName + 按钮解禁 + 文案「新建会话」
  · R5 选 codex + 新建会话 → 终端页 on + 服务端会话 cwd == 本地匹配路径
  · R6 克隆 E2E（GITHUB_PROBE_CLONE=1 才跑）：选一个 local.found=False 的仓 →
     按钮文案「克隆并开会话」→ 点后 CLONE_BASE 沙箱下出现新目录且会话 cwd 指向它
  · R7 会话清理 + 零 JS 错误
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target      # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3102"
TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
CDP_PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9398"))
DO_CLONE = os.getenv("GITHUB_PROBE_CLONE", "0") == "1"
W, H = 1280, 800
TARGET_REPO = "gztxt/agent-hub"          # 本地已有：测「直接开会话」路径
AGENT = "codex"
res, spawned_sessions = [], []


def chk(name, ok, detail=""):
    res.append(bool(ok))
    print("  %-4s %-56s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:160]))


def click(c, sel, need=True):
    raw = c.eval("(() => { const e = document.querySelector(%s); if (!e) return 'null';"
                 " const b = e.getBoundingClientRect();"
                 " if (!b.width || !b.height) return JSON.stringify([0,0,0]);"
                 " return JSON.stringify([Math.round(b.left+b.width/2), Math.round(b.top+b.height/2), 1]); })()"
                 % json.dumps(sel))
    if not raw or raw == "null":
        if need:
            chk("点击目标存在 " + sel, False, raw)
        return None, "元素不存在"
    x, y, vis = json.loads(raw)
    if not vis:
        return None, "元素零尺寸（display:none）"
    if not (0 <= x < W and 0 <= y < H):
        return None, "坐标 (%d,%d) 在视口外" % (x, y)
    for typ in ("mousePressed", "mouseReleased"):
        c.send("Input.dispatchMouseEvent", type=typ, x=x, y=y, button="left",
               clickCount=1, buttons=1 if typ == "mousePressed" else 0)
    time.sleep(0.9)
    return (x, y), "点 (%d,%d)" % (x, y)


def api(method, path, body=None):
    cmd = ["curl", "-s", "-m", "30", "-X", method, BASE + path,
           "-H", "X-TERM-TOKEN: " + TOKEN]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout or "{}"
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"_raw": out[:200]}


proc = None
c = None
try:
    print("=" * 78)
    print("GitHub 项目页：远端清单 → 选 agent → 新建会话 → 终端页   BASE=%s" % BASE)
    print("=" * 78)

    # 前置：API 有数据、目标仓本地已有、codex 有 term 入口
    d = api("GET", "/api/github/repos?force=1")
    chk("前置 /api/github/repos 有数据且 token 可用",
        d.get("count", 0) > 0 and d.get("token"), f"count={d.get('count')}")
    by_fn = {r["full_name"]: r for r in (d.get("repos") or [])}
    chk("前置 agent-hub 本地匹配", by_fn.get(TARGET_REPO, {}).get("local", {}).get("found"),
        by_fn.get(TARGET_REPO, {}).get("local"))
    agents = api("GET", "/api/agents").get("agents", [])
    ent = {a["id"]: [e["type"] for e in a.get("entries", [])] for a in agents}
    chk("前置 codex 有 term 入口", "term" in ent.get(AGENT, []), ent.get(AGENT))

    profile = tempfile.mkdtemp(prefix="hub_gh_probe_")
    proc = launch_chrome(BASE + "/", CDP_PORT, profile, W, H)
    time.sleep(3.0)
    c = CDP(page_target(CDP_PORT, tries=50))
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Page.addScriptToEvaluateOnNewDocument", source=(
        "window.__errs=[];addEventListener('error',e=>__errs.push(String(e.message)));"
        "addEventListener('unhandledrejection',e=>__errs.push('reject:'+String(e.reason).slice(0,120)));"))
    c.eval("localStorage.clear(); localStorage.setItem('hub.term.token', %s);" % json.dumps(TOKEN))
    c.send("Page.reload", ignoreCache=True)
    for _ in range(20):
        time.sleep(0.5)
        if c.eval("!!document.querySelector('#btnNavGithub')"):
            break

    # R1：按钮位置（本机项目 < GitHub < navTree）
    order = c.eval("(() => { const gh = document.querySelector('#btnNavGithub');"
                   " const lp = document.querySelector('#btnNavProjects');"
                   " const tree = document.querySelector('#navTree');"
                   " if (!gh || !lp || !tree) return null;"
                   " return (lp.compareDocumentPosition(gh) & Node.DOCUMENT_POSITION_FOLLOWING) &&"
                   "        (gh.compareDocumentPosition(tree) & Node.DOCUMENT_POSITION_FOLLOWING); })()")
    chk("R1 「GitHub 项目」在 本机项目 与 AGENTS 之间", bool(order), f"order={order}")

    # R2：点按钮 → 页面 + 行渲染
    click(c, "#btnNavGithub")
    for _ in range(30):
        time.sleep(0.5)
        rows = c.eval("document.querySelectorAll('#ghList .mem-item').length")
        if rows and int(rows) > 0:
            break
    page = c.eval("[...document.querySelectorAll('section.page.on')].map(s=>s.id).join(',')")
    rows = c.eval("document.querySelectorAll('#ghList .mem-item').length")
    chk("R2 点菜单进 GitHub 页且列表有行", page == "page-github" and int(rows or 0) > 0,
        f"page={page} rows={rows}")

    # R3：fork 开关
    base_rows = int(rows or 0)
    fork_rows = c.eval("(function(){ const cb=document.getElementById('ghForks');"
                       " cb.checked=true; cb.dispatchEvent(new Event('change'));"
                       " return document.querySelectorAll('#ghList .mem-item').length; })()")
    chk("R3 勾选显示 fork 后可见行数不减", int(fork_rows or 0) >= base_rows,
        f"{base_rows} → {fork_rows}")
    # 收回去（后续过滤目标仓更稳）
    c.eval("document.getElementById('ghForks').checked=false;"
           " document.getElementById('ghForks').dispatchEvent(new Event('change'));")

    # R4：点 agent-hub 行（过滤定位）
    c.eval("document.getElementById('ghQ').value = %s;"
           " ghRenderList();" % json.dumps("agent-hub"))
    time.sleep(0.3)
    idx = c.eval("(() => { const items=[...document.querySelectorAll('#ghList .mem-item')];"
                 " for (let i=0;i<items.length;i++){"
                 "   if ((items[i].title||'') === %s) return items[i].dataset.i; }"
                 " return null; })()" % json.dumps(TARGET_REPO))
    chk("R4a 目标仓库行存在", idx is not None, f"idx={idx}")
    if idx is not None:
        click(c, f"#ghList .mem-item[data-i='{idx}']")
        st = c.eval("(() => { const sel=document.querySelector('#ghList .mem-item.on');"
                    " const btn=document.getElementById('ghStartBtn');"
                    " return JSON.stringify({ on: !!sel,"
                    " name: document.getElementById('ghSelName').textContent,"
                    " disabled: btn.disabled, label: btn.lastChild.textContent }); })()")
        s = json.loads(st)
        chk("R4b 选中态 + 名称上屏 + 按钮解禁 + 文案「新建会话」",
            s["on"] and s["name"] == TARGET_REPO and not s["disabled"]
            and "新建会话" in str(s["label"]), s)

    # R5：选 codex → 新建会话 → 终端页 + cwd 对账（本地匹配路径）
    has_codex = c.eval(f"!!document.querySelector('#ghAgent option[value=\"{AGENT}\"]')")
    chk("R5a agent 下拉含 codex", bool(has_codex))
    c.eval(f"document.getElementById('ghAgent').value = {json.dumps(AGENT)};")
    local_path = by_fn[TARGET_REPO]["local"]["path"]
    click(c, "#ghStartBtn")
    for _ in range(20):
        time.sleep(0.5)
        if c.eval("(document.querySelector('#termPane')||{classList:{contains:()=>false}}).classList.contains('on')"):
            break
    term_on = c.eval("document.querySelector('#termPane').classList.contains('on')")
    sess = api("GET", "/api/term/sessions").get("sessions", [])
    mine = [s for s in sess if s.get("agent_id") == AGENT and s.get("cwd") == local_path]
    chk("R5 点新建会话 → 终端页 on + pty cwd == 本地匹配路径",
        bool(term_on) and bool(mine), f"term_on={term_on} matched={len(mine)}")
    if mine:
        spawned_sessions = [s["id"] for s in mine]
        screen = c.eval("(() => { try { let s=''; for (let i=0;i<term.rows;i++){"
                        " const l=term.buffer.active.getLine(i); if(l) s+=l.translateToString(true); }"
                        " return s.trim().length; } catch(e){ return -1; } })()")
        chk("R5b xterm 屏幕缓冲非空", int(screen or 0) > 0, f"chars={screen}")

    # R6：克隆 E2E（默认关；须影子实例 + 沙箱 CLONE_BASE）
    if DO_CLONE:
        # 找一个本地无的非 fork 仓
        no_local = next((r for r in (d.get("repos") or [])
                         if not r["fork"] and not r["local"]["found"]), None)
        chk("R6 前置存在本地无的仓库", no_local is not None,
            no_local and no_local["full_name"])
        if no_local:
            c.eval("go('github'); document.getElementById('ghQ').value=''; ghRenderList();")
            time.sleep(0.3)
            c.eval("document.getElementById('ghForks').checked=false;"
                   " document.getElementById('ghForks').dispatchEvent(new Event('change'));")
            idx2 = c.eval("(() => { const items=[...document.querySelectorAll('#ghList .mem-item')];"
                          " for (let i=0;i<items.length;i++){"
                          "   if ((items[i].title||'') === %s) return items[i].dataset.i; }"
                          " return null; })()" % json.dumps(no_local["full_name"]))
            if idx2 is not None:
                click(c, f"#ghList .mem-item[data-i='{idx2}']")
                label = c.eval("document.getElementById('ghStartBtn').lastChild.textContent")
                chk("R6a 本地无 → 按钮文案「克隆并开会话」", "克隆" in str(label), label)
                c.eval(f"document.getElementById('ghAgent').value = {json.dumps(AGENT)};")
                click(c, "#ghStartBtn")
                # 克隆 + 开会话可能要数十秒
                for _ in range(90):
                    time.sleep(1.0)
                    if c.eval("(document.querySelector('#termPane')||{classList:{contains:()=>false}}).classList.contains('on')"):
                        break
                sess2 = api("GET", "/api/term/sessions").get("sessions", [])
                fresh = [s for s in sess2 if s.get("agent_id") == AGENT
                         and s.get("cwd", "").endswith("/" + no_local["name"])]
                chk("R6b 克隆并开会话 → 新目录 cwd",
                    bool(fresh), [s.get("cwd") for s in fresh])
                spawned_sessions += [s["id"] for s in fresh]

    errs = c.eval("(window.__errs||[]).slice(0,5)")
    chk("R7 页面零 JS 错误", not errs, errs)
finally:
    for sid in spawned_sessions:
        r = api("DELETE", "/api/term/sessions/" + sid)
        print("  清理会话 %s：%s" % (sid, r.get("status", r)))
    if c:
        try:
            c.ws.close()
        except Exception:
            pass
    if proc:
        proc.terminate()

n = len(res)
ok = sum(res)
print("=" * 78)
print("RESULT: %d/%d PASS" % (ok, n))
sys.exit(0 if ok == n and n else 1)
