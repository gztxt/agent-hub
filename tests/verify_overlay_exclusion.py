#!/usr/bin/env python3
"""浮层唯一性验收（真鼠标点击，390×844 与 1280×800）。

为什么必须真点：前几轮我用 elementFromPoint 间接判"被谁盖住"，从没按坐标点过一次，
结果连续几轮"探针全绿 + 用户说还是坏的"。这条闸门走的就是用户的操作路径：
展开抽屉 → 点设置 → 点总览 → 看正文到底能不能点。

判据全部 PASS/FAIL 打印，任一 FAIL 退出码非 0（可直接挂进 run_tests.sh 的 L2）。
"""
import json, os, subprocess, sys, time
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
from _cdp_min import CDP, launch_chrome, page_target

BASE = "http://127.0.0.1:3102"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9371
OVERLAYS = ("#sideMask", "#detailDrawer", "#settingsDrawer", "#regMask")
results = []


def chk(name, ok, detail=""):
    results.append(bool(ok))
    print("  %-4s %-46s %s" % ("PASS" if ok else "FAIL", name, detail))


STATE = r"""
(() => {
  const key = el => !el ? 'none' : (el.id ? '#'+el.id : el.tagName.toLowerCase() +
      (typeof el.className==='string' && el.className ? '.'+el.className.split(/\s+/)[0]:''));
  const on = id => { const e = document.getElementById(id); return !!(e && e.classList.contains('on')); };
  const box = id => { const e = document.getElementById(id); if (!e) return null;
      const b = e.getBoundingClientRect();
      return (b.width>0 && b.height>0 && getComputedStyle(e).display!=='none')
             ? [Math.round(b.left), Math.round(b.top), Math.round(b.right), Math.round(b.bottom)] : null; };
  const sb = document.getElementById('sidebar');
  const ccx = Math.round(innerWidth*0.6), ccy = Math.round(innerHeight*0.5);
  const drawn = ['detailDrawer','settingsDrawer','regMask'].filter(on);
  const covering = OVERLAY_IDS.filter(id => { const b = box(id);
      return b && b[0] <= %s && b[2] >= %s && (b[3]-b[1]) > innerHeight*0.5; });
  return { vw: innerWidth, collapsed: sb.classList.contains('collapsed'),
           maskOn: on('sideMask'), drawersOpen: drawn, sidebarBox: box('sidebar'),
           coveringContent: covering, contentHit: key(document.elementFromPoint(ccx, ccy)),
           contentClickable: !OVERLAY_IDS.includes('#' + (document.elementFromPoint(ccx, ccy)||{id:''}).id),
           pageOn: [...document.querySelectorAll('section.page.on')].map(e=>e.id).join(','),
           errs: (window.__errs||[]).slice(0,3) };
})()
""".replace("OVERLAY_IDS", json.dumps(list(OVERLAYS)))


def click(c, sel, x=None, y=None, W=390, H=844):
    """真事件点击；元素在视口外直接判定点不到（这正是本次事故的关键条件）。"""
    if (x, y) == (None, None):
        raw = c.eval("(() => { const e = document.querySelector(%s); if (!e) return 'null';"
                     " const b = e.getBoundingClientRect();"
                     " return JSON.stringify([Math.round(b.left+b.width/2), Math.round(b.top+b.height/2)]); })()"
                     % json.dumps(sel))
        if not raw or raw == "null":
            return None, "元素不存在"
        x, y = json.loads(raw)
    if not (0 <= x < W and 0 <= y < H):
        return None, "坐标 (%d,%d) 在视口外→点不到" % (x, y)
    for typ in ("mousePressed", "mouseReleased"):
        c.send("Input.dispatchMouseEvent", type=typ, x=x, y=y, button="left",
               clickCount=1, buttons=1 if typ == "mousePressed" else 0)
    time.sleep(0.8)
    return (x, y), "点 (%d,%d)" % (x, y)


