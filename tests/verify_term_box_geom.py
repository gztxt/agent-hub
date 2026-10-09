#!/usr/bin/env python3
"""终端框几何不变量（L2 live，v0.13.97 新增；纯 DOM，不依赖 xterm 运行时）。

【为什么不用真xterm 量】前面 `probe-term-fit-rows.py` 试了4 版都在量xterm 时
`Runtime.evaluate` 无应答——`termLoadRenderer()` 走 WebGL addon，在本机headless
chromium（--disable-gpu）下会把 renderer 拖住。按军规「同一判据 2 次无结论就停手」，
不再重试那条路，改量**不依赖 xterm 的那份几何**。

【本闸门量什么（可断言的量，不以截图交差）】
  A #termEl 内容盒 vs 黑底区（.term-body）内容盒：必须**完全重合**。
    历史上踩过的坑就是这两层不一致——父层带 padding 时 .xterm 高 468而 #termEl
    内容盒仅 459，底部溢出 9~10px（templates/index.html 的 FitAddon 铁律注释）。
    「黑底比容器小」= 底部留出一条不属于终端的白边 ⇒ FitAddon 口径已被破坏。
  B 终端框到 main 内容盒四缘的净留白，必须**等于** main padding 的计算值。
    收窄后仍应精确等于 calc(var(--main-pad-*) * .4)，不多不少。
  C改前/改后留白比值必须 ≈0.4（这就是「缩小 60%」本身的判据）。

【它不覆盖什么（诚实登记）】
  · rows×行高是否放得下——那必须真xterm 实例，本机 headless 量不到（如上）。
  · 真实 PTY 会话的挂载与回放——与本次纯 CSS 改动无关。
"""
import json
import sys
import time

sys.path.insert(0, "/fs/1000/ftp/技术文档/agenthub/tests")
from _cdp_min import CDP, launch_chrome, page_target      # noqa: E402

BASE = "http://127.0.0.1:3102"
CDP_PORT = 9393
WIDTHS = [390, 768, 1280, 1440, 1920]

# 只切section.page 的 .on + 点亮 termPane，**绝不调ensureTerm()**：
# 后者会 loadAddon(WebGL) 挂住 headless renderer（见本文件头）。
SETUP = r"""
(() => {
  document.querySelectorAll('section.page').forEach(s => s.classList.remove('on'));
  const pg = document.getElementById('page-chat'); if (pg) pg.classList.add('on');
  ['embedPane','chatPane'].forEach(id => { const e = document.getElementById(id);
    if (e) e.classList.remove('on'); });
  const p = document.getElementById('termPane'); if (p) p.classList.add('on');
  //给 #termEl 一个确定高度，好让「黑底 vs 容器」这条可比
  const te = document.getElementById('termEl');
  if (te) { te.style.background = '#000'; te.textContent = 'probe'; }
  return 'ok';
})()
"""

MEAS = r"""
JSON.stringify((() => {
  const px = v => Math.round(parseFloat(v) * 100) / 100;
  const g = id => document.getElementById(id);
  const te = g('termEl'), tb = document.querySelector('.term-body');
  const cm = document.querySelector('.chat-main');
  const mw = g('mainWrap'), pane = g('termPane');
  if (!te || !tb || !cm || !mw || !pane) return JSON.stringify({err: 'missing-node'});
  const cs = getComputedStyle(mw);
  const pr = pane.getBoundingClientRect(), mr = mw.getBoundingClientRect();
  const ter = te.getBoundingClientRect(), tbr = tb.getBoundingClientRect();
  const cmr = cm.getBoundingClientRect();
  return JSON.stringify({
    vw: innerWidth,
    padMain: {t: px(cs.paddingTop), r: px(cs.paddingRight),
              b: px(cs.paddingBottom), l: px(cs.paddingLeft)},
    gap: {t: Math.round(pr.top - mr.top), r: Math.round(mr.right - pr.right),
          b: Math.round(mr.bottom - pr.bottom), l: Math.round(pr.left - mr.left)},
    // 黑底（.term-body）内容盒 vs #termEl 内容盒：必须重合
    bodyVsEl: {dw: Math.round(tbr.width - ter.width),
               dh: Math.round(tbr.height - ter.height)},
    chatMainBorder: getComputedStyle(cm).borderTopWidth,
    paneW: Math.round(pr.width), paneH: Math.round(pr.height),
    elClient: {w: te.clientWidth, h: te.clientHeight}
  });
})())
"""

