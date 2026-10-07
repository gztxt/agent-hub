#!/usr/bin/env python3
"""「备用屏已被彻底禁止」验收探针（v0.13.83；前身是 v0.13.82 的「造红→逃生」版）。

背景（一句话）：codex 的 TUI 发 \\x1b[?1049h 进备用屏，而 xterm.js 的备用屏按设计
没有 scrollback；整页共用一个 xterm 实例 ⇒ 一次污染让所有 agent 都滚不动。

v0.13.82 的对策是「换会话那帧补写退出序列 + 状态字 + 手点逃生按钮」。
v0.13.83 用户裁定改为**从根上禁止进入**：把 DECSET 的 1049/1047/47 注册成「吞掉」，
内建 activateAltBuffer 不执行（见 03-agents-cards.js 的 termAltScreenBlock）。
于是本探针的判据整体**翻转**：不再是「进得去、逃得出来」，而是「**根本进不去**」。

判据（每条都可断言，不以「我看了截图」交差）：
  B1 新会话后本端在**主屏**（normal）
  B2 基线：主屏 baseY>0 且能上翻（先证明这屏本来就有 scrollback）
  B3 写 \\x1b[?1049h 后**仍在主屏**（alternate 永不出现）← 本次核心
  B4 写 \\x1b[?1049h 后 scrollback 与上翻能力**不变**（判据从「丢了」翻成「没丢」）
  B5 混合序列 \\x1b[?1049h\\x1b[?1003h：仍在主屏 **且**鼠标模式为 h
     ← 证明拦截是**精确点名** 1049/1047/47，没有把 DECSET 全家闷掉
  B6 切到另一条会话后仍在主屏、仍能上翻
  B7 DOM 里已不存在 #termBufChip / #termAltOut（UI 真删干净，防"注释删了元素还在"）
  B8 逐一写序列看状态：1047/47 也拦；1003(鼠标)/2004(粘贴) 照常放行
  B9 输入控件去鼠标（要求 1b）：helper textarea 恒 none；查找框**关闭时**整条 none、
     **打开时** auto、关闭后又能回到 none（状态可逆）

跑法（L2 live：先起影子实例，**绝不动生产 :3102**；worktree 无 .env，端口/数据与生产隔离）：
  ( cd <repo> && mkdir -p work/probe/data && \
    setsid env DATA_DIR=$PWD/work/probe/data PORT=3199 HOST=127.0.0.1 \
      HUB_WRITE_TOKEN=probe-token TERM_TOKEN=probe-term-token \
      venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 \
      > work/probe/shadow.log 2>&1 < /dev/null & )
  HUB_PROBE_TERM_TOKEN=probe-term-token venv/bin/python tests/verify_term_altscreen.py
收尾：fuser -k 3199/tcp    # 禁 pkill -f "3199"，会连带杀自己

踩过的坑（改本文件前先读）：
  - 本仓 `_cdp_min.CDP.send()` 返回的**已经是 result 对象**（内部做过 m.get("result")），
    取值只需再下一层；照抄别的探针写 r["result"]["result"] 会 KeyError 并被吞成 None，
    表现为「API 明明 POST 200，却报会话没建起来」——本轮就是这样白跑两轮。
  - `Runtime.evaluate` 的 awaitPromise 取回 None（实测），所以建会话走「挂 window 再轮询」。
  - 合成 WheelEvent 在 headless DOM 档下**不保证**走 xterm 的物理滚轮分支，
    故「能不能上翻」一律用 baseY + scrollToTop() 判，不拿它当 PASS/FAIL 判据。
  - 状态字（v0.13.82 有、v0.13.83 已删）当年是异步自刷的，写 ?1049h 后要等一帧才准；
    现在直接读 `term.buffer.active.type`，同步即准，但**仍要等解析器跑完**（见下）。
  - 探针必须**自清理建的会话**：影子实例 MAX_SESSIONS=8，不清理第 9 次跑就以 429 告终。
"""
import json
import os
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

BASE = os.environ.get("HUB_PROBE_BASE", "http://127.0.0.1:3199")
PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9455"))
TOKEN = os.environ.get("HUB_PROBE_TERM_TOKEN", "probe-term-token")

RES = []
MADE = []      # 本探针建的会话，收尾一定要销毁：影子实例 MAX_SESSIONS=8，
               # 不复位就会在第 9 次运行时以 429「开不出来」告终（本轮踩过）。


def chk(name, ok, detail=""):
    RES.append(bool(ok))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("  —— " + str(detail)) if detail else ""))


def dismiss(cdp):
    try:
        cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception:  # noqa: BLE001
        pass


