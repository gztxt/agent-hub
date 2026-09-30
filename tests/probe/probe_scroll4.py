#!/usr/bin/env python3
"""第四轮：DOM 渲染器档 vs WebGL 档，滚动行为是否一致（手机走 DOM 档）。"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")

def dismiss(cdp):
    try: cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception: pass

def run(cdp, renderer_q):
    cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
    cdp.send("Page.reload", ignoreCache=True); time.sleep(2.5)
    cdp.eval("go('chat')"); time.sleep(0.6); cdp.eval("switchMode('term')"); time.sleep(0.6)
    cdp.eval("ensureTerm()"); time.sleep(2.0)
    cdp.eval("""(function(){ var s=''; for (var i=1;i<=2000;i++) s+='L'+i+'\\r\\n'; term.write(s); return 1; })()""")
    time.sleep(2.5)
    name = cdp.eval("(typeof termRendererName==='undefined'?'?':termRendererName)")
    box = cdp.eval("""(function(){ var e=document.querySelector('#termEl .xterm-screen');
        if(!e) return null; var r=e.getBoundingClientRect();
        return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; })()""")
    cdp.eval("term.scrollToBottom()"); time.sleep(0.6)
    b4 = cdp.eval("term.buffer.active.viewportY")
    if box:
        for i in range(10):
            cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
            time.sleep(0.2)
    time.sleep(0.8)
    a4 = cdp.eval("term.buffer.active.viewportY")
    vp = cdp.eval("""(function(){ var v=document.querySelector('.xterm-viewport');
        if(!v) return null; var cs=getComputedStyle(v);
        return {scrollTop:v.scrollTop, sh:v.scrollHeight, ch:v.clientHeight,
                overflowY:cs.overflowY, scrollbarW: v.offsetWidth - v.clientWidth}; })()""")
    # 触摸式：Page.scrollTouch（手机真实路径）
    cdp.eval("term.scrollToBottom()"); time.sleep(0.5)
    b5 = cdp.eval("term.buffer.active.viewportY")
    try:
        cdp.send("Input.synthesizeScrollGesture", x=box["x"], y=box["y"],
                 xDistance=0, yDistance=300, speed=800, gestureSourceType="touch")
        time.sleep(1.0)
    except Exception as ex:
        pass
    a5 = cdp.eval("term.buffer.active.viewportY")
    print("[%s] renderer=%s 滚轮10格 %s→%s 位移=%s | 触摸滑动 %s→%s 位移=%s | viewport=%s" % (
        renderer_q, name, b4, a4, (b4-a4) if isinstance(a4,int) and isinstance(b4,int) else '?',
        b5, a5, (b5-a5) if isinstance(a5,int) and isinstance(b5,int) else '?', json.dumps(vp, ensure_ascii=False)))

prof = tempfile.mkdtemp(prefix="scroll4")
for q in ["dom", "webgl"]:
    port = 9420 + len(q)
    proc = launch_chrome(BASE + "/?term=" + q, port, tempfile.mkdtemp(prefix="sp"), 1200, 900)
    cdp = CDP(page_target(port), on_event=lambda m, p: dismiss(cdp))
    try:
        cdp.send("Page.enable"); cdp.send("Page.navigate", url=BASE + "/?term=" + q); time.sleep(2.0)
        run(cdp, q)
    finally:
        try: proc.terminate()
        except Exception: pass
