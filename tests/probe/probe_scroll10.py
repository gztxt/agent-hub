#!/usr/bin/env python3
"""第十轮：滚轮卡在哪？逐格记录 viewportY，找拐点。
同时对照：用 CDP 真实 wheel（deltaY=-120）vs 直接调 API 滚动同样距离。"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9480"))
def dismiss(cdp):
    try: cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception: pass
prof = tempfile.mkdtemp(prefix="scroll10")
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
    cdp.eval("term.scrollToBottom()"); time.sleep(0.5)
    print("逐格记录（每 10 格一采样，deltaY=-120）:")
    seq = []
    for i in range(1, 121):
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
        if i % 10 == 0:
            seq.append((i, cdp.eval("term.buffer.active.viewportY")))
            print("   格%-4d viewportY=%s" % (i, seq[-1][1]))
        if i % 10 == 0: time.sleep(0.2)
    # 找拐点：从某格开始不再变化
    stall = None
    for j in range(1, len(seq)):
        if seq[j][1] == seq[j-1][1]:
            stall = seq[j]; break
    print("拐点: %s" % (("在第 %d 格停住于 %s" % stall) if stall else "未见停住"))
    # 大幅 deltaY 试试（一次滚 10 倍）
    cdp.eval("term.scrollToBottom()"); time.sleep(0.4)
    b = cdp.eval("term.buffer.active.viewportY")
    cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-1200)
    time.sleep(0.6)
    a = cdp.eval("term.buffer.active.viewportY")
    print("单次 deltaY=-1200: %s → %s 位移=%s" % (b, a, (b-a) if isinstance(a,int) and isinstance(b,int) else '?'))
    # API 滚动同样距离对照
    cdp.eval("term.scrollToBottom()"); time.sleep(0.4)
    c = cdp.eval("term.buffer.active.viewportY")
    cdp.eval("term.scrollLines(-31)"); time.sleep(0.4)
    d = cdp.eval("term.buffer.active.viewportY")
    print("API scrollLines(-31) 对照: %s → %s 位移=%s" % (c, d, (c-d) if isinstance(d,int) and isinstance(c,int) else '?'))
    # xterm 内部滚动条 slider 的位置（自绘滚动条反映的可见比例）
    sl = cdp.eval("""(function(){ var s=document.querySelector('.scrollbar.vertical .slider, .xterm-scrollable-element .scrollbar.vertical .slider');
        if(!s) return {err:'no-slider'}; var r=s.getBoundingClientRect();
        var p=document.querySelector('.xterm-scrollable-element .scrollbar.vertical');
        var pr=p?p.getBoundingClientRect():null;
        return {sliderH:Math.round(r.height), trackH:pr?Math.round(pr.height):null,
                top:Math.round(r.top), trackTop:pr?Math.round(pr.top):null,
                cls:s.parentNode?String(s.parentNode.className):''}; })()""")
    print("自绘滚动条 slider: " + json.dumps(sl, ensure_ascii=False))
finally:
    try: proc.terminate()
    except Exception: pass