def ev(cdp, expr, awaitp=False):
    """取 Runtime.evaluate 的值。

    注意本仓 _cdp_min.CDP.send() 返回的**已经是 result 对象**（内部做过 m.get("result")），
    所以这里只再下一层；照抄别的探针写 r["result"]["result"] 会 KeyError 并被吞成 None。"""
    if awaitp:
        r = cdp.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return (r.get("result") or {}).get("value")
    return cdp.eval(expr)


SNAP = """(function(){var t=term;var b=t&&t.buffer&&t.buffer.active;
  return {type:b?b.type:null, vpY:b?b.viewportY:null, baseY:b?b.baseY:null, len:b?b.length:null,
          chip:document.getElementById('termBufChip'),
          altBtn:document.getElementById('termAltOut'),
          mouse:(t&&t.modes)?t.modes.mouseTrackingMode:null};})()"""


def snap(cdp):
    return ev(cdp, SNAP) or {}


def fill_and_scroll(cdp):
    """灌 300 行撑出 scrollback，把视口拉到顶，返回（scrollTop, 视口Y）对账用。"""
    ev(cdp, """(function(){var t=term;if(!t)return 0;
      for(var i=1;i<=300;i++)t.write('LINE-'+i+' 上翻测试 abcdefghijklmnop\\r\\n');
      return t.buffer.active.length;})()""")
    time.sleep(1.2)
    return ev(cdp, """(function(){var t=term;var vp=document.querySelector('.xterm-viewport');
      if(t.scrollToTop)t.scrollToTop();
      return {vpY:t.buffer.active.viewportY, st:vp?vp.scrollTop:null};})()""")


def scroll_probe(cdp):
    """用 xterm 自己的滚动语义量「能不能上翻」，绕开合成 wheel 事件的保真度问题。

    判据是可断言的量：
      baseY>0            ⇒ 这一屏**有** scrollback（备用屏恒 0，这是它的设计属性）
      scrollToTop() 后 viewportY 是否真的挪走 ⇒ 能否**真上翻**
    合成 WheelEvent 在 headless DOM 档下不保证走 xterm 的物理滚轮分支，故只作参考输出。"""
    return ev(cdp, """(function(){var t=term;if(!t)return null;var b=t.buffer.active;
      if(t.scrollToBottom)t.scrollToBottom();
      var atBottom=b.viewportY, base=b.baseY;
      if(t.scrollToTop)t.scrollToTop();
      var afterTop=b.viewportY;
      return {type:b.type, baseY:base, atBottom:atBottom, afterTop:afterTop,
              canScrollUp:(base>0 && afterTop<atBottom)};})()""")


def wheel_info(cdp, times=6):
    """仅供参考：合成 wheel 一格的位移（不参与 PASS/FAIL）。"""
    return ev(cdp, """(function(){var t=term;var vp=document.querySelector('.xterm-viewport');
      if(!vp||!t)return null;var before=t.buffer.active.viewportY;
      for(var i=0;i<%d;i++)vp.dispatchEvent(new WheelEvent('wheel',
        {deltaY:-120,bubbles:true,cancelable:true}));
      return {before:before, after:t.buffer.active.viewportY};})()""" % times)


def mk_session(cdp, agent="shell"):
    """在页面里走官方 API 建一条终端会话，结果挂 window 上再轮询取回。

    为什么不用 Runtime.evaluate 的 awaitPromise：实测那条路取回 None（请求其实成功了，
    shadow.log 里有 POST 200），白等一轮。轮询不依赖 CDP 的 Promise 语义，稳。"""
    ev(cdp, """(function(){window.__mk=null;
      fetch('/api/term/sessions',{method:'POST',
        headers:{'content-type':'application/json','x-term-token':%s},
        body:JSON.stringify({agent_id:%s})})
        .then(function(r){return r.json();})
        .then(function(d){window.__mk=(d&&d.session&&d.session.id)||'NOID';})
        .catch(function(e){window.__mk='ERR:'+e;});})()"""
       % (json.dumps(TOKEN), json.dumps(agent)))
    for _ in range(80):
        time.sleep(0.25)
        v = ev(cdp, "window.__mk")
        if v:
            if v == "NOID" or str(v).startswith("ERR:"):
                return None
            MADE.append(v)
            return v
    return None


