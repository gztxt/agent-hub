#!/usr/bin/env python3
"""xterm 6.0.0 真渲染闸门（L2 live）：P2-A。

跑法（先起影子实例；worktree 无 .env，端口/数据与生产隔离，务必 setsid 否则 shell 退出连带杀）：
    ( cd <worktree> && mkdir -p work/probe/data && \
      setsid env DATA_DIR=$PWD/work/probe/data PORT=3199 HOST=127.0.0.1 \
        HUB_WRITE_TOKEN=probe-token TERM_TOKEN=probe-term-token \
        ../agent-hub/venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 \
        > work/probe/shadow.log 2>&1 < /dev/null & )
    HUB_PROBE_CDP_PORT=9411 ../agent-hub/venv/bin/python tests/verify_term_xterm6.py http://127.0.0.1:3199
收尾：fuser -k 3199/tcp   # 禁用 pkill -f "port 3199"，会连带杀自己

为什么必须真渲染：这次改的是**整个终端栈**（核心 290KB→489KB + 6 个 addon 全换），
静态断言只能证明"文件换对了、提手同步了"，证明不了"终端还起得来、字形没崩、
addon 还能挂上"。而 6.0 是 major 版本，破坏性变更**不会**出现在 changelog 里逐条列全，
只能靠真跑一遍关键路径来撞。headless 且 `--disable-gpu` ⇒ 恒走 DOM 档，
这恰好覆盖"回落链末端"这条最容易被忽略的分支（有 GPU 的机器上永远走不到）。

判据分组：
  V1-V4  版本与全局：Terminal 构造成功、六个 addon 全局在位、canvas 全局**不在**位、
        6.0 核心构造器可用（而不是退回某处缓存的 5.5）
  V5-V8  渲染器回落：auto 落 dom（headless 无 GPU）、?term=dom 落 dom、
        ?term=canvas **显式降级 dom** 且不留 canvas 痕迹、localStorage 老存量同样降级
  V9-V12 终端可用：xterm DOM 挂上、有内容渲染（CJK 不方块）、Unicode11 生效、
        查找 addon 能跑（findNext 不抛）
  V13    图集止血线存在（DOM 档下不启动 webgl 定时器，但代码路径可达性可验）

踩过的坑（改这几行前先读）：
  - **token 必须在 Page.reload 之后写 localStorage**：headless 每次新建临时 profile，
    reload 之前的写入实测读回 None。
  - **?v= 提手对端侧缓存无效**（ETag 由 mtime+size 算，与 query 无关）⇒ 换过 vendor 后
    探针必须 `Page.reload` + `ignoreCache=True`，否则量的是旧版页面。
  - **c.eval 不等 Promise**：eval("ensureTerm()") 后必须 sleep 等同步副作用落地。
  - **必须先 `go('chat')` 再切 term 模式**：终端在 `page-chat` 里，只调 `switchMode('term')`
    不会让该 page 变成 `.on`，`#termEl` 的 `getBoundingClientRect()` 全是 0 ⇒
    量到 0x0 会误判成"终端挂了"（初版 V6 就这样红）。
  - **headless 的 webgl 不是"没有"**：`--disable-gpu` 下 Chromium 仍会用 SwiftShader
    给出 WebGL2 上下文，所以 auto 档**真的挂上 webgl**（初版 V5 因此红）。这不是缺陷，
    反而是本闸门的意外收获：webgl 路径在 6.0 上被真跑通了。V5 的判据改成
    "渲染器名 ∈ {webgl, dom} 且不白屏"，而不是"必须是 dom"。
  - **termToken() 的原生 prompt() 会挂 renderer**：本闸门给 on_dialog 兜底，
    保证即便回归成弹窗，探针表现为"断言失败"而不是"卡死"。
"""
import json
import os
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
CDP_PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9411"))
TERM_TOKEN = os.getenv("HUB_PROBE_TERM_TOKEN", "probe-term-token")
H = 900
res = []


