#!/usr/bin/env python3
"""第二轮取证：滚轮到底能滚多少 + scrollToTop 为什么不到顶。"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
CDP_PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9414"))
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
def dismiss(cdp):
    try: cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception: pass
prof = tempfile.mkdtemp(prefix="scroll2")
proc = launch_chrome(BASE + "/", CDP_PORT, prof, 1200, 900)
cdp = CDP(page_target(CDP_PORT), on_event=lambda m, p: dismiss(cdp))
try:
    cdp.send("Page.enable"); cdp.send("Page.navigate", url=BASE + "/"); time.sleep(2.0)
    cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
    cdp.send("Page.reload", ignoreCache=True); time.sleep(2.5)
    cdp.eval("go('chat')"); time.sleep(0.6)
    cdp.eval("switchMode('term')"); time.sleep(0.6)
    cdp.eval("ensureTerm()"); time.sleep(2.0)
    cdp.eval("""(function(){ var s=''; for (var i=1;i<=2000;i++) s+='L'+i+'\\r\\n'; term.write(s); return 1; })()""")
    time.sleep(2.5)
    box = cdp.eval("""(function(){ var e=document.querySelector('#termEl .xterm-screen');
        var r=e.getBoundingClientRect(); return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; })()""")
    # A: scrollToTop 后 ydisp 到底多少
    a = cdp.eval("""(function(){ var b=term.buffer.active;
        term.scrollToTop(); return {afterScrollToTop:(typeof b.ydisp==='number'?b.ydisp:String(b.ydisp)), baseY:b.baseY, ybase:b.length, rows:term.rows, ydispType:typeof b.ydisp, hasYdisp:('ydisp' in b)}; })()""")
    print("[A scrollToTop] " + json.dumps(a, ensure_ascii=False))
    # B: 从顶往下滚 3 格，每格滚多少
    cdp.eval("term.scrollToBottom()"); time.sleep(0.8)
    start = cdp.eval("(function(){var v=term.buffer.active.ydisp; return typeof v==='number'?v:-1;})()")
    seq = []
    for i in range(3):
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
        time.sleep(0.4)
        seq.append(cdp.eval("(function(){var v=term.buffer.active.ydisp; return typeof v==='number'?v:-1;})()"))
    print("[B 滚轮每格] start=%s seq=%s 每格位移=%s" % (start, seq, [seq[i]-(seq[i-1] if i else start) for i in range(len(seq))]))
    # C: 键盘方向键/PageUp 能否滚
    cdp.eval("term.scrollToBottom()"); time.sleep(0.6)
    b4 = cdp.eval("(function(){var v=term.buffer.active.ydisp; return typeof v==='number'?v:-1;})()")
    cdp.send("Input.dispatchKeyEvent", type="rawKeyDown", key="PageUp", code="PageUp", windowsVirtualKeyCode=33, nativeVirtualKeyCode=33)
    cdp.send("Input.dispatchKeyEvent", type="keyUp", key="PageUp", code="PageUp", windowsVirtualKeyCode=33, nativeVirtualKeyCode=33)
    time.sleep(0.6)
    af = cdp.eval("(function(){var v=term.buffer.active.ydisp; return typeof v==='number'?v:-1;})()")
    print("[C PageUp] before=%s after=%s delta=%s" % (b4, af, (af-b4) if isinstance(af,int) and isinstance(b4,int) else '?'))
    # D: viewport 能不能直接写 scrollTop
    d = cdp.eval("""(function(){ var v=document.querySelector('.xterm-viewport');
        if(!v) return {err:'no-viewport'};
        var out={sh:v.scrollHeight, ch:v.clientHeight, before:v.scrollTop};
        try{ v.scrollTop = 200; out.afterSet=v.scrollTop; }catch(e){ out.throw=e.message; }
        try{ v.scrollTo(0,200); out.afterScrollTo=v.scrollTop; }catch(e){ out.throw2=e.message; }
        out.ydispAfter=(function(){var v=term.buffer.active.ydisp; return typeof v==='number'?v:String(v);})(); return out; })()""")
    print("[D viewport 直写] " + json.dumps(d, ensure_ascii=False))
    # E: 连续滚 30 格看是否线性
    cdp.eval("term.scrollToBottom()"); time.sleep(0.5)
    b5 = cdp.eval("(function(){var v=term.buffer.active.ydisp; return typeof v==='number'?v:-1;})()")
    for i in range(30):
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
    time.sleep(1.2)
    a5 = cdp.eval("(function(){var v=term.buffer.active.ydisp; return typeof v==='number'?v:-1;})()")
    print("[E 连滚30格] before=%s after=%s 位移=%s (每格 %.2f)" % (b5, a5, (b5-a5) if isinstance(a5,int) else '?', ((b5-a5)/30.0) if isinstance(a5,int) else 0))
finally:
    try: proc.terminate()
    except Exception: pass