def main():
    prof = tempfile.mkdtemp(prefix="altverify")
    proc = launch_chrome(BASE + "/", PORT, prof, 1280, 900)
    cdp = CDP(page_target(PORT), on_event=lambda m, p: dismiss(cdp))
    cdp.send("Page.enable")
    cdp.send("Page.navigate", url=BASE + "/")
    time.sleep(1.5)
    ev(cdp, "localStorage.setItem('hub.term.token', %s)" % json.dumps(TOKEN))
    cdp.send("Page.reload", ignoreCache=True)
    time.sleep(3.0)

    ev(cdp, "go('chat')")
    time.sleep(0.5)

    # 直接走官方 API 建会话（不依赖 UI 选实体），再复用页面自己的 termConnect
    sid = mk_session(cdp)
    if not sid:
        chk("建终端会话", False, "API 未返回 session.id（影子实例起没起？TERM_TOKEN 对不对？）")
        return finish(cdp, proc)
    chk("建终端会话", True, "sid=%s" % sid)
    ev(cdp, "termConnect(%s, 'shell', {user:true})" % json.dumps(sid))
    time.sleep(2.5)
    ev(cdp, "ensureTerm()")
    time.sleep(0.6)

    s = snap(cdp)
    chk("B1 新会话落在主屏", s.get("type") == "normal", s)
    chk("B7 DOM 里已无 #termBufChip / #termAltOut（UI 真删干净）",
        s.get("chip") is None and s.get("altBtn") is None,
        "chip=%r altBtn=%r" % (s.get("chip"), s.get("altBtn")))

    # 撑出 scrollback，确认「主屏可上翻」这一基线成立
    fill_and_scroll(cdp)
    s1b = scroll_probe(cdp)
    chk("B2 基线：主屏有 scrollback 且能上翻", bool(s1b) and s1b.get("canScrollUp"),
        "scroll=%s wheel(参考)=%s" % (s1b, wheel_info(cdp)))
    base_before = s1b

    # ── 核心翻转：本该「进得去」的序列，现在必须进不去 ─────────────────
    ev(cdp, "term.write('\\x1b[?1049h')")
    time.sleep(0.8)                     # 留足解析器跑完的时间（term.write 是异步进 parser 的）
    s = snap(cdp)
    chk("B3 写 ?1049h 后**仍在主屏**（备用屏永不出现）", s.get("type") == "normal", s)

    # ── B8 逐参点名：三个变体都拦、邻居都放行 ────────────────────────
    # 刻意**不读 parser 私有表**（`_csiHandlers[key]` 的键是 `_collect<<8|final`，
    # 且 ParserApi 还包了一层 toArray 适配——照抄内部形状写出来的判据会随版本升级静默失灵）。
    # 改成「写进去、看状态」，与用户真实路径同构。1049 由 B3 覆盖，这里补齐另两个变体
    # 与三个**必须放行**的邻居（鼠标/粘贴；同步块 2026 无公开观测点故不入判据）。
    for seqs, want in ((["\\x1b[?1047h"], "normal"), (["\\x1b[?47h"], "normal")):
        ev(cdp, "term.write('%s')" % "".join(seqs))
        time.sleep(0.6)
        got = snap(cdp).get("type")
        chk("B8 备用屏变体 %s 也被拦（仍在主屏）" % seqs[0].replace("\\x1b[?", "?"), got == want, got)
    # ?2004h 必须放行 —— 它是 bracketed paste（多行粘贴的正确性依赖它）。
    ev(cdp, "term.write('\\x1b[?2004h')")
    time.sleep(0.6)
    bp = ev(cdp, "(function(){try{return term.modes.bracketedPasteMode;}catch(e){return 'ERR';}})()")
    ev(cdp, "term.write('\\x1b[?2004l')")
    time.sleep(0.3)
    chk("B8 ?2004h 照常生效（粘贴模式未被误伤）", bp is True, bp)

    # ── B9 输入控件对鼠标失效（要求 1b 的行为判据，不是「CSS 里写了一行」）──
    # 用户口径：终端页的文字输入控件不吃鼠标，鼠标只保留页面/scrollback 滚动。
    # 两个控件的判定方式不同，因为它们的「可点」来源不同：
    #   · #termFindInput —— 走我们自己的 CSS 规则，且**按开/关分档**（用户 2026-10-07 追认）：
    #     关闭时（日常所有时刻）整条浮层 pointer-events:none ⇒ 鼠标完全穿透、滚轮不被截走；
    #     打开时（.term-find.on）恢复 auto ⇒ Ctrl+F 期间鼠标照常可用。
    #     所以这条**两个状态都要量**——只验关闭态会漏掉"打开后还能不能用鼠标"，
    #     只验打开态则漏掉本次要修的滚轮穿透。
    #   · .xterm-helper-textarea —— vendor 的静态 CSS 只保证**静息态** 0×0；
    #     组字期间 updateCompositionElements 会把它挪到光标处并撑开（inline style 压过 class）
    #     ⇒ 必须在运行时钉 style.pointerEvents。所以这里读的是 **effective 值**
    #     （getComputedStyle + inline），而不是「CSS 里有没有那行」。
    def pe_of(cdp):
        return ev(cdp, """(function(){
          var ta=document.querySelector('.xterm-helper-textarea');
          var fi=document.getElementById('termFindInput');
          var box=document.getElementById('termFind');
          return {ta: ta ? {inline: ta.style.pointerEvents||'(unset)',
                            eff: getComputedStyle(ta).pointerEvents} : null,
                  fi: fi ? {inline: fi.style.pointerEvents||'(unset)',
                            eff: getComputedStyle(fi).pointerEvents} : null,
                  bar: box ? getComputedStyle(box).pointerEvents : null,
                  open: box ? box.classList.contains('on') : null};})()""")
    pe = pe_of(cdp)
    chk("B9a xterm helper textarea 对鼠标失效（effective none）",
        bool(pe) and pe.get("ta") and pe["ta"]["eff"] == "none", pe and pe.get("ta"))
    chk("B9b 查找框**关闭**时对鼠标失效（整条浮层 effective none）",
        bool(pe) and pe.get("fi") and pe["fi"]["eff"] == "none"
        and pe.get("bar") == "none" and pe.get("open") is False, pe)
    # 打开后必须能用鼠标 —— 否则等于用"修滚轮"顺手把 Ctrl+F 拆了半边。
    ev(cdp, "termFindOpen()")
    time.sleep(0.5)
    po = pe_of(cdp)
    chk("B9c 查找框**打开**时鼠标可用（input/浮层均 effective auto）",
        bool(po) and po.get("open") is True and po.get("fi") and po["fi"]["eff"] == "auto"
        and po.get("bar") == "auto", po)
    ev(cdp, "termFindClose()")
    time.sleep(0.4)
    pc = pe_of(cdp)
    chk("B9d 关闭后又回到穿透（状态可逆，不是一次性）",
        bool(pc) and pc.get("open") is False and pc.get("fi") and pc["fi"]["eff"] == "none", pc)

    fill_and_scroll(cdp)
    s4 = scroll_probe(cdp)
    chk("B4 写 ?1049h 后 scrollback 与上翻能力不变（没被偷走）",
        bool(s4) and s4.get("baseY", 0) > 0 and s4.get("canScrollUp"),
        "before=%s after=%s" % (base_before, s4))

    # ── B5 精确点名：拦的是 1049，不是 DECSET 全家 ────────────────────
    # ?1003h（鼠标 any-event 跟踪）必须照常生效 —— 否则「吞 ?h 全部」会把
    # 鼠标/粘贴/同步块一起闷掉，那是比备用屏严重得多的破坏。
    ev(cdp, "term.write('\\x1b[?1049h\\x1b[?1003h')")
    time.sleep(0.8)
    s5 = snap(cdp)
    chk("B5 混合序列：仍在主屏 **且** ?1003h 照常生效（只点名 1049，未误伤 DECSET）",
        s5.get("type") == "normal" and s5.get("mouse") == "any",
        "type=%s mouse=%s" % (s5.get("type"), s5.get("mouse")))

    # ── B6 换会话后仍然稳定在主屏 ────────────────────────────────────
    sid2 = mk_session(cdp)
    if not sid2:
        chk("B6 切到另一条会话", False, "第二条会话没建起来")
    else:
        ev(cdp, "termConnect(%s, 'shell', {user:true})" % json.dumps(sid2))
        time.sleep(2.5)
        s6 = snap(cdp)
        chk("B6 切会话后仍在主屏", s6.get("type") == "normal", s6)
        fill_and_scroll(cdp)
        s7 = scroll_probe(cdp)
        chk("B6b 切会话后仍能上翻（scrollback 真在）",
            bool(s7) and s7.get("canScrollUp"), "scroll=%s" % s7)

    return finish(cdp, proc)


def finish(cdp, proc):
    ok = sum(1 for x in RES if x)
    print("\n%d/%d PASS" % (ok, len(RES)))
    for s in MADE:            # 幂等自清理：下次复跑才不会撞 MAX_SESSIONS(8) 的 429
        try:
            rq = urllib.request.Request(BASE + "/api/term/sessions/" + s, method="DELETE",
                                        headers={"x-term-token": TOKEN})
            urllib.request.urlopen(rq, timeout=10).read()
        except Exception:  # noqa: BLE001
            pass
    try:
        cdp.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass
    sys.exit(0 if ok == len(RES) else 1)


if __name__ == "__main__":
    main()