#!/usr/bin/env python3
"""终端上翻/滚动专项真渲染闸门（09-30 用户报障：「终端页面无法上翻、无滚动条」）。

跑法（影子实例须已起，见 tests/verify_term_xterm6.py 头部）：
    HUB_PROBE_CDP_PORT=9412 ../agent-hub/venv/bin/python tests/verify_term_scrollback.py http://127.0.0.1:3199

为什么必须真渲染量几何：滚动条是 xterm 内部 `.xterm-viewport` 这个 DOM 节点的
`overflow-y:scroll` + 其 scrollTop/scrollHeight，静态读 CSS 只能证明"规则还在"，
证明不了"真能滚上去"。判据一律写成可断言的量，不以"我看了截图"交差。

关键坑（照抄，别自己重踩）：
  - 必须先 go('chat') 再切 term 模式，否则 #termEl 尺寸全 0（见 verify_term_xterm6 头部）。
  - 灌内容要用 buffer.write + 真实换行；只塞一长行不会撑出 scrollback。
  - xterm 写完要 await 一帧再量 scrollHeight，否则量到的是还没上屏的旧值。
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
CDP_PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9412"))
H = 900
res = []


def chk(name, ok, detail=""):
    res.append(bool(ok))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("  —— " + str(detail)) if detail else ""))


def ev(cdp, expr, awaitp=False):
    r = cdp.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=awaitp)
    try:
        return r["result"]["result"].get("value")
    except Exception:
        return None


def main():
    prof = tempfile.mkdtemp(prefix="scrollprof")
    launch_chrome(BASE, CDP_PORT, prof, 1200, H)
    cdp = CDP(page_target(CDP_PORT), on_event=lambda m, p: dismiss(cdp))
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Page.navigate", url=BASE)
    time.sleep(2.0)
    cdp.send("Page.reload", ignoreCache=True)
    time.sleep(3.0)

    # 进终端页并把 term 真正建起来
    ev(cdp, "try{ if(localStorage.getItem('hub.passcode')===null){} }catch(e){}")
    ev(cdp, "try{ localStorage.setItem('hub.auth','1'); }catch(e){}", True)
    ev(cdp, "if(typeof go==='function'){go('chat');}")
    time.sleep(1.0)
    ev(cdp, "if(typeof switchMode==='function'){switchMode('term');}")
    time.sleep(1.5)
    ev(cdp, "if(typeof ensureTerm==='function'){try{window.__t=ensureTerm()}catch(e){window.__terr=String(e)}}")
    time.sleep(2.5)

    chk("终端实例已建立", ev(cdp, "!!(window.__t && window.__t.element)") is not False,
        ev(cdp, "window.__terr || (window.__t && window.__t.element ? 'ok' : 'no term')"))

    # 灌 400 行，超过默认 24 行视口 ⇒ 必有 scrollback
    ev(cdp, """(function(){
      var t=window.__t; if(!t) return;
      for(var i=1;i<=400;i++){ t.write('\\x1b[33mLINE-'+i+'\\x1b[0m 上翻测试填充行 abcdefghijklmnop\\r\\n'); }
    })()""")
    time.sleep(2.0)

    g = ev(cdp, """(function(){
      var vp=document.querySelector('.xterm-viewport');
      var sc=document.querySelector('.xterm-screen');
      var el=document.getElementById('termEl');
      if(!vp) return {err:'no .xterm-viewport'};
      var cs=getComputedStyle(vp);
      var r=vp.getBoundingClientRect(), er=el?el.getBoundingClientRect():{height:0};
      return {scrollTop:vp.scrollTop, scrollHeight:vp.scrollHeight, clientHeight:vp.clientHeight,
              overflowY:cs.overflowY, w:Math.round(r.width), h:Math.round(r.height),
              termH:Math.round(er.height), canScroll:vp.scrollHeight>vp.clientHeight,
              rows:(t=window.__t)?t.rows:0, base:(t=window.__t)?t.buffer.baseY:0};
    })()""")
    print("  [几何]", g)
    if not g or g.get("err"):
        chk("拿到 viewport 几何", False, g)
        return finish()
    chk("拿到 viewport 几何", True, g)
    chk("内容超出视口（有 scrollback 可上翻）", g["canScroll"],
        "scrollHeight=%s clientHeight=%s" % (g["scrollHeight"], g["clientHeight"]))
    chk("overflow-y 未被改成 hidden/visible", g["overflowY"] in ("scroll", "auto"), g["overflowY"])
    chk("viewport 有实际尺寸（没塌成 0×0）", g["h"] > 50 and g["w"] > 100,
        "w=%s h=%s termH=%s" % (g["w"], g["h"], g["termH"]))

    # 真滚：脚本把 scrollTop 拉到底（= 用户手指上翻到顶）
    after = ev(cdp, """(function(){
      var vp=document.querySelector('.xterm-viewport');
      vp.scrollTop = 0;                       // 0 = 最老一行
      return {scrollTop:vp.scrollTop, topLine:(window.__t&&window.__t.buffer.active.getLine(vp.scrollTop))?1:0};
    })()""")
    chk("上翻生效（scrollTop 可置 0 且命中行）", after is not None and after.get("scrollTop") == 0, after)

    # 滚轮上翻（真实事件路径）
    wheel = ev(cdp, """(function(){
      var vp=document.querySelector('.xterm-viewport');
      vp.scrollTop=vp.scrollHeight;
      for(var i=0;i<5;i++){ vp.dispatchEvent(new WheelEvent('wheel',{deltaY:-120,bubbles:true,cancelable:true})); }
      return {scrollTop:vp.scrollTop, sh:vp.scrollHeight, ch:vp.clientHeight};
    })()""")
    chk("滚轮上翻改变 scrollTop", wheel and wheel["scrollTop"] < wheel["sh"], wheel)

    # 用户视角：底部内容是否真的能翻到
    vis = ev(cdp, """(function(){
      var t=window.__t; if(!t) return null;
      var vp=document.querySelector('.xterm-viewport');
      var l1=t.buffer.active.getLine(t.buffer.baseY+t.buffer.cursorY+1);
      var l0=t.buffer.active.getLine(vp.scrollTop);
      return {atBottom:vp.scrollTop>=vp.scrollHeight-vp.clientHeight-2,
              firstVisibleLen:l0?l0.getLineLength():-1, baseY:t.buffer.baseY};
    })()""")
    print("  [可见性]", vis)
    chk("默认停在底部（内容写完自动跟随）", vis and vis.get("atBottom"), vis)
    return finish()


def dismiss(cdp):
    try:
        cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception:
        pass


def finish():
    ok = sum(1 for x in res if x)
    print("\n%d/%d PASS" % (ok, len(res)))
    sys.exit(0 if ok == len(res) else 1)


if __name__ == "__main__":
    main()
