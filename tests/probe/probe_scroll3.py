#!/usr/bin/env python3
"""第三轮：确认 ydisp 在 6.0 被改名，以及新 API 是否真能滚。"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
CDP_PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9416"))
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
def dismiss(cdp):
    try: cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception: pass
prof = tempfile.mkdtemp(prefix="scroll3")
proc = launch_chrome(BASE + "/", CDP_PORT, prof, 1200, 900)
cdp = CDP(page_target(CDP_PORT), on_event=lambda m, p: dismiss(cdp))
try:
    cdp.send("Page.enable"); cdp.send("Page.navigate", url=BASE + "/"); time.sleep(2.0)
    cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
    cdp.send("Page.reload", ignoreCache=True); time.sleep(2.5)
    cdp.eval("go('chat')"); time.sleep(0.6); cdp.eval("switchMode('term')"); time.sleep(0.6)
    cdp.eval("ensureTerm()"); time.sleep(2.0)
    cdp.eval("""(function(){ var s=''; for (var i=1;i<=2000;i++) s+='L'+i+'\\r\\n'; term.write(s); return 1; })()""")
    time.sleep(2.5)
    # 1) buffer 上到底有哪些视口相关属性
    keys = cdp.eval("""(function(){ var b=term.buffer.active; var out=[];
        for (var k in b) { if (typeof b[k] === 'number') out.push(k+'='+b[k]); }
        return out.join(' '); })()""")
    print("[1 buffer 数值属性] " + str(keys))
    # 2) 6.0 的新 API
    api = cdp.eval("""(function(){ var b=term.buffer.active; return {
        viewportY: b.viewportY, baseY: b.baseY, ybase: b.length, cursorY: b.cursorY,
        ydisp: String(b.ydisp), viewportBaseY: b.viewportBaseY }; })()""")
    print("[2 6.0 API] " + json.dumps(api, ensure_ascii=False))
    # 3) scrollToTop / scrollLines 用新 API 验
    t = cdp.eval("""(function(){ var b=term.buffer.active;
        term.scrollToTop(); return {afterTop_viewportY:b.viewportY, afterTop_baseY:b.baseY}; })()""")
    print("[3 scrollToTop] " + json.dumps(t, ensure_ascii=False))
    # 4) 真滚轮 + viewportY 读数
    box = cdp.eval("""(function(){ var e=document.querySelector('#termEl .xterm-screen');
        var r=e.getBoundingClientRect(); return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; })()""")
    cdp.eval("term.scrollToBottom()"); time.sleep(0.6)
    b4 = cdp.eval("term.buffer.active.viewportY")
    for i in range(10):
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
        time.sleep(0.2)
    time.sleep(0.8)
    a4 = cdp.eval("term.buffer.active.viewportY")
    print("[4 滚轮10格] before=%s after=%s 位移=%s ⇒ %s" % (b4, a4, (b4-a4) if isinstance(a4,int) and isinstance(b4,int) else '?',
          "滚轮有效" if isinstance(a4,int) and isinstance(b4,int) and a4 < b4 else "滚轮无效"))
    # 5) scrollLines 负数
    cdp.eval("term.scrollToBottom()"); time.sleep(0.5)
    b5 = cdp.eval("term.buffer.active.viewportY")
    cdp.eval("term.scrollLines(-10)"); time.sleep(0.5)
    a5 = cdp.eval("term.buffer.active.viewportY")
    print("[5 scrollLines(-10)] before=%s after=%s 位移=%s" % (b5, a5, (b5-a5) if isinstance(a5,int) and isinstance(b5,int) else '?'))
finally:
    try: proc.terminate()
    except Exception: pass
