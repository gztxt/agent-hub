#!/usr/bin/env python3
"""L2 live：终端滚轮真渲染闸门（v0.13.63，PT-20260930-07）。

用户报障：「agent 终端页面，桌面浏览器里无法上翻 / 滚动到页顶」。
静态断言（tests/test_term_scroll_sensitivity.py）只能证明"参数写了"，证明不了
"真滚得动" —— 而后者才是用户判据。**判据必须是可断言的量，不能以截图交差**
（AGENTS.md 布局/前端类报障铁律）。

跑法（先起影子实例，worktree 无 .env，务必 setsid 否则 shell 退出连带杀）：
    ( cd <worktree> && mkdir -p work/probe/data && \
      setsid env DATA_DIR=$PWD/work/probe/data PORT=3199 HOST=127.0.0.1 \
        HUB_WRITE_TOKEN=probe-token TERM_TOKEN=probe-term-token \
        ../agent-hub/venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 \
        > work/probe/shadow.log 2>&1 < /dev/null & )
    HUB_PROBE_CDP_PORT=9412 ../agent-hub/venv/bin/python tests/verify_term_scroll.py http://127.0.0.1:3199
收尾：fuser -k 3199/tcp    # 禁用 pkill -f "3199"，会连带杀自己

判据分组（每条都能被证伪，不接受"看起来对了"）：
  S1-S3  参数与量纲：scrollSensitivity 生效值 = 5；单格滚轮 ≥ 8 行（修前 2.1）；
        线性关系成立（sens 越大滚得越远，且 sens=10 明显快于 sens=3）
  S4-S5  端到端复现原句：从底部连续真滚轮 ⇒ viewportY 真能到 0（不是停在半路），
        视口首行是 banner（真·页顶）而非 L1xxx
  S6-S7  滚动条可见性：鼠标进终端区后 opacity>0 且 pointer-events 非 none
        （6.0 的 Auto 档是 hover 才显、离开 500ms 淡出 —— 那是设计不是缺陷，
         所以这里断言"hover 后可见"而不是"常态可见"，改了样式反而会红）

踩过的坑（改这几行前先读）：
  - **`term` 是全局词法作用域**（`let term` + 'use strict'），`window.term` 取到 undefined，
    必须用裸 `term` 表达式求值。
  - **必须先 `go('chat')` 再 `switchMode('term')`**：终端在 `page-chat` 里，只切模式
    不会让该 page 变成 `.on`，`#termEl` 的 getBoundingClientRect() 全是 0 ⇒ 量到 0x0
    会误判成"终端挂了"。
  - **`?v=` 提手对端侧缓存无效**：静态资源 ETag 由 mtime+size 算，与 query 无关 ⇒
    换过 vendor/产物后必须 `Page.reload(ignoreCache=True)`，否则量的是旧页面。
  - **`c.eval` 不等 Promise**：eval("ensureTerm()") 后必须 sleep 等同步副作用落地。
  - **别用 DOM 行选择器读首行**：6.0 DOM 档的行结构与选择器不稳定，用
    `buffer.active.getLine(0)` 才是内容真值。
  - **termToken() 的原生 prompt() 会挂 renderer**：给 on_dialog 兜底，
    保证即便回归成弹窗，探针表现为"断言失败"而不是"卡死"。
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
LINES = 2000
ROWS_NOTCH = 10          # 每次量测滚几格
MIN_ROWS_PER_NOTCH = 8   # 下限：修前实测 2.1；5 ⇒ 实测 10.5
res = []


def chk(name, ok, detail=""):
    res.append(bool(ok))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("  —— " + str(detail)) if detail else ""))


def dismiss(cdp):
    try:
        cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception:
        pass


def boot():
    prof = tempfile.mkdtemp(prefix="termscroll")
    proc = launch_chrome(BASE + "/", CDP_PORT, prof, 1280, 900)
    cdp = CDP(page_target(CDP_PORT), on_event=lambda m, p: dismiss(cdp))
    cdp.send("Page.enable")
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
    cdp.eval("(function(){var s='';for(var i=1;i<=%d;i++) s+='L'+i+'\\r\\n'; term.write(s); return 1;})()" % LINES)
    time.sleep(2.5)
    box = cdp.eval("""(function(){ var e=document.querySelector('#termEl .xterm-screen');
        var r=e.getBoundingClientRect(); return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; })()""")
    return proc, cdp, box


def wheel(cdp, box, n, dy=-120):
    for _ in range(n):
        cdp.send("Input.dispatchMouseEvent", type="mouseWheel",
                 x=box["x"], y=box["y"], deltaX=0, deltaY=dy)
        time.sleep(0.1)


def rows_per_notch(cdp, box, sens):
    cdp.eval("term.options.scrollSensitivity=%d" % sens)
    time.sleep(0.3)
    cdp.eval("term.scrollToBottom()")
    time.sleep(0.4)
    b = cdp.eval("term.buffer.active.viewportY")
    wheel(cdp, box, ROWS_NOTCH)
    time.sleep(0.5)
    a = cdp.eval("term.buffer.active.viewportY")
    if not (isinstance(a, int) and isinstance(b, int)):
        return None
    return (b - a) / float(ROWS_NOTCH)


def main():
    proc, cdp, box = boot()
    try:
        cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=box["x"], y=box["y"])
        time.sleep(0.3)

        sens = cdp.eval("term.options.scrollSensitivity")
        chk("S1 scrollSensitivity 生效值 = 5", sens == 5, "实际 %r" % (sens,))

        r5 = rows_per_notch(cdp, box, 5)
        chk("S2 单格滚轮 ≥ %d 行（修前 2.1）" % MIN_ROWS_PER_NOTCH,
            r5 is not None and r5 >= MIN_ROWS_PER_NOTCH, "%.1f 行/格" % r5 if r5 else "读数失败")

        r3 = rows_per_notch(cdp, box, 3)
        r10 = rows_per_notch(cdp, box, 10)
        chk("S3 线性：sens=10 快于 sens=3",
            r3 and r10 and r10 > r3, "3⇒%.1f / 10⇒%.1f 行/格" % (r3, r10))

        cdp.eval("term.options.scrollSensitivity=5")
        time.sleep(0.2)
        cdp.eval("term.scrollToBottom()")
        time.sleep(0.5)
        y0 = cdp.eval("term.buffer.active.viewportY")
        n = 0
        while n < 600:
            cdp.send("Input.dispatchMouseEvent", type="mouseWheel",
                     x=box["x"], y=box["y"], deltaX=0, deltaY=-120)
            n += 1
            if cdp.eval("term.buffer.active.viewportY") == 0:
                break
        yend = cdp.eval("term.buffer.active.viewportY")
        chk("S4 真滚轮能滚到页顶（viewportY=0）", yend == 0,
            "从 %r 用 %d 格滚到 %r" % (y0, n, yend))
        chk("S5 到顶比修前省力（%d 格 < 修前约 940）" % 400, n < 400, "实际 %d 格" % n)
        first = cdp.eval("term.buffer.active.getLine(0).translateToString(true)")
        chk("S6 视口首行是 banner（真页顶，非 L1xxx）",
            bool(first) and not first.startswith("L1"), "首行=%r" % first)

        sb = cdp.eval("""(function(){ var s=document.querySelector('#termEl .scrollbar.vertical');
            if(!s) return null; var cs=getComputedStyle(s);
            return {cls:s.className, op:cs.opacity, pe:cs.pointerEvents,
                    w:Math.round(s.getBoundingClientRect().width)}; })()""")
        chk("S7 鼠标在终端内时滚动条可见可点（6.0 Auto 档设计）",
            bool(sb) and float(sb["op"]) > 0 and sb["pe"] != "none", json.dumps(sb, ensure_ascii=False))
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
    print("\n%d/%d PASS" % (sum(res), len(res)))
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