BASE_PADDING = {"t": 12.0, "r": 16.0, "b": 12.0, "l": 16.0}   # 宽屏基准
NARROW_BASE = 8.0                                            # 窄屏基准
RATIO = 0.4


def run(w, port):
    proc = launch_chrome("about:blank", port, "/vol1/tmp/hub_geom_%d" % port, 1280, 900)
    cdp = CDP(page_target(port))
    try:
        cdp.send("Page.enable"); cdp.send("Runtime.enable")
        cdp.send("Emulation.setDeviceMetricsOverride", width=w, height=900,
                 deviceScaleFactor=1, mobile=(w < 768))
        cdp.send("Page.navigate", url=BASE + "/")
        for _ in range(200):
            if cdp.eval("document.readyState") == "complete":
                break
            time.sleep(0.05)
        time.sleep(2.5)
        cdp.eval(SETUP)
        time.sleep(0.8)
        # MEAS 里的 JSON.stringify 会被 CDP 的 returnByValue **再序列化一层** ⇒
        # 拿到的是 '"{\"k\":1}"' 这种双层串。json.loads 一次得到 str、两次才到 dict；
        # 只 loads 一次会拿到 str，下游 .get() 报 'str' object has no attribute 'get'。
        return json.loads(json.loads(cdp.eval(MEAS) or '"{}"'))
    finally:
        cdp.close()
        proc.terminate()


def main():
    fails, port = [], CDP_PORT
    print("终端框几何不变量 · base=%s" % BASE)
    print("%-6s %-9s %-22s %-14s %-12s %s"
          % ("vw", "档", "main padding", "净留白 T/R/B/L", "黑底−容器", "判定"))
    for w in WIDTHS:
        port += 1
        d = run(w, port)
        if d.get("err"):
            fails.append("vw=%d %s" % (w, d["err"]))
            continue
        narrow = w < 768
        base = NARROW_BASE if narrow else None
        if base:
            want = {"t": base * RATIO, "r": base * RATIO,
                    "b": base * RATIO, "l": base * RATIO}
        else:
            want = {k: v * RATIO for k, v in BASE_PADDING.items()}
        pad, gap = d["padMain"], d["gap"]
        got = {"t": gap["t"], "r": gap["r"], "b": gap["b"], "l": gap["l"]}
        bad = []
        for k in ("t", "r", "b", "l"):
            # 净留白 = main padding + .chat-main 边框（左右各 1、上 1）
            exp = want[k] + (1.0 if k in ("t", "r", "b", "l") else 0)
            if abs(got[k] - exp) > 1.01:
                bad.append("%s侧留白 %s≠ 期望 %.1f" % (k, got[k], exp))
        dv = d["bodyVsEl"]
        if abs(dv["dh"]) > 1 or abs(dv["dw"]) > 1:
            bad.append("黑底与容器不等（Δ%d×%d）⇒ FitAddon 口径已破"
                       % (dv["dw"], dv["dh"]))
        fails.extend("vw=%d %s" % (w, b) for b in bad)
        print("%-6d %-9s %-22s %-14s %-12s %s"
              % (w, "窄屏" if narrow else "宽屏",
                 "%.1f/%.1f/%.1f/%.1f" % (pad["t"], pad["r"], pad["b"], pad["l"]),
                 "%d/%d/%d/%d" % (gap["t"], gap["r"], gap["b"], gap["l"]),
                 "%d×%d" % (dv["dw"], dv["dh"]),
                 "FAIL " + "; ".join(bad) if bad else "PASS"))
    print("\n%s" % ("FAIL: " + "; ".join(fails) if fails
                    else "PASS 四向留白 = 基准×0.4（缩小 60%），黑底与容器完全重合"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())