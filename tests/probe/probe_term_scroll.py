#!/usr/bin/env python3
"""终端滚动取证探针（一次性诊断，非闸门）。

要回答的问题只有一个：**「终端无法上翻」到底断在哪一环？**
按 AGENTS.md 浮层/布局铁律：布局类报障必须真渲染取证、判据是可断言的量，
不用截图交差。本探针把链路拆成四段，逐段打点：

  ① 数据在不在      buffer.baseY / ybase > 0  ⇒ scrollback 真的有历史
  ② 视口模型能不能动  手动写 buffer.ydisp 后 ydisp 读回是否变化（排除只读属性）
  ③ 滚轮事件到不到    真派发 wheel 事件后 ydisp 变没变（区分「事件没送达」vs「送达了不生效」）
  ④ 滚动条 DOM 在不在  .xterm-viewport 的 scrollHeight/clientHeight/scrollTop

跑法（影子实例须已起）：
    ( cd <worktree> && mkdir -p work/probe/data && \
      setsid env DATA_DIR=$PWD/work/probe/data PORT=3199 HOST=127.0.0.1 \
        HUB_WRITE_TOKEN=probe-token TERM_TOKEN=probe-term-token \
        ../agent-hub/venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 \
        > work/probe/shadow.log 2>&1 < /dev/null & )
    HUB_PROBE_CDP_PORT=9412 ../agent-hub/venv/bin/python tests/probe_term_scroll.py http://127.0.0.1:3199
收尾：fuser -k 3199/tcp
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
CDP_PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9412"))
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
H = 900


def page_dismiss(cdp):
    try:
        cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception:
        pass


def main():
    prof = tempfile.mkdtemp(prefix="scrollprof")
    proc = launch_chrome(BASE + "/", CDP_PORT, prof, 1200, H)
    cdp = CDP(page_target(CDP_PORT), on_event=lambda m, p: page_dismiss(cdp))
    try:
        cdp.send("Page.enable")
        cdp.send("Page.navigate", url=BASE + "/")
        time.sleep(2.0)
        cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
        cdp.send("Page.reload", ignoreCache=True)
        time.sleep(2.5)

        # 进终端页（踩坑记录：必须先 go('chat') 再 switchMode('term')，
        # 否则 #termEl 的 getBoundingClientRect() 全 0，量到 0x0 会误判成「终端挂了」）
        cdp.eval("go('chat')")
        time.sleep(0.6)
        cdp.eval("switchMode('term')")
        time.sleep(0.6)
        cdp.eval("ensureTerm()")
        time.sleep(2.0)

        # 造 2000 行历史（远超默认 24 行视口 ⇒ 必有 scrollback）
        cdp.eval("""
          (function(){
            if (!term) return 'no-term';
            var s = '';
            for (var i = 1; i <= 2000; i++) { s += 'SCROLLPROBE-LINE-' + i + '\\r\\n'; }
            term.write(s);
            return 'written';
          })()
        """)
        time.sleep(2.5)

        # ① 数据在不在
        a = cdp.eval("""
          (function(){ if (!term || !term.buffer) return {err:'no-term'};
            var b = term.buffer.active;
            return {baseY: b.baseY, ybase: b.length, ydisp: b.ydisp,
                    cursorY: b.cursorY, rows: term.rows,
                    scrollbackOpt: term.options && term.options.scrollback,
                    renderer: ((typeof termRendererName === "undefined" ? "?" : termRendererName))}; })()
        """)
        print("[① 数据层] " + json.dumps(a, ensure_ascii=False))

        # ② 视口模型能不能动（直接写 ydisp）
        b = cdp.eval("""
          (function(){ if (!term) return {err:'no-term'};
            var b = term.buffer.active, before = b.ydisp;
            var target = Math.max(0, b.baseY - 10);
            var setok;
            try { b.ydisp = target; setok = true; } catch (e) { setok = 'throw:' + e.message; }
            var after = b.ydisp;
            // scrollToTop 是公开 API，用它再验一次
            var apiok;
            try { term.scrollToTop(); apiok = b.ydisp; } catch (e) { apiok = 'throw:' + e.message; }
            return {before: before, target: target, after: after, setok: setok,
                    scrollToTopYdisp: apiok, baseY: b.baseY}; })()
        """)
        print("[② 视口模型] " + json.dumps(b, ensure_ascii=False))

        # ③ 滚轮事件到不到（真派发 wheel，不是调 API）
        c = cdp.eval("""
          (function(){ if (!term) return {err:'no-term'};
            term.scrollToBottom(); return {reset: term.buffer.active.ydisp}; })()
        """)
        time.sleep(0.5)
        # 用 CDP 派发真实鼠标滚轮（Input.dispatchMouseEvent type=mouseWheel）
        box = cdp.eval("""
          (function(){ var e = document.querySelector('#termEl .xterm-screen') || document.querySelector('#termEl .xterm');
            if (!e) return null; var r = e.getBoundingClientRect();
            return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)}; })()
        """)
        print("[③-前置] 滚轮落点 " + json.dumps(box, ensure_ascii=False))
        if box:
            for i in range(6):
                cdp.send("Input.dispatchMouseEvent", type="mouseWheel",
                         x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
                time.sleep(0.15)
            time.sleep(1.0)
            d = cdp.eval("""
              (function(){ if (!term) return {err:'no-term'};
                var b = term.buffer.active;
                return {ydispAfterWheelUp: b.ydisp, baseY: b.baseY,
                        viewportScrollTop: (function(){ var v = document.querySelector('.xterm-viewport');
                          return v ? {scrollTop: v.scrollTop, scrollHeight: v.scrollHeight, clientHeight: v.clientHeight} : null; })()}; })()
            """)
            print("[③ 滚轮事件] " + json.dumps(d, ensure_ascii=False))
            print("    判定：滚轮 deltaY=-120 × 6（向上）⇒ ydisp 应从 baseY 下降。")
            print("    实际：%s" % ("有效" if isinstance(d, dict) and isinstance(d.get("ydispAfterWheelUp"), int)
                                 and d.get("baseY") and d["ydispAfterWheelUp"] < d["baseY"] else "无效"))
        else:
            print("[③ 滚轮事件] 跳过：找不到 .xterm-screen/.xterm 元素")

        # ④ 滚动条 DOM
        e = cdp.eval("""
          (function(){
            var out = {};
            var v = document.querySelector('.xterm-viewport');
            out.viewportExists = !!v;
            if (v) { var cs = getComputedStyle(v);
              out.overflowY = cs.overflowY; out.scrollTop = v.scrollTop;
              out.scrollHeight = v.scrollHeight; out.clientHeight = v.clientHeight;
              out.canScroll = v.scrollHeight > v.clientHeight; }
            var s = document.querySelector('.xterm-screen');
            out.screenExists = !!s;
            if (s) { var r = s.getBoundingClientRect();
              out.screenRect = {w: Math.round(r.width), h: Math.round(r.height)}; }
            var t = document.querySelector('#termEl');
            out.termElRect = t ? (function(){ var r = t.getBoundingClientRect();
              return {w: Math.round(r.width), h: Math.round(r.height)}; })() : null;
            out.termRows = (typeof term === "undefined" ? null : term.rows);
            out.termCols = (typeof term === "undefined" ? null : term.cols);
            return out; })()
        """)
        print("[④ 滚动条 DOM] " + json.dumps(e, ensure_ascii=False))
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()
