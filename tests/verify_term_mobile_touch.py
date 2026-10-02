#!/usr/bin/env python3
"""L2 真机闸门：终端移动端触摸层 + auth_url 旁路（v0.13.64）。

为什么静态测试不够：tests/test_term_touch_authurl.py 只能证明"代码写了、拼进产物了"，
证明不了**真机能滚、真机能选、真机不误弹链接**。而这三条恰好是用户唯一能感知的判据。
沿用 tests/verify_term_scroll.py 的纪律：判据必须是可断言的量，**不以截图交差**。

跑法（先起影子实例；worktree 无 .env，务必 setsid 否则 shell 退出连带杀）：
    ( cd <repo> && mkdir -p work/probe/data && \
      setsid env DATA_DIR=$PWD/work/probe/data PORT=3199 HOST=127.0.0.1 \
        HUB_WRITE_TOKEN=probe-token TERM_TOKEN=probe-term-token \
        venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 \
        > work/probe/touch.log 2>&1 < /dev/null & )
    HUB_PROBE_CDP_PORT=9413 venv/bin/python tests/verify_term_mobile_touch.py http://127.0.0.1:3199
收尾：fuser -k 3199/tcp    # 禁用 pkill -f "3199"，会连带杀自己

判据分组（每条可证伪）：
  T1-T3  触摸：单指拖动确有位移；长按出选区；捏合改字号且 clamp
  W1     auth_url：旁路帧到达且**输出流未被污染**（画面里不出现 JSON 原文）
  D1-D2  桌面零回归：1440px 下触摸层不绑定；反复 ensureTerm 不重复绑定

踩过的坑（改这几行前先读）：
  - **`term` 是全局词法作用域**（`let term` + 'use strict'），`window.term` 取到 undefined，
    必须用裸 `term` 表达式求值。
  - 必须先 `go('chat')` 再 `switchMode('term')`：终端在 page-chat 里，只切模式不够。
  - Chromium headless 默认 pointer:fine ⇒ 触摸层**不会**绑定。探针必须显式
    `Emulation.setTouchEmulationEnabled` + `Emulation.setEmitTouchEventsForMouse`，
    否则 T 组会假红（这是本脚本最容易踩的一坑）。
  - `Input.dispatchTouchEvent` 的 touchPoints 必须**带 id**，缺 id 会被静默丢弃。
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199").rstrip("/")
TERM_TOKEN = os.getenv("TERM_TOKEN", "probe-term-token")
CDP_PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9413"))
LINES = 400
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  —— " + str(detail)) if detail else ""))


def dismiss(cdp):
    try:
        cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception:
        pass


def boot(width=390, height=844, touch=True):
    """起浏览器并把终端铺满。touch=True 时开触摸仿真（headless 默认 fine）。"""
    prof = tempfile.mkdtemp(prefix="termtouch")
    proc = launch_chrome(BASE + "/", CDP_PORT, prof, width, height)
    cdp = CDP(page_target(CDP_PORT), on_event=lambda m, p: dismiss(cdp))
    cdp.send("Page.enable")
    if touch:
        cdp.send("Emulation.setDeviceMetricsOverride", width=width, height=height,
                 deviceScaleFactor=1, mobile=True)
        cdp.send("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=5)
    cdp.send("Page.navigate", url=BASE + "/")
    time.sleep(2.0)
    cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
    cdp.send("Page.reload", ignoreCache=True)
    time.sleep(2.5)
    cdp.eval("go('chat')")
    time.sleep(0.6)
    cdp.eval("switchMode('term')")
    time.sleep(0.6)
    cdp.eval("ensureTerm()")
    time.sleep(2.0)
    # 灌足够多的行，scrollback 才有可滚的内容
    cdp.eval("(function(){var s='';for(var i=1;i<=%d;i++) s+='L'+i+'\\r\\n'; term.write(s); return 1;})()" % LINES)
    time.sleep(2.5)
    return proc, cdp


def screen_box(cdp):
    return cdp.eval("""(function(){ var e=document.querySelector('#termEl .xterm-screen');
        if(!e) return null; var r=e.getBoundingClientRect();
        return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2),
                top:Math.round(r.top), h:Math.round(r.height)}; })()""")


def touch_seq(cdp, pts, kind="touchStart"):
    """派发一条触摸序列。pts = [{x,y,id}]；id 必须有，否则被静默丢弃。"""
    cdp.send("Input.dispatchTouchEvent", type=kind,
             touchPoints=[{"x": p["x"], "y": p["y"], "id": p.get("id", 1)} for p in pts])


# 页面内 capture 阶段探针：产品监听器挂在 #termEl（target 阶段），window+capture 先跑，
# 此时 ttState 还没被产品置空 ⇒ 能读到**产品真正用来判甩动的那两个量**（idle/v）。
# 为什么要它：2026-10-02 排 T2b 抖动时，光看 viewportY 分不清"惯性没起"和"起了但被
# 逐帧取整吞掉"——两个假设都能产出 0 行。带上 idle/v，红了就是红的，不靠猜。
FLING_DIAG_JS = """
(function(){
  if (window.__flingDiag) return 'already';
  window.__flingDiag = [];
  window.addEventListener('touchend', function(){
    var s = window.ttState;
    window.__flingDiag.push({idle: s ? Math.round(Date.now() - s.lastT) : null,
                             v: s ? Math.round(s.v) : null, mode: s ? s.mode : null});
  }, true);
  return 'ok';
})()
"""


def fling_diag(cdp):
    """取最近一次松手时产品侧读到的 {idle, v}；取不到就返回 '-'（不能因此判 PASS）。"""
    d = cdp.eval("(function(){var a=window.__flingDiag||[];return a.length?a[a.length-1]:null})()")
    return "idle=%sms v=%spx/s" % (d.get("idle"), d.get("v")) if isinstance(d, dict) else "产品侧未读到"


def drag(cdp, box, dy_total, steps=12):
    """单指从 box 中间往上/下拖 dy_total 像素。"""
    x, y0 = box["x"], box["y"]
    touch_seq(cdp, [{"x": x, "y": y0}])
    time.sleep(0.05)
    for i in range(1, steps + 1):
        y = y0 + dy_total * i / float(steps)
        touch_seq(cdp, [{"x": x, "y": y}], "touchMove")
        time.sleep(0.02)
    touch_seq(cdp, [{"x": x, "y": y0 + dy_total}], "touchEnd")


def main():
    print("L2 真机：终端触摸层 + auth_url（%s）" % BASE)
    proc, cdp = boot()
    try:
        box = screen_box(cdp)
        if not box:
            check("T0 终端已渲染", False, "找不到 .xterm-screen")
            return
        check("T0 终端已渲染", True)

        # ── T1 触摸层确实绑上了 ──
        bound = cdp.eval("(function(){var e=document.getElementById('termEl');"
                         "return e?e.dataset.touchBound||'':'';})()")
        check("T1 触摸层已绑定", bound == "1", "touchBound=%r" % bound)
        cdp.eval(FLING_DIAG_JS)       # 装上松手取证探针（判红时要靠它区分根因）

        # ── T2 单指拖动 ⇒ viewportY 真变（不是"看着动了"）──
        cdp.eval("term.scrollToBottom()")
        time.sleep(0.4)
        before = cdp.eval("term.buffer.active.viewportY")
        drag(cdp, box, -160)          # 手指往上滑 ⇒ 看更新的内容
        time.sleep(0.6)
        after = cdp.eval("term.buffer.active.viewportY")
        moved = isinstance(before, int) and isinstance(after, int) and (before - after) > 0
        check("T2 单指拖动改变视口", moved, "viewportY %s → %s" % (before, after))

        # ── T2b 惯性：松手后**继续**滚（这才是 xterm 缺的，也是本层的存在理由）──
        cdp.eval("term.scrollToBottom()")
        time.sleep(0.4)
        b2 = cdp.eval("term.buffer.active.viewportY")
        drag(cdp, box, -200, steps=6)
        time.sleep(0.05)              # 几乎立刻取样：要看到"松手后还在动"
        mid = cdp.eval("term.buffer.active.viewportY")
        time.sleep(1.2)
        end = cdp.eval("term.buffer.active.viewportY")
        # 判据是**余量**而非 ">0"：这条甩动是"刚好过阈值"的轻甩（v≈-1000px/s，
        # 每帧位移不足半行）。惯性若按帧取整而不累积，尾巴会被整段吞掉 ⇒ 0~2 行抖动；
        # 累积后总位移 ≈ 0.27·v ≈ 10 行。取 3 行做线：既高于坏值(0~2)，又离好值(≈10)够远。
        coast = ((b2 - end) - (b2 - mid)) if all(isinstance(x, int) for x in (b2, mid, end)) else -1
        check("T2b 松手后有惯性滑行", coast >= 3,
              "松手后再滑 %s 行（要求≥3）｜viewportY %s →(松手)%s →(停)%s｜%s"
              % (coast, b2, mid, end, fling_diag(cdp)))

        # ── T3 长按 ⇒ 出选区 ──
        # 先滚到有内容的中段并保证视口贴顶：selectLines 用的是 buffer 绝对行号，
        # 若视口在底部(baseY)而长按落在屏幕上方，算出的行号会落在有效范围外。
        cdp.eval("term.scrollToTop()")
        time.sleep(0.5)
        box = screen_box(cdp)
        touch_seq(cdp, [{"x": box["x"], "y": box["y"]}])
        time.sleep(0.9)               # > TT_LONG_PRESS_MS(600)
        touch_seq(cdp, [{"x": box["x"], "y": box["y"]}], "touchEnd")
        time.sleep(0.4)
        sel = cdp.eval("term.getSelection()")
        check("T3 长按产生选区", bool(sel), "selection=%r" % (sel,))

        # ── T4 捏合 ⇒ 字号变且 clamp 在 [8,48] ──
        f0 = cdp.eval("term.options.fontSize")
        cx, cy = box["x"], box["y"]
        touch_seq(cdp, [{"x": cx - 40, "y": cy, "id": 1}, {"x": cx + 40, "y": cy, "id": 2}])
        for k in range(1, 9):
            d = 40 + k * 22
            touch_seq(cdp, [{"x": cx - d, "y": cy, "id": 1},
                            {"x": cx + d, "y": cy, "id": 2}], "touchMove")
            time.sleep(0.06)
        touch_seq(cdp, [], "touchEnd")
        time.sleep(0.5)
        f1 = cdp.eval("term.options.fontSize")
        check("T4 捏合放大字号", isinstance(f0, (int, float)) and isinstance(f1, (int, float))
              and f1 > f0, "fontSize %s → %s" % (f0, f1))
        # 连捏到最大也不能越界
        for _ in range(6):
            touch_seq(cdp, [{"x": cx - 60, "y": cy, "id": 1}, {"x": cx + 60, "y": cy, "id": 2}])
            for k in range(1, 7):
                d = 60 + k * 30
                touch_seq(cdp, [{"x": cx - d, "y": cy, "id": 1},
                                {"x": cx + d, "y": cy, "id": 2}], "touchMove")
                time.sleep(0.05)
            touch_seq(cdp, [], "touchEnd")
            time.sleep(0.2)
        fmax = cdp.eval("term.options.fontSize")
        check("T4b 字号 clamp 上界", isinstance(fmax, (int, float)) and 8 <= fmax <= 48,
              "fontSize=%s" % fmax)

        # ── T5 字号改了必须重发尺寸（否则前后端脱节 ⇒ 输入错位）──
        synced = cdp.eval("(function(){ var el=document.getElementById('termEl');"
                          "var cs=getComputedStyle(document.documentElement);"
                          "return String(cs.getPropertyValue('--term-fs')).trim(); })()")
        check("T5 CSS 变量与 term 同步", str(fmax) + "px" == str(synced).replace(" ", ""),
              "css=%s term=%s" % (synced, fmax))

        # ── W1 auth_url：画面里**不能**出现 JSON 原文 ──
        cdp.eval("(function(){ term.clear(); term.write('\\r\\nready\\r\\n'); return 1;})()")
        time.sleep(0.6)
        cdp.eval("termAuthUrl('https://login.example.com/device?code=ABC123', false)")
        time.sleep(0.6)
        painted = cdp.eval("""(function(){ var s='';
            for (var i=0;i<term.rows;i++){ var l=term.buffer.active.getLine(i);
              if(l) s+=l.translateToString(false); } return s; })()""")
        leaked = ('"type"' in painted) or ("auth_url" in painted)
        check("W1 auth_url 不污染画面", not leaked,
              "画面含 JSON 原文：%r" % (painted[-120:] if leaked else ""))
        has_note = "login.example.com" in painted
        check("W2 auth_url 在画面留可复制纯文本", has_note,
              "画面=%r" % painted[-140:])

        # ── D1 反复 ensureTerm 不重复绑定（监听器不泄漏）──
        for _ in range(6):
            cdp.eval("termDetach(); ensureTerm();")
            time.sleep(0.5)
        bound2 = cdp.eval("(function(){var e=document.getElementById('termEl');"
                          "return e?e.dataset.touchBound||'':'';})()")
        check("D1 反复重建后仍单次绑定", bound2 == "1", "touchBound=%r" % bound2)

        # ── D2 桌面视口：触摸层不绑定（宽屏零回归）──
        # 必须**重新导航**而不是只改 metrics：同一浏览器上下文里 pointer:coarse /
        # maxTouchPoints 已经被 touch 仿真改过，termTouchBind 的 dataset 闸门也会
        # 一直留着标记 ⇒ 只切视口必然假红（第一版就这么翻车，判据改成"新上下文"）。
        cdp.send("Emulation.setTouchEmulationEnabled", enabled=False)
        cdp.send("Emulation.clearDeviceMetricsOverride")
        cdp.send("Page.navigate", url=BASE + "/")
        time.sleep(2.0)
        cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
        cdp.send("Page.reload", ignoreCache=True)
        time.sleep(2.5)
        coarse = cdp.eval("(function(){try{return matchMedia('(pointer: coarse)').matches}catch(e){return 'err'}})()")
        max_tp = cdp.eval("navigator.maxTouchPoints")
        cdp.eval("go('chat')")
        time.sleep(0.5)
        cdp.eval("switchMode('term')")
        time.sleep(0.5)
        cdp.eval("ensureTerm()")
        time.sleep(1.5)
        desk_bound = cdp.eval("(function(){var e=document.getElementById('termEl');"
                              "return e?(e.dataset.touchBound||'none'):'noel';})()")
        check("D2 桌面视口不绑定触摸层",
              desk_bound in ("none", "noel") and not coarse and not max_tp,
              "touchBound=%r coarse=%r maxTouchPoints=%r" % (desk_bound, coarse, max_tp))
        desk_ok = cdp.eval("typeof term !== 'undefined' && !!term && term.rows > 0")
        check("D3 桌面终端仍可用", bool(desk_ok))
    finally:
        try:
            proc.kill()
        except Exception:
            pass

    ok = sum(1 for r in RESULTS if r)
    print("\n%d/%d 通过" % (ok, len(RESULTS)))
    sys.exit(0 if ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()