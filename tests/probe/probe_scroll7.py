#!/usr/bin/env python3
"""第七轮（决定性对照）：触摸失效到底归因于谁。
四个实验组，唯一变量逐个切换：
  G1 基线           —— 原样
  G2 删自绘滚动条    —— 移除 .xterm-scrollable-element 里的 .scrollbar 节点
  G3 viewport 置 auto —— 让 .xterm-viewport 回到能真正滚的形态（sh>ch）
  G4 直接写 viewportY  —— 绕开一切事件，纯 API 验「视口本身能不能被移动」
"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
def dismiss(cdp):
    try: cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception: pass

def setup(cdp):
    cdp.send("Page.enable"); cdp.send("Page.navigate", url=BASE + "/"); time.sleep(2.0)
    cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
    cdp.send("Page.reload", ignoreCache=True); time.sleep(2.5)
    cdp.eval("go('chat')"); time.sleep(0.6); cdp.eval("switchMode('term')"); time.sleep(0.6)
    cdp.eval("ensureTerm()"); time.sleep(2.0)
    cdp.eval("""(function(){ var s=''; for (var i=1;i<=2000;i++) s+='L'+i+'\\r\\n'; term.write(s); return 1; })()""")
    time.sleep(2.5)
    cdp.eval("""(function(){ window.__ev=[];
      ['touchstart','touchmove','touchend'].forEach(function(t){
        document.addEventListener(t, function(){ window.__ev.push(t); }, true); }); return 1; })()""")

def touch_test(cdp, tag):
    cdp.eval("term.scrollToBottom()"); time.sleep(0.5)
    b = cdp.eval("term.buffer.active.viewportY")
    cdp.send("Input.synthesizeScrollGesture", x=265, y=391, xDistance=0, yDistance=250, speed=800, gestureSourceType="touch")
    time.sleep(1.2)
    a = cdp.eval("term.buffer.active.viewportY")
    ev = cdp.eval("(window.__ev||[]).join(',')")
    d = (b-a) if isinstance(a,int) and isinstance(b,int) else None
    print("  %-34s viewportY %s→%s 位移=%-5s 事件=[%s]" % (tag, b, a, d, ev))
    return d

port = int(os.getenv("HUB_PROBE_CDP_PORT", "9450"))
# G1 基线
proc = launch_chrome(BASE + "/", port, tempfile.mkdtemp(prefix="g1"), 430, 900)
cdp = CDP(page_target(port), on_event=lambda m, p: dismiss(cdp))
try:
    setup(cdp)
    print("G1 基线（xterm 6.0 原样）"); touch_test(cdp, "基线")
    # G2 删掉自绘滚动条节点
    n = cdp.eval("""(function(){ var s=document.querySelectorAll('.xterm-scrollable-element .scrollbar');
        var c=0; s.forEach(function(x){ x.parentNode.removeChild(x); c++; }); return c; })()""")
    print("G2 移除自绘滚动条节点 %s 个" % n); touch_test(cdp, "无 .scrollbar")
    # G3 viewport 恢复成真能滚
    cdp.eval("""(function(){ var v=document.querySelector('.xterm-viewport');
        v.style.overflowY='auto'; v.style.height='200px'; return 1; })()""")
    time.sleep(0.5)
    vp = cdp.eval("""(function(){ var v=document.querySelector('.xterm-viewport');
        return {sh:v.scrollHeight, ch:v.clientHeight}; })()""")
    print("G3 viewport 限高200  scrollHeight/clientHeight=%s" % json.dumps(vp))
    touch_test(cdp, "viewport 可滚")
    # G4 纯 API
    g4 = cdp.eval("""(function(){ var b=term.buffer.active, before=b.viewportY;
        term.scrollLines(-5); var mid=b.viewportY;
        term.scrollToTop(); var top=b.viewportY;
        term.scrollToBottom(); var bot=b.viewportY;
        return {before:before, afterScrollLinesNeg5:mid, afterScrollToTop:top, afterScrollToBottom:bot}; })()""")
    print("G4 纯 API（绕开事件）: " + json.dumps(g4, ensure_ascii=False))
finally:
    try: proc.terminate()
    except Exception: pass
