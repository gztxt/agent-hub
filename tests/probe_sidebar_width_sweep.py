#!/usr/bin/env python3
"""宽度扫描：找出「左侧菜单栏空白」发生在哪个视口带。

只测 390px 一个点是我的取证缺口 —— 空白必然来自某个宽度/状态组合，
在轴上扫一遍才能定位，而不是继续猜。

每个宽度取：侧栏矩形与是否 fixed、可见按钮数、navTree 子项与文字长度、
侧栏中心点被谁接住（是否被别的层盖掉）、页面 JS 异常。
"""
import json, sys, time
sys.path.insert(0, "/home/gztxt/agent-hub/tests")
from _cdp_min import CDP, launch_chrome, page_target

BASE = "http://127.0.0.1:3102"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9349
WIDTHS = [320, 360, 390, 412, 480, 600, 767, 768, 769, 820, 900, 1000, 1280, 1920]

HOOK = r"""
window.__errs = [];
window.addEventListener('error', e => __errs.push('error: ' + (e.message || '?')));
window.addEventListener('unhandledrejection', e => __errs.push('reject: ' + String(e.reason).slice(0,140)));
"""

MEAS = r"""
(() => {
  const sb = document.getElementById('sidebar');
  if (!sb) return {err: 'no sidebar'};
  const r = sb.getBoundingClientRect(), cs = getComputedStyle(sb);
  let vis = 0, items = 0;
  for (const el of sb.querySelectorAll('button')) {
    const b = el.getBoundingClientRect();
    if (b.width > 4 && b.height > 4) { vis++; if (el.classList.contains('nav-item')) items++; }
  }
  const cx = Math.round(r.left + r.width / 2), cy = Math.round(r.top + Math.min(r.height, innerHeight) / 2);
  const hit = r.width > 0 && r.height > 0 ? document.elementFromPoint(cx, Math.min(cy, innerHeight - 2)) : null;
  const nt = document.getElementById('navTree');
  const key = el => !el ? 'none' : (el.id ? '#' + el.id : el.tagName.toLowerCase() +
      (typeof el.className === 'string' && el.className ? '.' + el.className.split(/\s+/)[0] : ''));
  return { vw: innerWidth, collapsed: sb.classList.contains('collapsed'),
           w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left), pos: cs.position,
           disp: cs.display, visBtns: vis, visItems: items,
           ntKids: nt ? nt.children.length : -1, ntText: nt ? (nt.innerText||'').trim().length : -1,
           centerHit: key(hit), covered: !!(hit && !(hit === sb || sb.contains(hit))),
           errs: (window.__errs || []).slice(0, 3) };
})()
"""

proc = launch_chrome(BASE + "/", PORT, "/tmp/hub_sweep", 1280, 800)
time.sleep(3.0)
c = CDP(page_target(PORT))
c.send("Page.enable"); c.send("Runtime.enable")
c.send("Page.addScriptToEvaluateOnNewDocument", source=HOOK)

for state, setup in (("全新", "localStorage.clear();"),
                     ("存量污染(legacy=0)", "localStorage.clear();localStorage.setItem('hub.sidebar','0');")):
    print("\n########## 状态：%s ##########" % state)
    print("%-6s %-6s %-5s %-5s %-9s %-8s %-7s %-9s %s"
          % ("宽度", "收起", "宽px", "高px", "定位", "可见按钮", "列表项", "中心接住", "判定"))
    for W in WIDTHS:
        c.eval("localStorage.clear();")
        c.eval(setup)
        c.send("Emulation.setDeviceMetricsOverride", width=W, height=844,
               deviceScaleFactor=2, mobile=(W < 768))
        c.send("Page.reload", ignoreCache=True)
        d = None
        for _ in range(16):
            time.sleep(0.4)
            d = c.eval(MEAS)
            if d and not d.get("err") and d.get("ntKids", -1) >= 0:
                break
        if not d or d.get("err"):
            print("%-6d 取不到" % W); continue
        verdict = []
        if d["w"] <= 0 or d["h"] <= 0 or d["disp"] == "none":
            verdict.append("侧栏不可见")
        if d["covered"]:
            verdict.append("被 %s 盖住" % d["centerHit"])
        if d["visBtns"] == 0:
            verdict.append("无任何可见项=空白")
        if d["errs"]:
            verdict.append("JS异常")
        print("%-6d %-6s %-5s %-5s %-9s %-8d %-7d %-9s %s"
              % (W, "收起" if d["collapsed"] else "展开", d["w"], d["h"], d["pos"],
                 d["visBtns"], d["visItems"], d["centerHit"],
                 ("; ".join(verdict) or "OK")))
        if d["errs"]:
            for e in d["errs"]:
                print("        JS: " + e)
proc.kill()
