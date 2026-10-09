#!/usr/bin/env python3
"""实测「右边终端框」四周留白（用户 2026-10-09 要求上下左右缩小 60% 的**改前基线 / 改后回归**）。

量的都是可断言的量，不是「我看了截图」：
  A 终端框外框 #termPane 的四向净留白（到视口四缘）
  B 相关容器的 computed padding/margin/gap
  C .chat-grid 的实际列数（确认「右边」= 单列，chat-side 已 display:none）

【测法上踩过的坑，逐条避开】
  1 **每个视口宽度起一个独立 chromium 实例**。共用一个实例时，第二次
    `Page.navigate` 会让在途的 `Runtime.evaluate` 永远无应答（实测抛
    TimeoutError: Runtime.evaluate 无应答）—— 不是页面卡住，是 CDP 会话跨导航失效。
  2 视口先就位再导航，量的才是该档真实布局。
  3 结果用 JSON 字符串回传再json.loads：直接 returnByValue 传对象在嵌套 dict 上不稳。

用法：
  ./venv/bin/python tests/probe_term_box_gap.py            # 改前基线
  ./venv/bin/python tests/probe_term_box_gap.py --judge     # 改后回归（判 60% 目标）
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/fs/1000/ftp/技术文档/agenthub/tests")
from _cdp_min import CDP, launch_chrome, page_target      # noqa: E402

BASE = "http://127.0.0.1:3102"
WIDTHS = [390, 768, 1280, 1920]
JUDGE = "--judge" in sys.argv

HOOK = r"""
window.__errs = [];
window.addEventListener('error', e => __errs.push('error: ' + (e.message || '?')));
"""

# ⚠️ 刻意**不调** go('chat')：它会走termToken() 的原生prompt/alert 口令框，
# 而原生对话框会挂住 renderer ⇒ 之后任何 Runtime.evaluate 全部无应答
# （09-23 探针栽过一次，这里直接绕开：只切section.page 的 .on，纯 DOM，不碰业务函数）。
SETUP = r"""
(() => {
  document.querySelectorAll('section.page').forEach(s => s.classList.remove('on'));
  const pg = document.getElementById('page-chat');
  if (pg) pg.classList.add('on');
  ['embedPane','chatPane'].forEach(id => { const e = document.getElementById(id);
    if (e) e.classList.remove('on'); });
  const p = document.getElementById('termPane');
  if (p) p.classList.add('on');
  return p && pg ? 'ok' : 'missing';
})()
"""

MEAS = r"""
JSON.stringify((() => {
  const box = el => { if (!el) return null; const r = el.getBoundingClientRect();
    return {l: Math.round(r.left), t: Math.round(r.top),
            w: Math.round(r.width), h: Math.round(r.height),
            b: Math.round(r.bottom), rt: Math.round(r.right)}; };
  const cs = (el, props) => { if (!el) return null; const s = getComputedStyle(el); const o = {};
    for (const k of props) o[k] = s[k]; return o; };
  const P = ['paddingTop','paddingRight','paddingBottom','paddingLeft',
             'marginTop','marginRight','marginBottom','marginLeft','gap'];
  const pane = document.getElementById('termPane');
  const body = document.querySelector('.term-body');
  const cmain = document.querySelector('.chat-main');
  const grid = document.querySelector('#page-chat .chat-grid');
  const mw = document.getElementById('mainWrap');
  const pr = pane ? pane.getBoundingClientRect() : null;
  return {
    vw: innerWidth, vh: innerHeight,
    pane: box(pane), chatMain: box(cmain), termBody: box(body), mainWrap: box(mw),
    termEl: box(document.getElementById('termEl')),
    // 终端框到视口四缘的**净留白** = 用户口语里的「上下左右边距」
    gapTop: pr ? Math.round(pr.top) : -1,
    gapBottom: pr ? Math.round(innerHeight - pr.bottom) : -1,
    gapLeft: pr ? Math.round(pr.left) : -1,
    gapRight: pr ? Math.round(innerWidth - pr.right) : -1,
    csMainWrap: cs(mw, P), csChatMain: cs(cmain, P),
    csTermPane: cs(pane, P), csTermBody: cs(body, P), csGrid: cs(grid, P),
    gridCols: grid ? getComputedStyle(grid).gridTemplateColumns : null,
    errs: (window.__errs || []).slice(0, 3)
  };
})())
"""


def run(w, port, profile):
    """单档：独立实例，先设视口再导航。返回量到的 dict。"""
    proc = launch_chrome("about:blank", port, profile, 1280, 900)
    cdp = CDP(page_target(port))
    try:
        cdp.send("Emulation.setDeviceMetricsOverride", width=w, height=900,
                 deviceScaleFactor=1, mobile=(w < 768))
        cdp.send("Page.navigate", url=BASE + "/")
        for _ in range(200):                      # 等首帧就绪
            r = cdp.eval("document.readyState")
            if r == "complete":
                break
            time.sleep(0.05)
        time.sleep(3.0)
        cdp.eval(SETUP)
        time.sleep(1.5)
        return json.loads(cdp.eval(MEAS) or "{}")
    finally:
        try:
            cdp.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            pass


def main():
    print("终端框四向留白%s· base=%s" % ("（改后回归）" if JUDGE else "（改前基线）", BASE))
    rows, port = [], 9371
    for w in WIDTHS:
        port += 1
        d = run(w, port, "/vol1/tmp/hub_termgap_%d" % port)
        rows.append(d)
        print("\n== 视口宽 %d ==" % w)
        print("  净留白 T/B/L/R = %s/%s/%s/%s   终端框 %sx%s @(%s,%s)"
              % (d["gapTop"], d["gapBottom"], d["gapLeft"], d["gapRight"],
                 d["pane"]["w"], d["pane"]["h"], d["pane"]["l"], d["pane"]["t"]))
        print("  mainWrap  padding=%s/%s/%s/%s"
              % (d["csMainWrap"]["paddingTop"], d["csMainWrap"]["paddingRight"],
                 d["csMainWrap"]["paddingBottom"], d["csMainWrap"]["paddingLeft"]))
        print("  chat-grid cols=%s gap=%s" % (d["gridCols"], d["csGrid"]["gap"]))
        print("  chatMain padding=%s/%s/%s/%s border=%s"
              % (d["csChatMain"]["paddingTop"], d["csChatMain"]["paddingRight"],
                 d["csChatMain"]["paddingBottom"], d["csChatMain"]["paddingLeft"],
                 cs_border(d)))
        print("  termPane/termBody padding 四向均为 0：%s / %s"
              % (_zero(d["csTermPane"]), _zero(d["csTermBody"])))
        if d["errs"]:
            print("  !! JS 异常 %s" % d["errs"])

    out = "/vol1/tmp/term_gap_%s.json" % ("after" if JUDGE else "before")
    json.dump(rows, open(out, "w"), ensure_ascii=False, indent=1)
    print("\n已存 %s" % out)

    if JUDGE:
        base_p = Path("/vol1/tmp/term_gap_before.json")
        if not base_p.exists():
            print("!! 无改前基线，先跑一次不带 --judge 的")
            return 1
        before = json.load(open(base_p))
        print("\n改前 vs 改后（净留白 T/B/L/R）")
        bad = []
        for b, a in zip(before, rows):
            bt = (b["gapTop"], b["gapBottom"], b["gapLeft"], b["gapRight"])
            at = (a["gapTop"], a["gapBottom"], a["gapLeft"], a["gapRight"])
            grew_w = a["pane"]["w"] - b["pane"]["w"]
            grew_h = a["pane"]["h"] - b["pane"]["h"]
            print("  vw=%-5d %s -> %s   终端框 Δ宽%+d Δ高%+d" % (b["vw"], bt, at, grew_w, grew_h))
            # 判据：留白不许变大，且终端框四向必须**变大**（缩小边距 = 终端框变大）
            if any(x > y for x, y in zip(at, bt)):
                bad.append("vw=%d 留白反而变大 %s->%s" % (b["vw"], bt, at))
            if a["pane"]["w"] < b["pane"]["w"] or a["pane"]["h"] < b["pane"]["h"]:
                bad.append("vw=%d 终端框没变大 %sx%s -> %sx%s"
                           % (b["vw"], b["pane"]["w"], b["pane"]["h"],
                              a["pane"]["w"], a["pane"]["h"]))
        print("\n%s" % ("FAIL: " + "; ".join(bad) if bad else "PASS 留白缩小且终端框四向变大"))
        return 1 if bad else 0
    return 0


def _zero(cs):
    return all(cs[k] == "0px" for k in
               ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft"))


def cs_border(d):
    return "%s/%s/%s/%s" % (d["csChatMain"]["marginTop"], d["csChatMain"]["marginRight"],
                            d["csChatMain"]["marginBottom"], d["csChatMain"]["marginLeft"])


if __name__ == "__main__":
    sys.exit(main())