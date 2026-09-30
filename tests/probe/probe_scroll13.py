#!/usr/bin/env python3
"""第十三轮：滚动条可见性真渲染取证（不改任何代码，只量）。

要区分三件事，否则修法会挑错：
  (a) 6.0 Auto 档的类切换 —— `_onMouseOver` → `visible` 常显；`_onMouseLeave` 后 500ms
      自动 `_hide()`（class 变 `invisible fade`，CSS opacity:0）。这是**设计**，不是缺陷。
  (b) slider 背景色 —— 由 theme.scrollbarSliderBackground 驱动，xterm 在 open() 时往
      #termEl 里注入一个 <style>（同特异性、注入点在 head 之后 ⇒ 会盖掉 index.html 里的静态 CSS）。
  (c) 到底是谁把它弄没了 —— 是"没 hover 所以隐形"，还是"hover 了也不显示"。

量法：真派发 Input.dispatchMouseEvent(mouseMoved) 进终端区（这会走 xterm 的
_onMouseOver 监听），再等 700ms 越过 500ms 隐藏超时，取 computedStyle。
"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9513"))


def dismiss(cdp):
    try:
        cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception:
        pass


READ = """(function(){
  var sb=document.querySelector('#termEl .xterm-scrollable-element > .scrollbar.vertical');
  if(!sb) return {found:false};
  var sl=sb.querySelector('.slider');
  var cs=getComputedStyle(sb);
  var slcs=sl?getComputedStyle(sl):null;
  return {found:true, cls:sb.className, opacity:cs.opacity, pe:cs.pointerEvents,
          disp:cs.display, w:Math.round(sb.getBoundingClientRect().width),
          h:Math.round(sb.getBoundingClientRect().height),
          slW:sl?Math.round(sl.getBoundingClientRect().width):-1,
          slH:sl?Math.round(sl.getBoundingClientRect().height):-1,
          slBg:slcs?slcs.backgroundColor:'-1', slOp:slcs?slcs.opacity:'-1'};
})()"""

prof = tempfile.mkdtemp(prefix="scroll13")
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
    print("term 区中心 =", box)
    print("\n[A] 加载后未动鼠标:")
    print("   ", json.dumps(cdp.eval(READ), ensure_ascii=False))
    cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=box["x"], y=box["y"])
    time.sleep(0.4)
    print("\n[B] mouseMoved 进终端后 0.4s（应已 visible）:")
    print("   ", json.dumps(cdp.eval(READ), ensure_ascii=False))
    time.sleep(1.0)
    print("\n[C] 停留 1.4s 后（mouseIsOver 应仍 true ⇒ 不该被 hide）:")
    print("   ", json.dumps(cdp.eval(READ), ensure_ascii=False))
    # 滚一下，验证 scroll 也会 reveal
    for _ in range(3):
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel", x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
        time.sleep(0.12)
    print("\n[D] 滚动 3 格后:")
    print("   ", json.dumps(cdp.eval(READ), ensure_ascii=False))
    # 移出终端区
    cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=5, y=5)
    time.sleep(1.2)
    print("\n[E] 移出终端、等待 1.2s（越过 500ms hide 超时）:")
    print("   ", json.dumps(cdp.eval(READ), ensure_ascii=False))
    print("\n[F] 注入的 <style> 实际内容（slider 背景色真值）:")
    print(cdp.eval("""(function(){ var out=[]; document.querySelectorAll('#termEl style').forEach(function(s){
        if(/slider/.test(s.textContent)) out.push(s.textContent); }); return out.join('\\n---\\n')||'(无)'; })()"""))
    print("\n[G] scrollSensitivity 生效值 =", cdp.eval("term.options.scrollSensitivity"))
finally:
    try:
        proc.terminate()
    except Exception:
        pass
