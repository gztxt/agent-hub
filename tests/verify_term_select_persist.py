#!/usr/bin/env python3
"""L2 live 真渲染闸门：终端「选区抗回装 + 右键菜单」（v0.13.92，PT-20261008-03）。

跑法（影子实例或生产，真 chromium + CDP）：
    ( cd <worktree> && mkdir -p work/probe/data && \
      setsid env DATA_DIR=$PWD/work/probe/data PORT=3199 HOST=127.0.0.1 \
        HUB_WRITE_TOKEN=probe-token TERM_TOKEN=probe-term-token \
        ../agent-hub/venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 \
        > work/probe/shadow.log 2>&1 < /dev/null & )
    HUB_PROBE_TERM_TOKEN=probe-term-token python tests/verify_term_select_persist.py http://127.0.0.1:3199

为什么必须真渲染：本缺陷的机制是 **焦点/xterm 内部协议状态**（SelectionService.disable →
clearSelection），DOM 里没有任何静态痕迹可断言；右键菜单的显隐与可用性也是运行时状态。

判据（每条可证伪）：
  A0  前提：会话是「声明了鼠标跟踪」的 TUI（termMouseWant 非空 / mode≠none）——
      否则 termMouseArm 本就早退，测不到本缺陷。
  A1  程序化建选区后调 termMouseArm()，选区**仍在**（修复前会被清空）。
  A2  仿真 app 重断言：建选区后写 ?1003h，选区仍在（有选区时 DECSET 被吞）。
  B1  真鼠标拖选 + 松开：选区仍在且文本非空（端到端复现用户动作）。
  C1  终端上右键 ⇒ #termCtx 出现，且恰好三个按钮。
  C2  有选区时「复制」可点；无选区时置灰。
  D1  点「复制」⇒ 复制通道被调用且承载的是选区文本；菜单随即关闭。
  D2  真 Ctrl+C（键事件）⇒ 复制通道承载选区文本（与 attachCustomKeyEventHandler 同一条）。
  D3  容器 copy 兜底：浏览器原生 copy 事件打到 #termEl ⇒ 写入选区文本（与系统菜单复制同路）。
  D4  **负控**：无选区时容器 copy 不得被接管（prevented=False、无数据）——防 D3 假绿。
  E1  菜单外 pointerdown ⇒ 关闭（点空白逃生）。
  E2  Escape ⇒ 关闭。
  F   全程零 JS 异常。

红向基线（无脚本开关，直接打到未修复实例）：把本探针打到 **v0.13.91** 实例
（生产 `:3102`，或从主 checkout 起的 pre-fix 影子）⇒ A1/A2/B1/C*/D* 全红
（实测 9 项 FAIL）—— 那才是用户报障形态的原样复现；打到修复态（本 worktree 影子）
则全绿。一红一绿即证明本探针真的在测这两处修复，而不是恒绿。
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
TOK = os.environ.get("HUB_PROBE_TERM_TOKEN", "")
PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9463"))
FAILED = []


def chk(name, ok, detail=""):
    print(("  PASS " if ok else "  FAIL ") + name + ("  " + detail if detail else ""))
    if not ok:
        FAILED.append(name)
    return bool(ok)


def key(cdp, k, code, vk, mods):
    for t in ("keyDown", "keyUp"):
        cdp.send("Input.dispatchKeyEvent", type=t, key=k, code=code,
                 windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk, modifiers=mods)


#: 浏览器原生 copy 事件打到 #termEl（与「右键菜单复制/系统菜单复制」同一条路）。
COPY_EVENT_JS = r"""(function(){
  var ta = term.textarea; if (ta) ta.focus();
  var dt = new DataTransfer();
  var ev = new ClipboardEvent('copy', {bubbles:true, cancelable:true, clipboardData:dt});
  document.getElementById('termEl').dispatchEvent(ev);
  return {prevented: ev.defaultPrevented, data: dt.getData('text/plain')};
})()"""


INSTRUMENT = r"""(function(){
  window.__r = {copies: [], execPayloads: [], errs: []};
  var oc = termCopySelection;
  termCopySelection = function(){
    window.__r.copies.push((term && term.getSelection()) || '');
    return oc.apply(this, arguments);
  };
  var oe = document.execCommand;
  document.execCommand = function(c){
    if (c === 'copy') {
      var a = document.activeElement;
      window.__r.execPayloads.push(a && a.value ? String(a.value) : '');
    }
    return oe.apply(this, arguments);
  };
  window.addEventListener('error', function(e){ window.__r.errs.push(String(e.message)); });
  return true;
})()"""

def open_term(cdp):
    cdp.eval("go('chat')"); time.sleep(0.4)
    cdp.eval("pickChatEntity('claude')"); time.sleep(1.0)
    cdp.eval("switchMode('term')"); time.sleep(3.0)
    if not cdp.eval("typeof termWs!=='undefined'&&termWs&&termWs.readyState===1"):
        cdp.eval("termNew()"); time.sleep(6.0)


def wait_mouse_tracking(cdp, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cdp.eval("(typeof termMouseWant!=='undefined'&&termMouseWant.size>0)&&"
                    "term.modes&&term.modes.mouseTrackingMode!=='none'"):
            return True
        time.sleep(0.5)
    return False


def wait_idle(cdp, stable_need=3, timeout=45):
    """等 TUI 停止流式输出。

    为什么必须有这一步：claude 回答时会不断重画，**缓冲区内容在动** —— 先前选中的
    绝对行索引会随重绘移位/变空，`term.getSelection()` 于是自己变成空串（实测：清了
    却**没走** SelectionService.clearSelection，calls=[]）。那是探针的时序问题，不是产品
    缺陷。等屏面连续 1.5s 不变再测，判据才可复现。"""
    last, stable, t0 = None, 0, time.time()
    while time.time() - t0 < timeout:
        t = cdp.eval("(function(){var b=term.buffer.active;var s='';for(var i=0;i<b.length;i++){"
                     "var l=b.getLine(i);if(l)s+=l.translateToString(true)+'\\n';}return s.length+'|'+s.slice(-120);})()")
        if t == last:
            stable += 1
            if stable >= stable_need:
                return True
        else:
            stable, last = 0, t
        time.sleep(0.5)
    return False


def main():
    prof = tempfile.mkdtemp(prefix="verifysel")
    proc = launch_chrome(BASE + "/", PORT, prof, 1280, 900)
    cdp = CDP(page_target(PORT), on_event=lambda m, p: None)
    cdp.send("Page.enable")
    time.sleep(2.0)
    if TOK:
        cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TOK))
    cdp.send("Page.reload", ignoreCache=True)
    time.sleep(3.5)
    open_term(cdp)

    ok = cdp.eval("typeof term!=='undefined'&&!!term")
    chk("V0 xterm 实例已就绪", bool(ok))
    if not ok:
        print("  终端没起来，后面无从谈起"); cdp.close(); proc.terminate(); return 1

    tracking = wait_mouse_tracking(cdp)
    chk("A0 会话声明了鼠标跟踪（否则测不到本缺陷）", tracking,
        "want=%s mode=%s" % (cdp.eval("Array.from(termMouseWant||[])"),
                             cdp.eval("term.modes&&term.modes.mouseTrackingMode")))
    if not tracking:
        print("  ~~ 无跟踪态：本平台 claude 可能未起或变了行为；A1 只能作参考")
        # 仍继续，但 A1 用非跟踪会话也可能绿（termMouseArm 早退），如实标注

    # 造一行内容，并等 TUI 停止流式输出（否则缓冲区在动，选区行索引会自己失效）
    cdp.eval("term.input('echo SELPROBE\\r')")
    time.sleep(2.0)
    idle = wait_idle(cdp)
    print("  ~~ 屏面已静止（wait_idle=%s）" % idle)
    ln = cdp.eval("(function(){var b=term.buffer.active;for(var i=0;i<b.length;i++){var l=b.getLine(i);"
                  "if(l&&l.translateToString(true).indexOf('SELPROBE')>=0)return i;}"
                  "return Math.max(0,b.length-1);})()")

    cdp.eval(INSTRUMENT)

    # ── A1：程序化选区抗回装 ──
    cdp.eval("term.clearSelection(); term.select(0, %d, 30)" % ln); time.sleep(0.2)
    before = cdp.eval("term.getSelection()") or ""
    cdp.eval("termMouseResetNow()"); time.sleep(0.2)
    cdp.eval("termMouseArm()"); time.sleep(0.5)
    after = cdp.eval("term.getSelection()") or ""
    chk("A1 建选区 → 回装跟踪态后选区仍在", bool(before.strip()) and before == after,
        "before=%r after=%r" % (before[:20], after[:20]))

    # ── A2：app 重断言鼠标模式（流式输出里每帧都发）时选区不被清 ──
    cdp.eval("term.select(0, %d, 30)" % ln); time.sleep(0.1)
    a2b = cdp.eval("term.getSelection()") or ""
    cdp.eval("term.write('\\x1b[?1000;1002;1003;1006h')"); time.sleep(0.4)
    a2a = cdp.eval("term.getSelection()") or ""
    chk("A2 有选区时 app 重断言 ?1003h，选区仍在", bool(a2b.strip()) and a2b == a2a,
        "before=%r after=%r mode=%s" % (a2b[:20], a2a[:20],
                                        cdp.eval("term.modes.mouseTrackingMode")))

    # ── B1：真鼠标拖选 + 松开 ──
    cdp.eval("term.clearSelection()")
    geo = cdp.eval("""(function(){
      var scr=document.querySelector('.xterm-screen').getBoundingClientRect();
      var rowH=scr.height/term.rows;
      var y=Math.round(scr.top+(%d-term.buffer.active.viewportY+0.5)*rowH);
      return {left:Math.round(scr.left), y:y};
    })()""" % ln)
    x0, y0 = geo["left"] + 5, geo["y"]
    cdp.send("Input.dispatchMouseEvent", type="mousePressed", x=x0, y=y0, button="left", buttons=1, clickCount=1)
    for i in range(1, 11):
        cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=x0 + i * 30, y=y0, button="left", buttons=1)
        time.sleep(0.02)
    mid = cdp.eval("term.hasSelection()")
    cdp.send("Input.dispatchMouseEvent", type="mouseReleased", x=x0 + 300, y=y0, button="left", buttons=0, clickCount=1)
    time.sleep(0.6)   # 给 mouseup→termMouseArm（若有）留时间
    sel = cdp.eval("term.getSelection()") or ""
    chk("B1 拖选松开后选区仍在（用户动作端到端）", bool(mid) and bool(sel.strip()),
        "拖动中=%s 松开后=%r" % (mid, sel[:24]))

    # ── C1/C2：右键菜单 ──
    # D1 之前把选区**确定性**重建：B1 之后 TUI 可能继续输出并重断言鼠标模式，
    # 经 SelectionService.disable() 把选区清掉（这本身不是缺陷，是「新输出到了」）。
    # 菜单/复制这几条要测的是「有选区时菜单与复制通道对不对」，不该被这层时序影响。
    cdp.eval("term.select(0, %d, 30)" % ln); time.sleep(0.2)
    r = cdp.eval("(function(){var s=document.querySelector('.xterm-screen').getBoundingClientRect();"
                 "return {x:Math.round(s.left+s.width/2),y:Math.round(s.top+s.height/2)};})()")
    menu = cdp.eval("""(function(){
      var el=document.getElementById('termEl');
      el.dispatchEvent(new MouseEvent('contextmenu', {bubbles:true, cancelable:true,
        clientX:%d, clientY:%d}));
      var m=document.getElementById('termCtx');
      return {on:m.classList.contains('on'), n:m.querySelectorAll('button').length};
    })()""" % (r["x"], r["y"]))
    chk("C1 终端右键 ⇒ 菜单出现且三按钮", bool(menu) and menu["on"] and menu["n"] == 3, str(menu))
    chk("C2 有选区时「复制」可点", cdp.eval("!document.getElementById('termCtxCopy').disabled"))

    # ── D1：点复制 ──
    cdp.eval("document.getElementById('termCtxCopy').click()"); time.sleep(0.8)
    copies = cdp.eval("window.__r.copies")
    chk("D1 点「复制」⇒ 复制通道承载选区文本",
        bool(copies) and "SELPROBE" in (copies[-1] or ""),
        "copies=%s" % [c[:20] for c in (copies or [])][-2:])
    chk("D1b 点后菜单关闭", cdp.eval("!document.getElementById('termCtx').classList.contains('on')"))

    # ── D2：Ctrl+C 路（真实键事件；与 attachCustomKeyEventHandler 同一条）──
    # ⚠️ 不要用鼠标点击来抢焦点：无选区时 xterm 处于跟踪态，点击会被上报给 claude
    # ⇒ 触发重画、缓冲区一动刚才选中的行索引就失效（实测 copies[-1]='' / D3 全空）。
    # 用 term.focus() 直接把焦点交给 helper textarea，零鼠标上报。
    wait_idle(cdp)
    ln = cdp.eval("(function(){var b=term.buffer.active;for(var i=0;i<b.length;i++){var l=b.getLine(i);"
                  "if(l&&l.translateToString(true).indexOf('SELPROBE')>=0)return i;}"
                  "return Math.max(0,b.length-1);})()")
    cdp.eval("term.focus()"); time.sleep(0.2)
    cdp.eval("term.select(0, %d, 30)" % ln); time.sleep(0.2)
    chk("D2 前置：焦点在终端且选区非空",
        cdp.eval("document.activeElement===term.textarea") and
        "SELPROBE" in (cdp.eval("term.getSelection()") or ""),
        "focus=%s sel=%r" % (cdp.eval("document.activeElement===term.textarea"),
                             (cdp.eval("term.getSelection()") or "")[:20]))
    n0 = cdp.eval("window.__r.copies.length")
    key(cdp, "c", "KeyC", 67, 2)          # modifiers=2 ⇒ Ctrl
    time.sleep(0.6)
    copies2 = cdp.eval("window.__r.copies")
    chk("D2 真 Ctrl+C ⇒ 复制通道承载选区文本",
        len(copies2) > n0 and "SELPROBE" in (copies2[-1] or ""),
        "copies=%s" % [c[:20] for c in (copies2 or [])][-2:])

    # ── D3：容器 copy 兜底路（浏览器原生 copy 事件）──
    got = cdp.eval(COPY_EVENT_JS)
    chk("D3 容器 copy 兜底写入选区文本",
        bool(got) and got["prevented"] and "SELPROBE" in (got["data"] or ""),
        "prevented=%s data=%r" % (got and got["prevented"], (got and got["data"] or "")[:24]))

    # ── D4：负控 —— 无选区时容器 copy **不得**被接管（防 D3 因「恒 preventDefault」假绿）──
    cdp.eval("term.clearSelection()"); time.sleep(0.2)
    neg = cdp.eval(COPY_EVENT_JS)
    chk("D4 无选区时容器 copy 不接管（prevented=False 且无数据）",
        bool(neg) and (not neg["prevented"]) and not (neg["data"] or ""),
        "prevented=%s data=%r" % (neg and neg["prevented"], (neg and neg["data"] or "")[:16]))

    # ── C2b：无选区时置灰 ──
    cdp.eval("term.clearSelection()")
    cdp.eval("termCtxOpen(200, 120)")
    chk("C2b 无选区时「复制」置灰", cdp.eval("document.getElementById('termCtxCopy').disabled"))

    # ── E1：点空白关 ──
    cdp.eval("document.body.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}))")
    chk("E1 菜单外 pointerdown ⇒ 关闭", cdp.eval("!document.getElementById('termCtx').classList.contains('on')"))

    # ── E2：Escape 关 ──
    cdp.eval("termCtxOpen(200, 120)")
    cdp.eval("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
    chk("E2 Escape ⇒ 关闭", cdp.eval("!document.getElementById('termCtx').classList.contains('on')"))

    errs = cdp.eval("window.__r.errs")
    chk("F 全程零 JS 异常", not errs, "errs=%s" % (errs or [])[:2])

    cdp.close(); proc.terminate()
    print("\n[修复态] %s（%d 项失败）" % ("全绿" if not FAILED else "有 FAIL：" + ", ".join(FAILED), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())