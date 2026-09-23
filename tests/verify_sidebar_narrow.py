#!/usr/bin/env python3
"""L2 实况探针：窄屏侧栏抽屉不得把页面盖住（2026-09-23 白板事故的常驻回归闸门）。

跑法：venv/bin/python tests/verify_sidebar_narrow.py [http://127.0.0.1:3102]
需要：服务在跑 + 本机 chromium。**只读**——不发写请求、不建 pty 会话，不打扰在用终端。

为什么必须拿真浏览器：这是合成布局层的事，`test_*.py` 在 node/venv 里量不到
「fixed 白底抽屉把视口中心那一击整个接走」。

判据不是"截图好不好看"，而是可文本化的三条：
  · `.sidebar` 的 collapsed 类在窄屏首屏必须为真；
  · 视口正中 `elementFromPoint()` 命中的元素**不得**是侧栏子树（=它真的挡住了交互）；
  · 首屏解析不得写 localStorage（写了就是把当次宽度固化成全局偏好）。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cdp_min import CDP, launch_chrome, page_target   # noqa: E402

BASE = next((a for a in sys.argv[1:] if a.startswith("http")), "http://127.0.0.1:3102")
PORT = int(__import__("os").environ.get("HUB_CDP_PORT", "9341"))
PROFILE = "/tmp/hub_sidebar_probe"

MEASURE = r"""
(() => {
  const sb = document.getElementById('sidebar');
  if (!sb) return {ready:false};
  const r = sb.getBoundingClientRect(), cs = getComputedStyle(sb);
  const hit = document.elementFromPoint(Math.round(innerWidth/2), Math.round(innerHeight/2));
  const mask = document.getElementById('sideMask');
  return { ready:true, vw: innerWidth,
           collapsed: sb.classList.contains('collapsed'),
           sbW: Math.round(r.width), pos: cs.position, bg: cs.backgroundColor,
           drawerOnScreen: (cs.position === 'fixed' && r.right > 2 && r.left < innerWidth - 2),
           centerHit: hit ? (hit.id ? '#'+hit.id : hit.tagName.toLowerCase()+
                     (typeof hit.className==='string'&&hit.className?'.'+hit.className.split(' ')[0]:'')) : 'none',
           centerCoveredBySidebar: !!(hit && (hit === sb || sb.contains(hit))),
           maskOn: mask ? mask.classList.contains('on') : null,
           legacy: localStorage.getItem('hub.sidebar'),
           narrowKey: localStorage.getItem('hub.sidebar.narrow'),
           wideKey: localStorage.getItem('hub.sidebar.wide') };
})()
"""


def main():
    proc = launch_chrome(BASE + "/", PORT, PROFILE)
    time.sleep(3.0)
    c = CDP(page_target(PORT))
    c.send("Page.enable"); c.send("Runtime.enable")
    fails = []

    def meas(tries=24):
        for _ in range(tries):
            time.sleep(0.5)
            try:
                d = c.eval(MEASURE)
            except Exception:
                continue
            if isinstance(d, dict) and d.get("ready"):
                return d
        raise SystemExit("量不到 #sidebar（页面结构变了？探针需同步）")

    def check(name, ok, detail):
        print(("  PASS  " if ok else "  FAIL  ") + name + "   [" + detail + "]")
        if not ok:
            fails.append(name)

    def metrics(w, h):
        c.send("Emulation.setDeviceMetricsOverride", width=w, height=h,
               deviceScaleFactor=2, mobile=(w < 768))

    print("目标：%s   （headless chromium + CDP）" % BASE)

    print("\n[A] 事故现场原样重放：存量被污染的手机（只有宽屏留下的 legacy='0'）")
    c.eval("localStorage.clear(); localStorage.setItem('hub.sidebar','0');")
    metrics(390, 844); c.send("Page.reload", ignoreCache=True)
    a = meas()
    print("   " + json.dumps(a, ensure_ascii=False))
    check("A 窄屏首屏收成图标条、不遮内容", a["collapsed"] and not a["centerCoveredBySidebar"],
          "collapsed=%s w=%s pos=%s centerHit=%s" % (a["collapsed"], a["sbW"], a["pos"], a["centerHit"]))
    check("A 首屏解析不写盘（否则又会把当次宽度固化）", a["narrowKey"] is None and a["wideKey"] is None,
          "narrow=%r wide=%r" % (a["narrowKey"], a["wideKey"]))
    check("A 存量污染值自然失效（用户不必清缓存）", a["legacy"] == "0" and a["collapsed"] is True,
          "legacy=%r → collapsed=%s" % (a["legacy"], a["collapsed"]))

    print("\n[B] 跨断点：同一页拖宽（老代码必须刷新才好）")
    metrics(1280, 800); time.sleep(1.2)
    b = meas(6)
    print("   " + json.dumps(b, ensure_ascii=False))
    check("B 宽屏自动展开、且不回退 B 路 240px 成果",
          b["collapsed"] is False and not b["centerCoveredBySidebar"] and b["sbW"] == 240,
          "collapsed=%s w=%s" % (b["collapsed"], b["sbW"]))

    print("\n[C] 跨断点回窄：应自动收起")
    metrics(390, 844); time.sleep(1.2)
    d = meas(6)
    print("   " + json.dumps(d, ensure_ascii=False))
    check("C 回窄屏自动收成图标条", d["collapsed"] is True and not d["centerCoveredBySidebar"],
          "collapsed=%s w=%s" % (d["collapsed"], d["sbW"]))

    print("\n[D] 用户在窄屏**显式**展开 ⇒ 偏好必须被尊重（合法状态，不是缺陷）")
    c.eval("localStorage.setItem('hub.sidebar.narrow','0');")
    c.send("Page.reload", ignoreCache=True)
    e = meas()
    print("   " + json.dumps(e, ensure_ascii=False))
    check("D 本档显式偏好生效且遮罩在位（点遮罩即收）",
          e["collapsed"] is False and e["maskOn"] is True,
          "collapsed=%s maskOn=%s w=%s" % (e["collapsed"], e["maskOn"], e["sbW"]))

    print("\n[E] 回到宽屏档：窄屏的偏好不得渗回桌面")
    metrics(1280, 800); time.sleep(1.2)
    f = meas(6)
    print("   " + json.dumps(f, ensure_ascii=False))
    check("E 手机上的展开选择不影响桌面档", f["collapsed"] is False,
          "collapsed=%s wide=%r narrow=%r" % (f["collapsed"], f["wideKey"], f["narrowKey"]))

    c.close(); proc.kill()
    print("\n" + ("全部通过 ✅" if not fails else "失败 %d 项 ❌：%s" % (len(fails), fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
