#!/usr/bin/env python3
"""第十五轮：到顶后视口首行内容取证（走 API，避开 DOM 行选择器差异）。"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9515"))
def dismiss(cdp):
    try: cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception: pass
prof = tempfile.mkdtemp(prefix="scroll15")
proc = launch_chrome(BASE + "/", PORT, prof, 1280, 900)
cdp = CDP(page_target(PORT), on_event=lambda m, p: dismiss(cdp))
try:
    cdp.send("Page.enable"); cdp.send("Page.navigate", url=BASE + "/"); time.sleep(2.0)
    cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
    cdp.send("Page.reload", ignoreCache=True); time.sleep(2.5)
    cdp.eval("go('chat')"); time.sleep(0.6); cdp.eval("switchMode('term')"); time.sleep(0.6)
    cdp.eval("ensureTerm()"); time.sleep(2.0)
    cdp.eval("(function(){var s='';for(var i=1;i<=2000;i++) s+='L'+i+'\\r\\n'; term.write(s); return 1;})()")
    time.sleep(2.5)
    box = cdp.eval("""(function(){ var e=document.querySelector('#termEl .xterm-screen');
        var r=e.getBoundingClientRect(); return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; })()""")
    cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=box["x"], y=box["y"])
    cdp.eval("term.scrollToBottom()"); time.sleep(0.5)
    for i in range(190):
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
    time.sleep(0.8)
    print("viewportY =", cdp.eval("term.buffer.active.viewportY"))
    print("视口首行 =", cdp.eval("term.buffer.active.getLine(0).translateToString(true)"))
    print("视口末行 =", cdp.eval("term.buffer.active.getLine(23).translateToString(true)"))
finally:
    try: proc.terminate()
    except Exception: pass
