#!/usr/bin/env python3
"""第五轮：触摸失效的机制定位——是事件没到 xterm，还是被 CSS/布局吃掉。"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9430"))
def dismiss(cdp):
    try: cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception: pass
prof = tempfile.mkdtemp(prefix="scroll5")
proc = launch_chrome(BASE + "/", PORT, prof, 430, 900)   # 手机视口
cdp = CDP(page_target(PORT), on_event=lambda m, p: dismiss(cdp))
try:
    cdp.send("Page.enable"); cdp.send("Page.navigate", url=BASE + "/"); time.sleep(2.0)
    cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
    cdp.send("Page.reload", ignoreCache=True); time.sleep(2.5)
    cdp.eval("go('chat')"); time.sleep(0.6); cdp.eval("switchMode('term')"); time.sleep(0.6)
    cdp.eval("ensureTerm()"); time.sleep(2.0)
    cdp.eval("""(function(){ var s=''; for (var i=1;i<=2000;i++) s+='L'+i+'\\r\\n'; term.write(s); return 1; })()""")
    time.sleep(2.5)
    print("[renderer] " + str(cdp.eval("(typeof termRendererName==='undefined'?'?':termRendererName)")))
    # 1) 触摸落点上最顶层的元素是谁（谁在吃事件）
    box = cdp.eval("""(function(){ var e=document.querySelector('#termEl .xterm-screen');
        if(!e) return null; var r=e.getBoundingClientRect();
        return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; })()""")
    hit = cdp.eval("""(function(x,y){ var el=document.elementFromPoint(x,y);
        var out=[]; while(el && out.length<6){ var cs=getComputedStyle(el);
          out.push({tag:el.tagName, cls:(el.className&&el.className.baseVal!==undefined?el.className.baseVal:el.className)||'',
                    touchAction:cs.touchAction, overflowY:cs.overflowY, pe:cs.pointerEvents,
                    of:cs.overflow, h:Math.round(el.getBoundingClientRect().height)});
          el=el.parentElement; } return out; })()""", ) if False else None
    # eval 只接受表达式字符串，这里用格式化注入
    hit = cdp.eval("""(function(){ var e=document.querySelector('#termEl .xterm-screen');
        if(!e) return null; var r=e.getBoundingClientRect();
        var x=Math.round(r.left+r.width/2), y=Math.round(r.top+r.height/2);
        var el=document.elementFromPoint(x,y); var out=[];
        while(el && out.length<7){ var cs=getComputedStyle(el);
          out.push({tag:el.tagName, cls:String(el.className||''), touchAction:cs.touchAction,
                    overflowY:cs.overflowY, pe:cs.pointerEvents, h:Math.round(el.getBoundingClientRect().height)});
          el=el.parentElement; }
        return {point:[x,y], chain:out}; })()""")
    print("[① 触摸落点元素链] " + json.dumps(hit, ensure_ascii=False))
    # 2) 全链 touch-action 汇总
    ta = cdp.eval("""(function(){ var out={};
        ['html','body','.shell','main','#termPane','.term-body','#termEl','.xterm','.xterm-viewport','.xterm-screen']
        .forEach(function(sel){ var el=document.querySelector(sel); if(!el) return;
          var cs=getComputedStyle(el); out[sel]={touchAction:cs.touchAction, overflowY:cs.overflowY, overflowX:cs.overflowX}; });
        return out; })()""")
    print("[② 关键节点 touch-action/overflow] " + json.dumps(ta, ensure_ascii=False))
    # 3) 手动派发 touchstart/touchmove 看 xterm 有没有收到
    recv = cdp.eval("""(function(){
        window.__touches=[];
        var el=document.querySelector('.xterm-viewport')||document.querySelector('.xterm');
        if(!el) return {err:'no-el'};
        ['touchstart','touchmove','touchend'].forEach(function(t){
          el.addEventListener(t, function(e){ window.__touches.push(t); }, {passive:true});
        });
        return {bound:true, el:el.className}; })()""")
    print("[③ 监听已挂] " + json.dumps(recv, ensure_ascii=False))
    cdp.eval("term.scrollToBottom()"); time.sleep(0.5)
    b4 = cdp.eval("term.buffer.active.viewportY")
    cdp.send("Input.synthesizeScrollGesture", x=hit["point"][0], y=hit["point"][1],
             xDistance=0, yDistance=250, speed=800, gestureSourceType="touch")
    time.sleep(1.2)
    a4 = cdp.eval("term.buffer.active.viewportY")
    tl = cdp.eval("(window.__touches||[]).join(',')")
    print("[④ 触摸滑动结果] viewportY %s→%s 位移=%s  收到事件=[%s]" % (b4, a4, (b4-a4) if isinstance(a4,int) and isinstance(b4,int) else '?', tl))
    # 4) 滚轮在手机视口下是否仍有效（对照）
    cdp.eval("term.scrollToBottom()"); time.sleep(0.4)
    b5 = cdp.eval("term.buffer.active.viewportY")
    for i in range(5):
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=hit["point"][0], y=hit["point"][1], deltaX=0, deltaY=-120)
        time.sleep(0.2)
    time.sleep(0.6)
    a5 = cdp.eval("term.buffer.active.viewportY")
    print("[⑤ 对照·滚轮] viewportY %s→%s 位移=%s" % (b5, a5, (b5-a5) if isinstance(a5,int) and isinstance(b5,int) else '?'))
finally:
    try: proc.terminate()
    except Exception: pass
