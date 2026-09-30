#!/usr/bin/env python3
"""第六轮（修正版）：触摸落点链 + 6.0 自绘滚动条可见性 + 事件是否真到达 xterm。"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9440"))
def dismiss(cdp):
    try: cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception: pass
prof = tempfile.mkdtemp(prefix="scroll6")
proc = launch_chrome(BASE + "/", PORT, prof, 430, 900)
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
    # ① .xterm 的完整子树结构（看 6.0 到底建了哪些滚动相关节点）
    tree = cdp.eval("""(function(){
      function desc(el, d, out){ if(d>3||!el) return out;
        var cs=getComputedStyle(el), r=el.getBoundingClientRect();
        out.push('  '.repeat(d)+el.tagName+'.'+String(el.className||'(no-cls)')+
          ' ['+Math.round(r.width)+'x'+Math.round(r.height)+'] ov='+cs.overflow+'/'+cs.overflowY+
          ' ta='+cs.touchAction);
        for(var i=0;i<el.children.length;i++) desc(el.children[i], d+1, out);
        return out; }
      var x=document.querySelector('#termEl .xterm'); if(!x) return ['no-xterm'];
      return desc(x,0,[]); })()""")
    print("[① .xterm 子树]\n" + "\n".join(tree))
    # ② 自绘滚动条在不在、可见不
    sb = cdp.eval("""(function(){
      var out={};
      var se=document.querySelector('.xterm-scrollable-element');
      out.scrollableElement=!!se;
      if(se){ var cs=getComputedStyle(se); var r=se.getBoundingClientRect();
        out.seStyle={overflow:cs.overflow, overflowY:cs.overflowY, touchAction:cs.touchAction,
                     pos:cs.position, h:Math.round(r.height), sh:se.scrollHeight, ch:se.clientHeight};
        out.seChildren=Array.prototype.map.call(se.children,function(c){return c.className;});
      }
      var sbs=document.querySelectorAll('.xterm-scrollable-element .scrollbar');
      out.scrollbarCount=sbs.length;
      out.scrollbars=Array.prototype.map.call(sbs,function(s){ var r=s.getBoundingClientRect();
        var cs=getComputedStyle(s);
        return {cls:s.className, w:Math.round(r.width), h:Math.round(r.height),
                display:cs.display, visibility:cs.visibility, opacity:cs.opacity, pos:cs.position}; });
      var v=document.querySelector('.xterm-viewport');
      out.viewport= v? (function(){var r=v.getBoundingClientRect();var cs=getComputedStyle(v);
        return {w:Math.round(r.width),h:Math.round(r.height),sh:v.scrollHeight,ch:v.clientHeight,
                overflowY:cs.overflowY, visible: cs.display!=='none'&&cs.visibility!=='hidden'};})() : null;
      return out; })()""")
    print("[② 滚动条结构] " + json.dumps(sb, ensure_ascii=False))
    # ③ 触摸事件真到达 xterm 了吗（挂 document，捕获阶段）
    cdp.eval("""(function(){ window.__ev=[];
      ['touchstart','touchmove','touchend'].forEach(function(t){
        document.addEventListener(t, function(e){ window.__ev.push(t); }, true); });
      return 1; })()""")
    cdp.eval("term.scrollToBottom()"); time.sleep(0.5)
    b4 = cdp.eval("term.buffer.active.viewportY")
    cdp.send("Input.synthesizeScrollGesture", x=265, y=391, xDistance=0, yDistance=250, speed=800, gestureSourceType="touch")
    time.sleep(1.2)
    a4 = cdp.eval("term.buffer.active.viewportY")
    print("[③ 触摸滑动] viewportY %s→%s 位移=%s  document收到=[%s]" % (
        b4, a4, (b4-a4) if isinstance(a4,int) and isinstance(b4,int) else '?', cdp.eval("(window.__ev||[]).join(',')")))
finally:
    try: proc.terminate()
    except Exception: pass
