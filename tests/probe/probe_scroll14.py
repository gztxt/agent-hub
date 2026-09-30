#!/usr/bin/env python3
"""第十四轮：端到端「从底部滚到页顶」验证（真派发滚轮，非 API）。

这是用户报障的原句复现量测：桌面浏览器里滚轮无法上翻/滚到页顶。
判据：连续真滚轮后 viewportY 能到 0（真到顶），且所需格数远低于修前。
"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9514"))


def dismiss(cdp):
    try:
        cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception:
        pass


prof = tempfile.mkdtemp(prefix="scroll14")
proc = launch_chrome(BASE + "/", PORT, prof, 1280, 900)
cdp = CDP(page_target(PORT), on_event=lambda m, p: dismiss(cdp))
try:
    cdp.send("Page.enable")
    cdp.send("Page.navigate", url=BASE + "/")
    time.sleep(2.0)
    cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
    cdp.send("Page.reload", ignoreCache=True)
    time.sleep(2.5)
    cdp.eval("go('chat')"); time.sleep(0.6)
    cdp.eval("switchMode('term')"); time.sleep(0.6)
    cdp.eval("ensureTerm()"); time.sleep(2.0)
    cdp.eval("(function(){var s='';for(var i=1;i<=2000;i++) s+='L'+i+'\\r\\n'; term.write(s); return 1;})()")
    time.sleep(2.5)
    box = cdp.eval("""(function(){ var e=document.querySelector('#termEl .xterm-screen');
        var r=e.getBoundingClientRect(); return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; })()""")
    for _ in range(3):
        cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=box["x"], y=box["y"])
        break
    cdp.eval("term.scrollToBottom()"); time.sleep(0.5)
    tot = cdp.eval("term.buffer.active.length - term.rows")
    y0 = cdp.eval("term.buffer.active.viewportY")
    print("缓冲总行数 =", cdp.eval("term.buffer.active.length"), " 视口行数 =", cdp.eval("term.rows"))
    print("起点 viewportY =", y0, "（可上翻行数 =", tot, "）")
    n = 0
    while n < 600:
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
        n += 1
        y = cdp.eval("term.buffer.active.viewportY")
        if y == 0:
            break
        if n % 20 == 0:
            cdp.eval("term.scrollToBottom()") if False else None
    print("滚到顶所需格数 =", n, " ⇒ 终态 viewportY =", cdp.eval("term.buffer.active.viewportY"))
    top = cdp.eval("""(function(){ var rows=document.querySelectorAll('#termEl .xterm-rows > div');
        return rows.length? rows[0].textContent.slice(0,30) : '(无)'; })()""")
    print("视口首行内容 =", top, "（应为 L1 附近 = 真的到顶了）")
    sb = cdp.eval("""(function(){ var s=document.querySelector('#termEl .scrollbar.vertical');
        if(!s) return null; var sl=s.querySelector('.slider'); var r=sl.getBoundingClientRect();
        return {cls:s.className, op:getComputedStyle(s).opacity, top:Math.round(r.top),
                trackTop:Math.round(s.getBoundingClientRect().top)}; })()""")
    print("到顶时滚动条 =", json.dumps(sb, ensure_ascii=False))
finally:
    try:
        proc.terminate()
    except Exception:
        pass
