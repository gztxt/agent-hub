#!/usr/bin/env python3
"""hub.page 持久化值必须校验后回退（390×844，真页面，带退出码的闸门）。

为什么盯它：`go(localStorage.getItem('hub.page') || 'classroom')` 是启动最后一句，
而 09-20「界面统一」重构改过页面名。**localStorage 是按 origin 隔离的**：
局域网那个源存的是合法页名（正常），Tailscale 那个源可能存着老页名 ⇒
`go()` 把所有 section.page 的 on 全关掉 ⇒ 正文空白、看起来完全点不动。

这正好是"同一份代码、两个地址、一个正常一个异常"的唯一免猜解释，逐名验。
"""
import json, sys, time
sys.path.insert(0, "/home/gztxt/agent-hub/tests")
from _cdp_min import CDP, launch_chrome, page_target

BASE, PORT = "http://127.0.0.1:3102", int(sys.argv[1]) if len(sys.argv) > 1 else 9381
VW, VH = 390, 844

MEAS = r"""
(() => {
  const key = el => !el ? 'none' : (el.id ? '#'+el.id : el.tagName.toLowerCase() +
      (typeof el.className==='string' && el.className ? '.'+el.className.split(/\s+/)[0]:''));
  const onPages = [...document.querySelectorAll('section.page.on')].map(e => e.id);
  const sb = document.getElementById('sidebar');
  const r = sb.getBoundingClientRect();
  // 正文取样的三个点：中上、正中、中下（避开左侧抽屉，看正文区到底有没有东西）
  const xs = [Math.round(r.right + (innerWidth - r.right) * 0.5)];
  const hits = [0.25, 0.5, 0.75].map(f => key(document.elementFromPoint(xs[0], Math.round(innerHeight * f))));
  const ccx = Math.round((r.right + innerWidth) / 2), ccy = Math.round(innerHeight / 2);
  const inter = document.elementFromPoint(ccx, ccy);
  const cs = inter ? getComputedStyle(inter) : null;
  let visBtn = 0;
  for (const b of document.querySelectorAll('section.page.on button, section.page.on a')) {
    const bb = b.getBoundingClientRect();
    if (bb.width > 4 && bb.height > 4 && bb.bottom > 0 && bb.top < innerHeight) visBtn++;
  }
  return { onPages, centerHit: key(inter), centerVisible: cs ? (cs.display !== 'none' && cs.visibility !== 'hidden') : false,
           sampleHits: hits, visControlsInPage: visBtn,
           sidebarCollapsed: sb.classList.contains('collapsed'),
           navBtns: [...sb.querySelectorAll('button')].filter(e => { const b = e.getBoundingClientRect(); return b.width > 4 && b.height > 4; }).length,
           bodyTextLen: (document.querySelector('section.page.on')||{innerText:''}).innerText.trim().length,
           errs: (window.__errs || []).slice(0, 3) };
})()
"""

CANDS = [("合法 classroom（基线）", "classroom"), ("合法 overview 别名?", "overview"),
         ("老页名 dashboard", "dashboard"), ("老页名 manager", "manager"),
         ("老页名 agents", "agents"), ("老页名 watch", "watch"),
         ("老页名 terminal", "terminal"), ("空串", ""), ("乱写", "zzz-nope")]

proc = launch_chrome(BASE + "/", PORT, "/tmp/hub_page", VW, VH)
time.sleep(3.0)
c = CDP(page_target(PORT)); c.send("Page.enable"); c.send("Runtime.enable")
c.send("Page.addScriptToEvaluateOnNewDocument", source=(
    "window.__errs=[];addEventListener('error',e=>__errs.push(String(e.message)));"
    "addEventListener('unhandledrejection',e=>__errs.push('reject:'+String(e.reason).slice(0,120)));"))

bad = []
print("%-24s %-22s %-16s %-9s %s" % ("hub.page 取值", "section.page.on", "正文中心命中", "页内控件", "判定"))
for name, val in CANDS:
    c.eval("localStorage.clear();localStorage.setItem('hub.page', %s);" % json.dumps(val))
    c.send("Emulation.setDeviceMetricsOverride", width=VW, height=VH, deviceScaleFactor=2, mobile=True)
    c.send("Page.reload", ignoreCache=True)
    d = None
    for _ in range(16):
        time.sleep(0.4)
        d = c.eval(MEAS)
        if d and d.get("onPages") is not None:
            break
    verdict = []
    if not d["onPages"]:
        verdict.append("★正文无任何页面=空白")
    if d["visControlsInPage"] == 0:
        verdict.append("页内可点数=0")
    if d["errs"]:
        verdict.append("JS异常")
    flag = "; ".join(verdict) or "OK"
    if not d["onPages"] or d["visControlsInPage"] == 0:
        bad.append("%s → onPages=%s 中心=%s" % (name, d["onPages"], d["centerHit"]))
    print("%-24s %-22s %-16s %-9d %s" % (name, ",".join(d["onPages"]) or "(无)",
                                          d["centerHit"], d["visControlsInPage"], flag))
    for e in d["errs"]:
        print("      JS: " + e)

# ── 收口为闸门：任何 hub.page 取值都必须有页面显示出来、且有可点元素 ──
print()
for name, val in CANDS:
    ok = val in ("", "classroom")  # 仅记录用途，真正判据在 bad
print("%s  (%d 个取值中招 / 共 %d 个)" % (
    "全部 hub.page 取值都能落到一个有内容的页面上 ✅" if not bad
    else "存在让正文空白的取值 ❌" , len(bad), len(CANDS)))
for b in bad:
    print("   FAIL: " + b)
proc.kill()
sys.exit(1 if bad else 0)
