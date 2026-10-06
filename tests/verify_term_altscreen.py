#!/usr/bin/env python3
"""「跑一次 codex 后所有终端都不能向上滚动」修复验收探针（v0.13.82）。

背景（一句话）：codex 的 TUI 发 \\x1b[?1049h 进备用屏，而 xterm.js 的备用屏按设计
没有 scrollback；整页共用一个 xterm 实例，切会话的 term.clear() 又不退出备用屏
⇒ 一次 codex 把整页拖进备用屏，谁来都滚不动。

判据（每条都可断言，不以「我看了截图」交差）：
  A1 新建会话后本端在**主屏**（normal）
  A2 写 \\x1b[?1049h 后进入交替屏 —— 先把「红」条件立起来
  A3 交替屏下滚轮**不改变** viewportY（= 没有 scrollback，正是用户报的「滚不动」）
  A4 状态字显示「备用屏·不可上翻」（③ 端侧可观测）
  A5 点 #termAltOut 后回到主屏（③ 逃生口有效）
  A6 再造一次污染，然后切到**另一条会话**（termNew）
     ⇒ 回放帧里的 TERM_STATE_RESET 把本端拉回主屏（① 跨会话污染修复，本次核心）
  A7 复位后滚轮**能**改变 viewportY（真正恢复上翻能力）

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
  - 状态字是 1.5s 自刷 + 动作即时刷：写完 ?1049h 要立刻断言时先显式调 termAltChipSync()，
    否则量到的是上一轮的值。
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
          chip:(document.getElementById('termBufChip')||{}).textContent||null,
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
    chk("A1 新会话落在主屏", s.get("type") == "normal", s)

    # 撑出 scrollback，确认「主屏可上翻」这一基线成立
    fill_and_scroll(cdp)
    s1b = scroll_probe(cdp)
    chk("A1b 基线：主屏有 scrollback 且能上翻", bool(s1b) and s1b.get("canScrollUp"),
        "scroll=%s wheel(参考)=%s" % (s1b, wheel_info(cdp)))

    # ── 造红：进备用屏 ────────────────────────────────────────────────
    ev(cdp, "term.write('\\x1b[?1049h')")
    time.sleep(0.6)
    ev(cdp, "termAltChipSync()")          # 状态字是 1.5s 自刷 + 动作即时刷，这里取即时那条
    s = snap(cdp)
    chk("A2 写 ?1049h 后进入交替屏", s.get("type") == "alternate", s)
    chk("A4 状态字报「备用屏·不可上翻」", s.get("chip") == "备用屏·不可上翻", s.get("chip"))

    fill_and_scroll(cdp)
    s3 = scroll_probe(cdp)
    chk("A3 交替屏没有 scrollback（baseY=0，上翻不可能）",
        bool(s3) and s3.get("baseY") == 0 and not s3.get("canScrollUp"), "scroll=%s" % s3)

    # ── A5 逃生口 ────────────────────────────────────────────────────
    ev(cdp, "document.getElementById('termAltOut').click()")
    time.sleep(0.5)
    s5 = snap(cdp)
    chk("A5 点「退出备用屏」回到主屏", s5.get("type") == "normal", s5)

    # ── A6 核心：切会话时的跨会话复位 ─────────────────────────────────
    ev(cdp, "term.write('\\x1b[?1049h')")
    time.sleep(0.5)
    armed = snap(cdp)
    chk("A6a 再造污染（attacked）", armed.get("type") == "alternate", armed)
    sid2 = mk_session(cdp)
    if not sid2:
        chk("A6 切到另一条会话", False, "第二条会话没建起来")
    else:
        ev(cdp, "termConnect(%s, 'shell', {user:true})" % json.dumps(sid2))
        time.sleep(2.5)
        s6 = snap(cdp)
        chk("A6 切会话后自动回到主屏（跨会话污染已修）", s6.get("type") == "normal", s6)
        fill_and_scroll(cdp)
        s7 = scroll_probe(cdp)
        chk("A7 复位后 scrollback 与上翻能力真恢复",
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