#!/usr/bin/env python3
"""第十一轮：验证「滚轮 2 行/格」是否等于 滚轮像素量 / 行高。
若 xterm 按像素滚动，则 每格行数 ≈ deltaY像素 / 行高px。
deltaY=-120 是标准一格滚轮；量行高像素即可预测。
另外验证 5.5.0 是否同此表现（若同，则非升级引入）。"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9490"))
def dismiss(cdp):
    try: cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception: pass
prof = tempfile.mkdtemp(prefix="scroll11")
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
    # 行高像素
    geo = cdp.eval("""(function(){ var r=document.querySelector('#termEl .xterm-screen').getBoundingClientRect();
        return {screenH:Math.round(r.height), rows:term.rows, rowPx:(r.height/term.rows),
                fontSize:term.options.fontSize, lineHeight:term.options.lineHeight,
                scrollback:term.options.scrollback}; })()""")
    print("几何: " + json.dumps(geo, ensure_ascii=False))
    rowpx = geo["rowPx"]; print("  ⇒ 预测每格行数 = 120/%.1f = %.2f" % (rowpx, 120/rowpx))
    box = cdp.eval("""(function(){ var e=document.querySelector('#termEl .xterm-screen');
        var r=e.getBoundingClientRect(); return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; })()""")
    for dy in [-40, -120, -360]:
        cdp.eval("term.scrollToBottom()"); time.sleep(0.4)
        b = cdp.eval("term.buffer.active.viewportY")
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=dy)
        time.sleep(0.6)
        a = cdp.eval("term.buffer.active.viewportY")
        moved = (b-a) if isinstance(a,int) and isinstance(b,int) else None
        pred = abs(dy)/rowpx
        print("  deltaY=%-5s 实走=%-4s 行  预测=%.2f 行  比值=%s" % (
            dy, moved, pred, ("%.2f" % (moved/pred)) if moved else '?'))
finally:
    try: proc.terminate()
    except Exception: pass
