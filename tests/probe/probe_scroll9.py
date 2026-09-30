#!/usr/bin/env python3
"""第九轮：滚上去之后会不会被弹回底部？以及「滚到页顶」到底指什么。
关键区别：
  「无法上翻」= 视口上移后被拉回 / 根本不动
  「无法滚动到页顶」= 页面（body/main）不滚 —— 这是**另一个滚动容器**，不是 xterm
"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9470"))
def dismiss(cdp):
    try: cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception: pass
prof = tempfile.mkdtemp(prefix="scroll9")
proc = launch_chrome(BASE + "/", PORT, prof, 1280, 900)
cdp = CDP(page_target(PORT), on_event=lambda m, p: dismiss(cdp))
try:
    cdp.send("Page.enable"); cdp.send("Page.navigate", url=BASE + "/"); time.sleep(2.0)
    cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
    cdp.send("Page.reload", ignoreCache=True); time.sleep(2.5)
    cdp.eval("go('chat')"); time.sleep(0.6); cdp.eval("switchMode('term')"); time.sleep(0.6)
    cdp.eval("ensureTerm()"); time.sleep(2.0)
    cdp.eval("""(function(){ var s=''; for (var i=1;i<=2000;i++) s+='L'+i+'\\r\\n'; term.write(s); return 1; })()""")
    time.sleep(2.5)
    box = cdp.eval("""(function(){ var e=document.querySelector('#termEl .xterm-screen');
        var r=e.getBoundingClientRect(); return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; })()""")
    # ① 滚上去后等 5 秒，看会不会被弹回
    cdp.eval("term.scrollToBottom()"); time.sleep(0.5)
    b = cdp.eval("term.buffer.active.viewportY")
    for i in range(15):
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
        time.sleep(0.1)
    time.sleep(0.5)
    mid = cdp.eval("term.buffer.active.viewportY")
    time.sleep(5.0)
    later = cdp.eval("term.buffer.active.viewportY")
    print("① 弹回检测: 底部%s → 滚后%s → 等5秒%s  ⇒ %s" % (b, mid, later,
        "会被弹回底部!" if later == b else "保持不动(无弹回)"))
    # ② 一口气滚到最顶需要多少格
    cdp.eval("term.scrollToBottom()"); time.sleep(0.4)
    n = 0; last = cdp.eval("term.buffer.active.viewportY")
    while n < 300 and last > 0:
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
        n += 1
        if n % 20 == 0: time.sleep(0.15)
        cur = cdp.eval("term.buffer.active.viewportY")
        if cur == last: break
        last = cur
    print("② 滚到顶：用了 %d 格，最终 viewportY=%s" % (n, last))
    # ③ 页面级滚动容器体检（这才是「页顶」那条）
    pg = cdp.eval("""(function(){
      function m(sel){ var el=document.querySelector(sel); if(!el) return {sel:sel, missing:true};
        var cs=getComputedStyle(el);
        return {sel:sel, sh:el.scrollHeight, ch:el.clientHeight, st:el.scrollTop,
                overflowY:cs.overflowY, canScroll: el.scrollHeight>el.clientHeight,
                h:Math.round(el.getBoundingClientRect().height)}; }
      return [m('html'), m('body'), m('.shell'), m('main'), m('#termPane'),
              m('.term-body'), m('#termEl'), m('.xterm'), m('.xterm-viewport'),
              m('.xterm-scrollable-element')]; })()""")
    for r in pg: print("   " + json.dumps(r, ensure_ascii=False))
    # ④ 在终端区滚轮，是否被页面容器接走（看 window.scrollY 与 body.scrollTop 变化）
    cdp.eval("term.scrollToBottom()"); time.sleep(0.3)
    y0 = cdp.eval("[window.scrollY, document.body.scrollTop, document.documentElement.scrollTop].join(',')")
    for i in range(10):
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
        time.sleep(0.08)
    time.sleep(0.5)
    y1 = cdp.eval("[window.scrollY, document.body.scrollTop, document.documentElement.scrollTop].join(',')")
    vy = cdp.eval("term.buffer.active.viewportY")
    print("③ 终端区滚轮后: 页面 scrollY/body/html: [%s] → [%s]；xterm viewportY=%s" % (y0, y1, vy))
finally:
    try: proc.terminate()
    except Exception: pass
