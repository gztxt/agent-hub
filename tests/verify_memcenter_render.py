#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆中心 A~E 真渲染取证（记忆中心 A~E 真渲染闸门（04 档宽度 · 09-24 定案「字节一致 ≠ 页面正常」））
只回答「改版后的页面在 4 档宽度下是否真的渲染出实测数据、且无横向滚动」。
判据全部是可断言的量；不把「HTTP 200」或「字节一致」当作页面正常的证据。
用法: venv/bin/python tests/verify_memcenter_render.py
"""
import json, sys, time
sys.path.insert(0, "/fs/1000/ftp/技术文档/agent-hub/tests")
from _cdp_min import CDP, launch_chrome, page_target

PORT = 9409
URL = "http://127.0.0.1:3102"
WIDTHS = [320, 375, 414, 768]

READ = """JSON.stringify((() => {
  const t = id => { const e = document.getElementById(id); return e ? e.textContent.trim() : '<<missing>>'; };
  return {
    src: t('mxMSrc'), items: t('mxMItems'), slow: t('mxMSlow'), search: t('mxMSearch'),
    srcHint: t('mxSrcHint'), leadHint: t('mxLeadHint'), leadNote: t('mxLeadNote'),
    hits: document.querySelectorAll('#mxHits .mx-hit').length,
    hit0: (document.querySelector('#mxHits .mx-hit .hint') || {}).textContent || '',
    srcRows: document.querySelectorAll('#mxSrcList .mx-src').length,
    srcDotOK: document.querySelectorAll('#mxSrcList .mx-dot.ok').length,
    srcDotBad: document.querySelectorAll('#mxSrcList .mx-dot.bad').length,
    ctxLen: (document.getElementById('ctxBody') || {}).textContent.length || 0,
    ctxHead: (document.getElementById('ctxBody') || {}).textContent.slice(0, 46) || '',
    stale: (document.getElementById('mxStale') || {}).textContent.trim().slice(0, 190) || '',
    cards: document.querySelectorAll('#page-memory .sp-card.mx-card').length,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    errs: window.__mcErr || ''
  };
})())"""

proc = launch_chrome(URL, PORT, "/tmp/hub_mc_probe", WIDTHS[0], 844)
time.sleep(2.5)
c = CDP(page_target(PORT))
errs = []
c.on_event = lambda m, p: (errs.append(f"{m}:{str(p)[:90]}")
                           if m == "Runtime.exceptionThrown" else None)
c.send("Page.enable"); c.send("Runtime.enable")
time.sleep(0.3)

print("=" * 72)
fails = []
for w in WIDTHS:
    c.send("Emulation.setDeviceMetricsOverride", width=w, height=844,
           deviceScaleFactor=2, mobile=(w <= 767))
    c.send("Page.navigate", url=URL)
    time.sleep(3.0)
    c.eval("(() => { window.__mcErr='';"
           "addEventListener('error', e => { window.__mcErr += (e.message||'') + ' | '; });"
           "try { go('memory'); } catch (err) { window.__mcErr += 'go():' + err.message; }"
           "return 1; })()")
    # 等预检真跑完：源健康与注入包都由实测响应填充，最长等 12s
    for _ in range(40):
        time.sleep(0.3)
        raw = c.eval("JSON.stringify({s:document.getElementById('mxMSrc').textContent,"
                     "n:document.querySelectorAll('#mxSrcList .mx-src').length,"
                     "c:document.getElementById('ctxBody').textContent.length})")
        d = json.loads(raw or "{}")
        if d.get("s") not in ("—", "") and d.get("n", 0) > 0 and d.get("c", 0) > 0:
            break
    r = json.loads(c.eval(READ) or "{}")
    row = []
    ok = True
    for key, val, want in [
        ("源健康行数", r.get("srcRows"), 10), ("可用源点", r.get("srcDotOK"), None),
        ("注入包字符", r.get("ctxLen"), None), ("命中行数", r.get("hits"), None),
        ("卡片数", r.get("cards"), 6), ("横向溢出", r.get("overflow"), 0),
    ]:
        good = (val == want) if want is not None else (bool(val) or val == 0)
        if key in ("可用源点", "注入包字符", "命中行数"):
            good = (val or 0) > 0
        ok = ok and good
        row.append(f"{key}={val}{'' if good else ' ✗'}")
    if r.get("src") in ("—", "<<missing>>") or r.get("errs"):
        ok = False
    print(f"[{'PASS' if ok else 'FAIL'}] {w}px  " + " · ".join(row))
    if w == WIDTHS[0]:
        print(f"        总览：{r.get('src')} 源 · {r.get('items')} 条 · 最慢 {r.get('slow')} · 检索 {r.get('search')}")
        print(f"        源说明：{r.get('srcHint')}")
        print(f"        引擎行：{r.get('leadNote')}")
        print(f"        命中首行：{(r.get('hit0') or '').strip()}")
        print(f"        注入包头：{(r.get('ctxHead') or '').replace(chr(10), ' ⏎ ')}")
        print(f"        陈旧标识：{(r.get('stale') or '')}")
    if not ok:
        fails.append(w)
    if r.get("errs"):
        fails.append(f"{w}px JS错误:{r['errs']}")

print("-" * 72)
if errs:
    print("控制台异常：")
    for e in errs[:6]:
        print("  ", e)
print("RESULT:", "ALL PASS" if not fails else f"FAIL widths={fails}")
print("=" * 72)
sys.exit(0 if not fails else 1)