def load(c, W, H, setup):
    c.send("Emulation.setDeviceMetricsOverride", width=W, height=H,
           deviceScaleFactor=2, mobile=(W < 768))
    c.eval(setup)
    c.send("Page.reload", ignoreCache=True)
    for _ in range(18):
        time.sleep(0.4)
        d = c.eval(STATE % (int(W * 0.6), int(W * 0.6)))
        if d and d.get("sidebarBox"):
            return d
    raise SystemExit("页面没起来")


proc = launch_chrome(BASE + "/", PORT, "/tmp/hub_overlay", 390, 844)
time.sleep(3.0)
c = CDP(page_target(PORT))
c.send("Page.enable"); c.send("Runtime.enable")
c.send("Page.addScriptToEvaluateOnNewDocument", source=(
    "window.__errs=[];addEventListener('error',e=>__errs.push(String(e.message)));"
    "addEventListener('unhandledrejection',e=>__errs.push('reject:'+String(e.reason).slice(0,120)));"))

print("== 窄屏 390×844：用户操作路径 ==")
d = load(c, 390, 844, "localStorage.clear();localStorage.setItem('hub.sidebar.narrow','0');")
chk("起点：抽屉展开且遮罩在", d["maskOn"] and not d["collapsed"], "mask=%s" % d["maskOn"])

_, why = click(c, "#btnSettings")
d = c.eval(STATE % (234, 234))
chk("点「设置」→ 设置抽屉开", d["drawersOpen"] == ["settingsDrawer"], why)
chk("点「设置」→ 侧栏自动收起（不叠两层）", d["collapsed"], "drawers=%s" % d["drawersOpen"])
chk("点「设置」→ 最多一个抽屉在开", len(d["drawersOpen"]) <= 1, str(d["drawersOpen"]))
chk("点「设置」→ 遮罩仍在（点空白可逃）", d["maskOn"], "")

_, why = click(c, "#btnNavHome")
d = c.eval(STATE % (234, 234))
chk("点「总览」→ 设置抽屉被导航关掉", d["drawersOpen"] == [], "drawers=%s %s" % (d["drawersOpen"], why))
chk("点「总览」→ 遮罩关掉", not d["maskOn"], "")
chk("★正文中心不再被任何浮层盖住", not d["coveringContent"],
    "残留层=%s / 中心命中=%s" % (d["coveringContent"], d["contentHit"]))
chk("★导航确实生效（页面=classroom）", d["pageOn"] == "page-classroom", d["pageOn"])

# 逃生路径：重开设置，点侧边 8% 留白（遮罩区）应一次关干净
click(c, "#btnSettings")
d = c.eval(STATE % (234, 234))
chk("重开设置后抽屉在开", d["drawersOpen"] == ["settingsDrawer"], "")
_, why = click(c, None, x=10, y=430)
d = c.eval(STATE % (234, 234))
chk("点遮罩留白 → 抽屉与遮罩一次关干净", not d["coveringContent"] and not d["maskOn"],
    "%s 中心命中=%s" % (why, d["contentHit"]))
chk("窄屏全程零 JS 异常", not d["errs"], str(d["errs"]))

print("\n== 桌面 1280×800：不得改坏原有行为 ==")
d = load(c, 1280, 800, "localStorage.clear();")
chk("桌面默认展开侧栏", not d["collapsed"] and d["sidebarBox"][2] - d["sidebarBox"][0] > 200,
    "宽=%s" % (d["sidebarBox"][2] - d["sidebarBox"][0]))
click(c, "#btnSettings")
d = c.eval(STATE % (768, 768))
chk("桌面点设置 → 抽屉开且侧栏不被强制收起", d["drawersOpen"] == ["settingsDrawer"] and not d["collapsed"], "")
chk("桌面不显示窄屏遮罩", not d["maskOn"], "")
click(c, "#btnNavHome")
d = c.eval(STATE % (768, 768))
chk("桌面导航后正文可点", not d["coveringContent"], "中心命中=%s" % d["contentHit"])

proc.kill()
ok = all(results)
print("\n%s  (%d/%d)" % ("全部 PASS ✅" if ok else "存在 FAIL ❌", sum(results), len(results)))
sys.exit(0 if ok else 1)