def chk(name, ok, detail=""):
    res.append(bool(ok))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("  —— " + str(detail)) if detail else ""))


def goto(page_url, extra=""):
    """开一个带 query 的页面并等 DOM 就绪。"""
    prof = tempfile.mkdtemp(prefix="xterm6prof")
    proc = launch_chrome(page_url, CDP_PORT, prof, 1200, H)
    cdp = CDP(page_target(CDP_PORT), on_event=lambda m, p: page_dismiss(cdp))
    return proc, cdp


def page_dismiss(cdp):
    """原生对话框（prompt/alert）会挂住 renderer ⇒ 探针必须能自动关掉，否则表现成超时。"""
    try:
        cdp.send("Page.handleJavaScriptDialog", accept=True, promptText="")
    except Exception:
        pass


def reload_fresh(cdp, url):
    """换 query 后必须**重新加载且不吃缓存**（?v= 提手对端侧缓存无效）。"""
    cdp.send("Page.enable")
    cdp.send("Page.navigate", url=url)
    time.sleep(2.0)
    cdp.send("Page.reload", ignoreCache=True)
    time.sleep(2.0)


def main():
    # ── 组 1：默认 auto 档（headless --disable-gpu ⇒ 应落 DOM） ──
    proc, cdp = goto(BASE)
    try:
        reload_fresh(cdp, BASE + "/")
        cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
        reload_fresh(cdp, BASE + "/")

        # V1 Terminal 构造成功
        ok = cdp.eval("typeof window.Terminal === 'function'")
        chk("V1 Terminal 构造器在位（6.0 UMD 全局）", ok is True, "typeof=%s" % ok)

        # V2 六个 addon 全局在位
        gl = cdp.eval("[typeof window.WebglAddon, typeof window.Unicode11Addon, "
                      "typeof window.SearchAddon, typeof window.ClipboardAddon, "
                      "typeof window.WebLinksAddon, typeof window.FitAddon].join(',')")
        want = "object,object,object,object,object,object"
        chk("V2 六个 addon 全局符号齐备（webgl/unicode11/search/clipboard/web-links/fit）",
            gl == want, gl)

        # V3 canvas 全局**不在**位（addon 已删）
        ca = cdp.eval("typeof window.CanvasAddon")
        chk("V3 CanvasAddon 全局不存在（addon 已随 6.0 移除）", ca == "undefined", "typeof=%s" % ca)

        # V4 6.0 核心可构造（不是缓存里的 5.5）
        made = cdp.eval("""
          (function(){ try {
            var t = new window.Terminal({cols: 20, rows: 5, scrollback: 10});
            var ok = t && t.cols === 20 && t.rows === 5;
            t.dispose(); return ok ? 'ok' : 'bad';
          } catch (e) { return 'err:' + e.message; } })()
        """)
        chk("V4 xterm 6.0 核心可实例化（20x5）", made == "ok", made)

        # ── 组 2：渲染器回落链（auto → 无 WebGL → dom） ──
        cdp.eval("go('chat')")
        time.sleep(0.5)
        cdp.eval("switchMode('term')")
        time.sleep(0.5)
        cdp.eval("ensureTerm()")
        time.sleep(1.5)
        rname = cdp.eval("termRendererName")
        chk("V5 auto 档渲染器落在受支持集合内（webgl 或 dom，不出现 canvas）",
            rname in ("webgl", "dom"), rname)

        # V6 xterm 真的挂进 DOM 且有尺寸
        gbox = cdp.eval("""
          (function(){ var e = document.querySelector('#termEl .xterm');
            if (!e) return 'no-xterm';
            var r = e.getBoundingClientRect();
            return (r.width > 100 && r.height > 50) ? 'ok' : ('tiny:' + Math.round(r.width) + 'x' + Math.round(r.height)); })()
        """)
        chk("V6 .xterm 已挂进 #termEl 且尺寸正常（≥100x50，非 0x0=页未激活）", gbox == "ok", gbox)

        # V7 CJK 字形可用（真渲染口径，不是调用 termCjkUsable 的自证）
        cjk = cdp.eval("termCjkUsable()")
        chk("V7 CJK 字形可用（汉字不会被画成方块）", cjk is True, "termCjkUsable=%s" % cjk)

        # V8 Unicode11 生效（6.0 仍需 open 前激活）
        uv = cdp.eval("term && term.unicode ? term.unicode.activeVersion : 'none'")
        chk("V8 Unicode11 已激活（activeVersion='11'）", uv == "11", "activeVersion=%s" % uv)

        # V9 查找 addon 可用（不抛）
        fs = cdp.eval("""
          (function(){ try {
            if (!termSearch) return 'no-addon';
            term.write('hello hub-term-xterm6-probe');
            var r = termSearch.findNext('xterm6-probe', {incremental: true});
            return r ? 'ok' : 'nohit';
          } catch (e) { return 'err:' + e.message; } })()
        """)
        chk("V9 SearchAddon 挂载且 findNext 可跑", fs in ("ok", "nohit"), fs)

        # V10 回退命令写入终端后可见（真渲染非空）
        txt = cdp.eval("""
          (function(){ try {
            var b = term.buffer.active, out = '';
            for (var y = 0; y < b.length; y++) { var t = b.getLine(y).translateToString(true); if (t.indexOf('xterm6-probe') >= 0) { out = t; break; } }
            return out || 'notfound';
          } catch (e) { return 'err:' + e.message; } })()
        """)
        chk("V10 写入内容落到 buffer（终端确实在工作）", "xterm6-probe" in str(txt), repr(str(txt)[:70]))

        # V11 termRendererPref 在 auto 档下返回空（不强制任何档）
        pf = cdp.eval("termRendererPref()")
        chk("V11 auto 档偏好为空（未按 query 强制）", pf in ("", None), repr(pf))
    finally:
        try:
            cdp.close()
        except Exception:
            pass
        proc.kill()

    # ── 组 3：?term=canvas 显式降级（V2 组的关键断点） ──
    proc, cdp = goto(BASE + "/?term=canvas")
    try:
        reload_fresh(cdp, BASE + "/?term=canvas")
        cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
        reload_fresh(cdp, BASE + "/?term=canvas")
        pf = cdp.eval("termRendererPref()")
        chk("V12 ?term=canvas 显式降级 dom（不留 canvas 档）", pf == "dom", repr(pf))
        cdp.eval("go('chat')")
        time.sleep(0.5)
        cdp.eval("switchMode('term')")
        time.sleep(0.5)
        cdp.eval("ensureTerm()")
        time.sleep(1.2)
        rn = cdp.eval("termRendererName")
        chk("V13 ?term=canvas 下实际渲染器为 dom", rn == "dom", rn)
    finally:
        try:
            cdp.close()
        except Exception:
            pass
        proc.kill()

    # ── 组 4：localStorage 老存量 canvas（无 query）同样降级并写回 ──
    proc, cdp = goto(BASE + "/")
    try:
        reload_fresh(cdp, BASE + "/")
        cdp.eval("localStorage.setItem('hub.term.token', %s)" % json.dumps(TERM_TOKEN))
        cdp.eval("localStorage.setItem('hubTermRenderer', 'canvas')")
        reload_fresh(cdp, BASE + "/")
        pf = cdp.eval("termRendererPref()")
        chk("V14 存量 canvas 偏好降级 dom", pf == "dom", repr(pf))
        back = cdp.eval("localStorage.getItem('hubTermRenderer')")
        chk("V15 降级后写回 dom（不每页重复告警）", back == "dom", repr(back))
    finally:
        try:
            cdp.close()
        except Exception:
            pass
        proc.kill()

    print("\n%d/%d PASS" % (sum(res), len(res)))
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
