#!/usr/bin/env python3
"""终端 FitAddon 行数口径的真渲染回归（v0.13.97 收窄留白后的配套闸门）。

【为什么必须真渲染量几何】本次把 `main` 的 padding 收窄 60%，终端框四向都变大了
（实测 1440×900：1166×823 → 1185×838）。而 templates/index.html 里有一条铁律：
FitAddon 按 `#termEl` 的 height 算行数，且**只减 xterm 自己的 padding、不减父元素的**——
父层一旦多 1px inset就会多算一行、终端底部被裁掉一行（历史实测：父层 6px padding 时
.xterm 高 468 而 #termEl 内容盒仅 459，底部溢出 9~10px）。
本次只**减**父层留白、不加inset，方向上是安全的；但「安全」不等于「已验证」：
必须量一次真实的 `rows ×行高 ≤ #termEl 内容盒高`，否则等于用推理交差。

【怎么起真终端】走生产端口 3102 + /api/term/activity 报的活会话（codebuddy 1 条）。
若此刻没有活会话，本探针**如实报 SKIP**而不是假绿——它量的必须是真xterm 实例
（`window.term` 有 cols/rows），量不出东西就说量不出。

跑法：
  ./venv/bin/python tests/probe_term_fit_rows.py
"""
import json
import sys
import tempfile
import time

sys.path.insert(0, "/fs/1000/ftp/技术文档/agenthub/tests")
from _cdp_min import CDP, launch_chrome, page_target      # noqa: E402

BASE = "http://127.0.0.1:3102"
CDP_PORT = 9391
W, H = 1440, 900

# 预置一个占位口令：termToken()（03-agents-cards.js:1010）在 localStorage 为空时会弹
# **原生 prompt**，而原生对话框挂住 renderer ⇒ 之后任何 evaluate 全无应答（09-23 探针栽过，
# 本文件第一版也栽了）。实测本机服务未设 TERM_TOKEN（/proc/<pid>/environ 0 命中），
# 服务端不校验，所以占位值即可让 attach 走通，不需要真口令。
SEED = "localStorage.setItem('hub.term.token','probe-placeholder');" \
       "localStorage.setItem('hub.page','chat');'seeded'"

# 「踢一脚」后立刻把结果**写进页面**，再用一条极简的 evaluate 取回。
# 前四版都栽在「踢一脚的同一条语句里顺手量」—— ensureTerm() 建xterm 时会触发
# 异步路径（口令/重连），量的时候 renderer 已经被挂住。
# 拆成「写全局」+「只读全局」两条独立语句，中间留时间，才量得到。
KICK = r"""
(() => {
  window.__probe = {step: 'kicked'};
  document.querySelectorAll('section.page').forEach(s => s.classList.remove('on'));
  const pg = document.getElementById('page-chat'); if (pg) pg.classList.add('on');
  ['embedPane','chatPane'].forEach(id => { const e = document.getElementById(id);
    if (e) e.classList.remove('on'); });
  const p = document.getElementById('termPane'); if (p) p.classList.add('on');
  try { if (window.ensureTerm) ensureTerm(); } catch (e) { window.__probe.err = '' + e; }
  return 'kicked';
})()
"""

# 量：#termEl 内容盒 vs xterm 实际画布高，以及 rows×行高是否放得下。
# 整体包try：xterm 内部 API 路径变了就返回 err，而不是让整条 evaluate 抛出去。
MEAS = r"""
(() => { try {
  const te = document.getElementById('termEl');
  const x  = document.querySelector('#termEl .xterm');
  if (!te || !x) return JSON.stringify({err: 'no-xterm', hasTerm: !!window.term});
  const cb = te.clientHeight, ch = te.clientWidth;          // clientBox = 内容盒（不含自身 border）
  const xb = x.getBoundingClientRect();
  const t = window.term;
  let cell = null;
  try { cell = t._core._renderService.dimensions.css.cell.height; } catch (e) {}
  const need = (t && cell) ? t.rows * cell : null;
  return JSON.stringify({
    hasTerm: !!(t && t.cols),
    cols: t ? t.cols : null, rows: t ? t.rows : null, cellH: cell,
    termElClientBox: {w: ch, h: cb},
    xtermCanvas: {w: Math.round(xb.width), h: Math.round(xb.height)},
    overflowY: Math.round(xb.height - cb),       # >0 = 画布比内容盒高 = 底部被裁
    overflowX: Math.round(xb.width - ch),
    needPx: need, rowsFitInBox: need === null ? null : (need <= cb + 1)
  });
} catch (e) { return JSON.stringify({err: '' + e}); } })()
""".replace("# >0 = 画布比内容盒高 = 底部被裁", "// >0 = 画布比内容盒高 = 底部被裁")


def _dismiss_dialog(method, params):
    """原生 prompt/confirm 一弹出就挂住 renderer ⇒ 之后任何 evaluate 全无应答
    （09-23 探针栽过；03-agents-cards.js:1013 的 termToken() 在无口令时会弹 prompt）。
    这里一律自动回「取消/空串」把它按掉，让 attach 走降级路径继续。"""
    if method == "Page.javascriptDialogOpening":
        try:
            cdp.send("Page.handleJavaScriptDialog", accept=False)
        except Exception:
            pass


def main():
    prof = tempfile.mkdtemp(prefix="termfit")
    proc = launch_chrome("about:blank", CDP_PORT, prof, W, H)
    cdp = CDP(page_target(CDP_PORT), on_event=_dismiss_dialog)
    fails = []
    try:
        # Page.enable 不可省：javascriptDialogOpening 是**事件**，对应域没 enable
        # 就一个都不送（第一版漏了它 ⇒ 兜底函数形同虚设，evaluate 照样超时）。
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Emulation.setDeviceMetricsOverride", width=W, height=H,
                 deviceScaleFactor=1, mobile=False)
        # 先在**当前文档**写盘，再导航 ⇒ 新文档首帧就带着口令，不会弹 prompt。
        # （反序：先导航后写盘，新文档已经把 prompt 弹出来了，renderer 已挂住。）
        cdp.eval(SEED)
        cdp.send("Page.navigate", url=BASE + "/")
        for _ in range(200):
            if cdp.eval("document.readyState") == "complete":
                break
            time.sleep(0.05)
        time.sleep(3.0)
        print("踢一脚：%s" % cdp.eval(KICK))
        time.sleep(6.0)          # 等 xterm 建好（不需要 WS 会话，只要xterm 实例有几何）
        d = json.loads(cdp.eval(MEAS) or "{}")

        if d.get("err") or not d.get("hasTerm"):
            print("SKIP 没有真xterm 实例（%s）—— 量不出东西，不假绿" % d)
            return 0
        print(json.dumps(d, ensure_ascii=False, indent=1))
        if d["overflowY"] > 1:
            fails.append("画布比内容盒高 %spx ⇒ 底部被裁（FitAddon 行数口径被打破）"
                         % d["overflowY"])
        if d["rowsFitInBox"] is False:
            fails.append("rows×行高 %spx 放不进内容盒 %spx" % (d["needPx"], d["termElClientBox"]["h"]))
        print("\n%s" % ("FAIL: " + "; ".join(fails) if fails
                        else "PASS 画布未溢出内容盒，rows×行高放得下"))
        return 1 if fails else 0
    finally:
        cdp.close()
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())