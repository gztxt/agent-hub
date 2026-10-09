#!/usr/bin/env python3
"""终端框改前/改后截图 + 留白分解（配合 probe-term-box-gap.py）。

为什么单独要截图：用户口语的「上下左右边距」可能指两层完全不同的东西——
① 外层留白（main 的 padding，终端框到视口/侧栏之间）
② 黑底**内**部的留白（#termEl 到 xterm 画布之间）
②已被上一轮定案钉死为0（.term-body / #termEl 一律 padding:0，见 templates/index.html
  的FitAddon 铁律注释），所以本探针先出图让人眼确认「边距」是①，再动手。
"""
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/fs/1000/ftp/技术文档/agenthub/tests")
from _cdp_min import CDP, launch_chrome, page_target      # noqa: E402

BASE = "http://127.0.0.1:3102"
TAG = sys.argv[1] if len(sys.argv) > 1 else "before"
W, H = 1440, 900
PORT = 9381
OUT = Path("/fs/1000/ftp/技术文档/agenthub/work/shot-term-%s.png" % TAG)

SETUP = r"""
(() => {
  document.querySelectorAll('section.page').forEach(s => s.classList.remove('on'));
  const pg = document.getElementById('page-chat'); if (pg) pg.classList.add('on');
  ['embedPane','chatPane'].forEach(id => { const e = document.getElementById(id);
    if (e) e.classList.remove('on'); });
  const p = document.getElementById('termPane'); if (p) p.classList.add('on');
  const bar = document.getElementById('chatModeBar');
  if (bar) bar.style.display = 'none';
  // 造一点可见内容，便于肉眼分辨黑底边界
  const t = document.getElementById('termEl');
  if (t) { t.style.background = '#000';
    t.innerHTML = '<div style="color:#0f0;font:14px monospace;padding:8px">'
      + Array.from({length: 18}, (_, i) => '行' + (i + 1)
      + '  这是一条用于分辨黑底上下左右边界的探针文本 abcdefghijklmnop').join('<br>')
      + '</div>'; }
  return 'ok';
})()
"""

DECOMP = r"""
JSON.stringify((() => {
  const r = el => { if (!el) return null; const b = el.getBoundingClientRect();
    return {l: Math.round(b.left), t: Math.round(b.top), rt: Math.round(b.right),
            b: Math.round(b.bottom), w: Math.round(b.width), h: Math.round(b.height)}; };
  const pane = document.getElementById('termPane');
  const te = document.getElementById('termEl');
  const cm = document.querySelector('.chat-main');
  const grid = document.querySelector('#page-chat .chat-grid');
  const mw = document.getElementById('mainWrap');
  const hd = document.querySelector('.header');
  const sb = document.getElementById('sidebar');
  const P = ['paddingTop','paddingRight','paddingBottom','paddingLeft'];
  const cs = el => { if (!el) return null; const s = getComputedStyle(el); const o = {};
    P.forEach(k => o[k] = s[k]); o.gap = s.gap; o.maxW = s.maxWidth; return o; };
  const P1 = r(pane);
  return {
    header: r(hd), sidebar: r(sb), mainWrap: r(mw), grid: r(grid),
    chatMain: r(cm), pane: P1, termEl: r(te),
    // 留白逐层分解：谁贡献了终端框四周的空白
    padTop_header: hd && P1 ? P1.t - Math.round(hd.getBoundingClientRect().bottom) : -1,
    padTop_main: mw && P1 ? P1.t - Math.round(mw.getBoundingClientRect().top) : -1,
    padLeft_sidebar: sb && P1 ? P1.l - Math.round(sb.getBoundingClientRect().right) : -1,
    padLeft_main: mw && P1 ? P1.l - Math.round(mw.getBoundingClientRect().left) : -1,
    padBottom_main: mw && P1 ? Math.round(mw.getBoundingClientRect().bottom) - P1.b : -1,
    padRight_main: mw && P1 ? Math.round(mw.getBoundingClientRect().right) - P1.rt : -1,
    csMainWrap: cs(mw), csGrid: cs(grid), csChatMain: cs(cm),
    csTermEl: cs(te)
  };
})())
"""

proc = launch_chrome("about:blank", PORT, "/vol1/tmp/hub_shot_%s" % TAG, W, H)
cdp = CDP(page_target(PORT))
try:
    cdp.send("Emulation.setDeviceMetricsOverride", width=W, height=H,
             deviceScaleFactor=1, mobile=False)
    cdp.send("Page.navigate", url=BASE + "/")
    for _ in range(200):
        if cdp.eval("document.readyState") == "complete":
            break
        time.sleep(0.05)
    time.sleep(3.0)
    print("setup:", cdp.eval(SETUP))
    time.sleep(1.0)
    d = json.loads(cdp.eval(DECOMP) or "{}")
    for k in ("header", "sidebar", "mainWrap", "grid", "chatMain", "pane", "termEl"):
        print("  %-10s %s" % (k, d.get(k)))
    print("  留白分解：上(顶栏下缘→框) %s / 上(main内) %s / 左(侧栏右缘→框) %s / 左(main内) %s"
          % (d["padTop_header"], d["padTop_main"], d["padLeft_sidebar"], d["padLeft_main"]))
    print("           下(main内) %s / 右(main内) %s" % (d["padBottom_main"], d["padRight_main"]))
    print("  mainWrap %s" % d["csMainWrap"])
    print("  grid     %s" % d["csGrid"])
    print("  chatMain %s" % d["csChatMain"])
    print("  termEl   %s← 内边距必须四向0（FitAddon 铁律）" % d["csTermEl"])
    r = cdp.send("Page.captureScreenshot", format="png")
    OUT.write_bytes(base64.b64decode(r["data"]))
    print("\n截图 → %s (%d bytes)" % (OUT, OUT.stat().st_size))
    json.dump(d, open("/vol1/tmp/term_shot_%s.json" % TAG, "w"),
              ensure_ascii=False, indent=1)
finally:
    cdp.close()
    proc.terminate()