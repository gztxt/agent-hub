#!/usr/bin/env python3
"""第十二轮：验证解法。xterm 6.0 官方参数 scrollSensitivity（默认 1）。
预期：调大后每格滚动的行数线性增加。"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9500"))
def dismiss(cdp):
    try: cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception: pass
prof = tempfile.mkdtemp(prefix="scroll12")
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
    def per_notch(sens):
        cdp.eval("""(function(){ try{ term.options.scrollSensitivity=%s; return 1; }catch(e){ return 'err:'+e.message; } })()""" % sens)
        time.sleep(0.3)
        got = cdp.eval("term.options.scrollSensitivity")
        cdp.eval("term.scrollToBottom()"); time.sleep(0.4)
        b = cdp.eval("term.buffer.active.viewportY")
        for i in range(10):
            cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
            time.sleep(0.12)
        time.sleep(0.5)
        a = cdp.eval("term.buffer.active.viewportY")
        return got, ((b-a)/10.0 if isinstance(a,int) and isinstance(b,int) else None)
    print("scrollSensitivity 对照（10 格滚轮的平均行/格）:")
    for s in [1, 3, 5, 10]:
        got, per = per_notch(s)
        print("   设=%-3s 实际生效=%-4s ⇒ %s 行/格" % (s, got, ("%.1f" % per) if per else '?'))
finally:
    try: proc.terminate()
    except Exception: pass
