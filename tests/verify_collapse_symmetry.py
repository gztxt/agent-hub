#!/usr/bin/env python3
"""L2：窄屏「点 agent 名称后自动收起侧栏」的**跨 origin 对称性**探针。

用户 09-23 23:3x 报："局域网点击 agent 名称后自动收起左侧菜单栏，只有终端的点击会话记录后
才自动收起；但是 Tailscale 好像不行，要手动点击一下右边才会收起。"

本探针只干一件事：**把到底是哪个变量让行为分叉测出来**（不许推理）。
四个格子 = {127.0.0.1:3102, 192.168.5.102:3102}（同一服务、两个 origin）
           × {hub.hist 空, hub.hist='claude'}（模拟"上次展开过终端历史"的存量差异）。
每格记录：mqNarrow / 点击前 collapsed / 点击后 collapsed / 点完存回的 hub.hist /
         hub.sidebar.narrow 值 / 历史行是否出现。

判据（对称性不变量）：**同档位下，点击同一 agent 名称后的 collapsed 结果不得依赖
该 origin 的 hub.hist 存量**；且两个 origin 在相同存量下必须给出相同结果。
"""
import json
import sys
import time
sys.path.insert(0, "/home/gztxt/agent-hub/tests")
from _cdp_min import CDP, launch_chrome, page_target

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9407
W, H = 390, 844
ORIGINS = ["http://127.0.0.1:3102", "http://192.168.5.102:3102"]
STATE_JS = ("JSON.stringify({narrow: matchMedia('(max-width: 767px)').matches,"
            " collapsed: document.getElementById('sidebar').classList.contains('collapsed'),"
            " stored: localStorage.getItem('hub.sidebar.narrow'),"
            " hist: localStorage.getItem('hub.hist'),"
            " hhRows: document.querySelectorAll('.hh-row').length,"
            " navItems: document.getElementById('navTree').children.length})")

proc = launch_chrome(ORIGINS[0], PORT, "/tmp/hub_sym", W, H)
time.sleep(2.5)
c = CDP(page_target(PORT))
c.send("Page.enable"); c.send("Runtime.enable")

def on_evt(method, params):
    if method == "Page.javascriptDialogOpening":
        try:
            c.send("Page.handleJavaScriptDialog", accept=False)
        except Exception:
            pass


c.on_event = on_evt   # 先挂钩子再 enable，否则窗口期内的对话框仍会挂死
c.send("Page.enable")

c.send("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=2, mobile=True)


def st():
    return json.loads(c.eval(STATE_JS) or "{}")


def goto(url, wait=3.2):
    c.send("Page.navigate", url=url)
    time.sleep(wait)


def entity_center(sel):
    raw = c.eval("(() => { const e = document.querySelector(%s); if (!e) return 'null';"
                 " const b = e.getBoundingClientRect();"
                 " return JSON.stringify([Math.round(b.left+b.width/2), Math.round(b.top+b.height/2),"
                 " Math.round(b.width), Math.round(b.height)]); })()" % json.dumps(sel))
    if not raw or raw == "null":
        return None
    x, y, w, h = json.loads(raw)
    return (x, y) if (0 <= x < W and 0 <= y < H and w > 0 and h > 0) else None


def click(x, y):
    for typ in ("mousePressed", "mouseReleased"):
        c.send("Input.dispatchMouseEvent", type=typ, x=x, y=y, button="left",
               clickCount=1, buttons=1 if typ == "mousePressed" else 0)
    time.sleep(1.0)


rows = []
for org in ORIGINS:
    for seed in (None, "claude"):
        goto(org + "/")                      # 不带 ?diag：面板的 fixed 层会压住侧栏，污染命中判定
        c.eval("localStorage.clear()")
        # 预置假 token：否则 termToken() 会弹原生口令框挂住 renderer（实测栽过一次）
        c.eval("localStorage.setItem('hub.term.token', 'SYMTEST')")
        if seed:
            c.eval("localStorage.setItem('hub.hist', %s)" % json.dumps(seed))
        goto(org + "/")
        # 真实路径：窄屏首屏是图标条 ⇒ 先点组图标展开侧栏（等价用户"点开菜单"）
        g = entity_center('button[data-group]')
        if not g:
            rows.append((org, seed, st(), None, "找不到组图标"))
            continue
        click(*g)
        opened = st()
        # 展开后再定位实体行；顺便把可用实体列出来（防止我瞎猜选择器）
        ents = c.eval("[...document.querySelectorAll('button[data-entity]')].map(e=>e.dataset.entity).join(',')") or ""
        sel = 'button[data-entity="claude"]' if "claude" in ents else (
            "button[data-entity]" if ents else None)
        if not sel:
            rows.append((org, seed, opened, None, "展开后仍无实体行（实体=%s）" % ents))
            continue
        p2 = entity_center(sel)
        if not p2:
            rows.append((org, seed, opened, None, "实体行在视口外（实体=%s）" % ents))
            continue
        before = st()
        click(*p2)
        after = st()
        rows.append((org, seed, before, after, "点 %s @(%d,%d)" % (sel.split('"')[1:-1], p2[0], p2[1])))

print("\n%-22s %-8s │ %-38s │ %-38s │ %s" % ("origin", "hub.hist", "点击前", "点击后", "备注"))
print("-" * 132)
for org, seed, b, a, note in rows:
    f = lambda d: ("narrow=%s collapsed=%s store=%s hist=%s hh=%s"
                   % (d.get("narrow"), d.get("collapsed"), d.get("stored"), d.get("hist"),
                      d.get("hhRows"))) if isinstance(d, dict) else str(d)
    print("%-22s %-8s │ %-38s │ %-38s │ %s" % (org.replace("http://", ""), seed or "(空)", f(b), f(a), note))

print("\n===== 对称性判定 =====")
bad = 0
for org in ORIGINS:
    pair = [(s, a) for (o, s, b, a, n) in rows if o == org and isinstance(a, dict)]
    if len(pair) == 2:
        same = pair[0][1]["collapsed"] == pair[1][1]["collapsed"]
        print("  %-20s hub.hist 空→collapsed=%s ／ ='claude'→collapsed=%s  ⇒ %s"
              % (org.replace("http://", ""), pair[0][1]["collapsed"], pair[1][1]["collapsed"],
                 "一致 PASS" if same else "★依赖存量 FAIL"))
        bad += 0 if same else 1
    else:
        print("  %-20s 数据不足（未测到两格）" % org.replace("http://", ""))
        bad += 1
proc.kill()
print("\n%s" % ("对称性成立 ✅" if not bad else "存在依赖 origin 存量的分叉 ❌"))
sys.exit(1 if bad else 0)
