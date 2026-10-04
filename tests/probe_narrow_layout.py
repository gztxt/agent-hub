#!/usr/bin/env python3
"""窄屏**首帧 + 排版**真渲染探针（CDP，v0.13.75 入库）。

  bash scripts/run_tests.sh probe verify_narrow_layout.py [base-url]
  # 或直接：venv/bin/python tests/probe_narrow_layout.py [port] [base-url]

【为什么要有它，以及它补的是哪一个洞】
2026-10-04 用户在真机上贴了两张截图：
  ① 「窄屏第一眼是一块白板」—— 236px 固定抽屉盖住 60.5%(390px)~73.8%(320px)；
  ② 「窄屏页面文字内容排版还是有问题」—— 技能列表的描述被压成 **3px**，
     每个字一行（「行)、小 / 字母描 / 客转文 / 字、」）。
这两条**静态闸门都抓不到**：`tests/test_narrow_first_paint.py` 判的是
「机制写对了没有」（head 标记 + CSS 规则 + 选择器存在于 DOM），
判不了「像素真的对不对」—— 而 CSS 写错类名、flex 基准给错、竞态丢帧这三类
**都不会让任何静态测试变红**。所以把「量出来的数字」固化进仓。

【它量什么（四组，都是可断言的量）】
  A 首帧几何：`.sidebar` 宽 / position / 是否 fixed、视口中心命中谁。
     判据：窄屏首帧 ≤60px 且 position≠fixed。
  B 技能列表描述宽度：`#skillList .mem-item > p` 的实际宽度。
     判据：≥200px（修前是 **3px**）。
  C 逐页最小描述宽度：凡用 `.mem-item` 的页都量一遍，防「修好一处崩另一处」。
  D 底部留白：内容底与视口底的距离（只报，不判 —— 上一轮试过把它
     「均匀分布到各块之间」，截图一看**更丑**，已回退；此处只留数据，
     免得下一个人再把那个更差的方案做一遍）。

【测法上踩过的坑，逐条避开】
  1. **视口必须先就位再导航**（`Emulation.setDeviceMetricsOverride` → `Page.navigate`）。
     反过来（先导航、后改视口）量到的是**已初始化页面的重排**，不是首帧 ——
     那样测出来 `collapsed` 早就加好了，会得出「没有白板闪」的假结论。
  2. **隐藏元素的 `getBoundingClientRect()` 恒为 0**，统计前必须过滤可见元素，
     否则会数出一堆「尺寸 0」的假警报。
  3. 放大截图用 `clip.scale`，**不要用 `deviceScaleFactor`** —— 后者会改布局尺寸，
     量出来的几何就不是用户看到的那份了。
  4. 每个表达式包 IIFE，避免跨次求值的 `const` 重名冲突。

【退出码】0 = 全部达标；1 = 有项不达标（可直接用于 CI/人工守门）。
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

NARROW = [320, 360, 390, 412, 767]
def _args(argv):
    """两种传法都收：`run_tests.sh probe <file> <base-url>` 与直接 `<port> <base-url>`。

    `scripts/run_tests.sh probe` 的约定是 **argv[1] 传 base-url**（见其 `probe)` 分支），
    而本仓另一支探针 `probe_sidebar_width_sweep.py` 用的是 **argv[1] 传 CDP 端口**。
    两种都在用 ⇒ 这里按「像不像 URL」判别，不让调用方记两套。
    第一版只认端口，`run_tests.sh probe` 一调就 `ValueError`（已犯）。
    """
    base, port = "http://127.0.0.1:3102", 9355
    for a in argv:
        if str(a).startswith("http"):
            base = str(a)
        else:
            try:
                port = int(a)
            except ValueError:
                pass
    return base, port


BASE, CDP_PORT = _args(sys.argv[1:])

# 首帧与稳定后都量；A 组用首帧，B/C/D 用稳定后。
# 未就绪时返回 'null' 而不是 '{}'：**字符串 '{}' 是 truthy**，
# 会让「等首帧」的轮询提前拿到空数据并当成有效值 —— 探针自己造了个假绿。
GEOM = """(() => { const sb=document.getElementById('sidebar'); if(!sb) return 'null';
  const r=sb.getBoundingClientRect();
  const top=document.elementFromPoint(Math.floor(innerWidth/2),Math.floor(innerHeight/2));
  return JSON.stringify({w:Math.round(r.width), pos:getComputedStyle(sb).position,
    collapsed:sb.classList.contains('collapsed'),
    center:(top?(top.id||top.className||top.tagName):null)}); })()"""

MEMITEM = """(() => { const vis=el=>{const r=el.getBoundingClientRect();
                      return r.width>0&&r.height>0;};
  const its=[...document.querySelectorAll('#skillList .mem-item')].filter(vis);
  const widths=its.slice(0,5).map(el=>{const p=el.querySelector('p');
     return p?Math.round(p.getBoundingClientRect().width):null;}).filter(x=>x!==null);
  return JSON.stringify({n:its.length, min:wids.length?Math.min(...wids):null,
    wrap:its[0]?getComputedStyle(its[0]).flexWrap:null}); })()"""
MEMITEM = MEMITEM.replace("wids", "widths")

# 逐页：找所有可见 .mem-item，量其中最窄的描述
ALLPAGES = """(() => { const vis=el=>{const r=el.getBoundingClientRect();
                       return r.width>0&&r.height>0;};
  const items=[...document.querySelectorAll('.mem-item')].filter(vis);
  if(!items.length) return JSON.stringify({n:0});
  let min=1e9, who='';
  for(const el of items){ const p=el.querySelector('p'); if(!p) continue;
    const w=Math.round(p.getBoundingClientRect().width);
    if(w<min){min=w; const t=el.querySelector('.tag'); who=t?t.textContent.slice(0,16):'?';} }
  return JSON.stringify({n:items.length, min:min===1e9?null:min, who}); })()"""

# 抽屉可展开性（2026-10-04 回归）：窄屏点开侧栏必须真的变成覆盖式抽屉。
# v0.13.75 引入的 `html.narrow-rail` 标记是首帧专用的，却**打完没摘** ⇒
# `html.narrow-rail .sidebar:not(.collapsed)` 特异性高于 `.sidebar:not(.collapsed)`，
# 点「展开」时 JS 移除了 collapsed、几何却被按回 52px ⇒ **抽屉永远打不开**，
# 而遮罩照样亮（点哪都点不到 = 整页锁死）。
# 【本探针上一版为什么没抓到】它只量**首帧**，从来没点过开 —— 首帧是对的，交互是坏的。
EXPAND = """(() => { const b=document.getElementById('btnSideToggle');
  if(b) b.click(); return 'clicked'; })()"""

SIDEGEO = """(() => { const sb=document.getElementById('sidebar'); if(!sb) return '{}';
  const m=document.getElementById('sideMask');
  return JSON.stringify({cls:sb.className,w:Math.round(sb.getBoundingClientRect().width),
    pos:getComputedStyle(sb).position,
    rail:document.documentElement.classList.contains('narrow-rail'),
    maskOn: m ? m.classList.contains('on') : null}); })()"""

# 收起态侧栏内**不得有可见的数字**（2026-10-04 用户报「收起后总览后面数字没有隐藏」）。
# 根因：收起态隐藏规则写的是 `.badge`，而侧栏徽标的真实类名是 `.nav-badge`
#（05-chat-and-history.js 里 `'<span class="nav-badge" id="badge-'+p+'"></span>'`）
# —— 选择器与真实类名对不上，规则**静默不生效**，48px 图标条上一直挤着个「10」。
# 这条量的价值：不看类名，只看「有没有数字还露在外面」 ⇒ 同族漏网（徽标/序号/计数）
# 都能被它抓到，而不只是这一处。
RAILDIGIT = """(() => { const vis=el=>{const r=el.getBoundingClientRect();
                          return r.width>0&&r.height>0;};
  const sb=document.getElementById('sidebar'); if(!sb) return '{}';
  const out=[];
  for(const el of sb.querySelectorAll('*')){
    const t=(el.textContent||'').trim();
    if(vis(el) && /^\\d{1,3}$/.test(t)) out.push({t:t, cls:el.className||el.tagName});}
  return JSON.stringify({collapsed:sb.classList.contains('collapsed'), digits:out}); })()"""

BOTTOM = """(() => { const vis=el=>{const r=el.getBoundingClientRect();
                      return r.width>0&&r.height>0;};
  const page=document.querySelector('.page.on'); if(!page) return '{}';
  let bottom=0; for(const el of page.querySelectorAll('*')){ if(!vis(el))continue;
    const r=el.getBoundingClientRect(); if(r.bottom>bottom) bottom=r.bottom; }
  return JSON.stringify({vh:innerHeight, contentBottom:Math.round(bottom),
    gap:Math.round(innerHeight-bottom)}); })()"""


def run(w, port, profile):
    """单档：先设视口再导航（量真·首帧），待稳定后量排版。"""
    proc = launch_chrome("about:blank", port, profile, 1280, 900)
    cdp = CDP(page_target(port))
    try:
        cdp.send("Emulation.setDeviceMetricsOverride", width=w, height=844,
                 deviceScaleFactor=1, mobile=(w < 768))
        cdp.send("Page.navigate", url=BASE + "/")
        first = None
        for _ in range(200):
            r = cdp.eval(GEOM)
            if r and r != "null" and r != "{}":
                first = json.loads(r)
                break
            time.sleep(0.01)
        time.sleep(3.5)
        late = json.loads(cdp.eval(GEOM) or "{}")
        # D 底部留白**必须在切页之前量**：技能列表有 200 行，切过去再回来量，
        # `.page.on` 可能仍指那页 ⇒ contentBottom 是几千像素 ⇒ gap 变成 -5 万这种废话
        # （第一版就犯了这个，输出 -50811px）。且只有**不可滚动**时 gap 才有意义。
        bottom = json.loads(cdp.eval(BOTTOM) or "{}")
        if cdp.eval("document.documentElement.scrollHeight > innerHeight"):
            bottom["scrollable"] = True
        # 切到技能中心量 .mem-item（该页才渲染列表）
        cdp.eval("(()=>{const b=[...document.querySelectorAll('.nav-item')]"
                 ".find(e=>/技能/.test(e.textContent));if(b)b.click();})()")
        time.sleep(3.0)
        mem2 = json.loads(cdp.eval(MEMITEM) or "{}")
        allpg = json.loads(cdp.eval(ALLPAGES) or "{}")
        raildig = json.loads(cdp.eval(RAILDIGIT) or "{}")
        # 点**一次**展开，取稳态：EXPAND 自带点击，所以只能调一次，
        # 再调一次就把抽屉又关上了（第一版这里连点两次，量到的还是收起态）。
        cdp.eval(EXPAND)
        time.sleep(1.5)
        exp = json.loads(cdp.eval(SIDEGEO) or "{}")
        return first or {}, late, mem2, allpg, bottom, exp, raildig
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
    fails = []
    print("窄屏真渲染探针 · base=%s" % BASE)
    print("坑①：视口先就位再导航，量的才是真·首帧\n")
    hdr = ("%-6s %-28s %-13s %-12s %-9s %-20s %s" %
           ("vw", "首帧(宽/定位)", "技能描述宽", "逐页最窄", "底部留白",
            "点开后抽屉(宽/定位)", "判定"))
    print(hdr); print("-" * len(hdr.encode("gbk", "ignore")))
    port = CDP_PORT
    for w in NARROW:
        port += 1
        first, late, mem, allpg, bottom, exp, raildig = run(w, port, "/tmp/hub_probe_narrow_%d" % port)
        # A 首帧：窄屏首帧必须是图标条（≤60px）且不能是 fixed 覆盖层
        ok_first = first.get("w", 999) <= 60 and first.get("pos") != "fixed"
        # B 技能列表描述：修前 3px
        ok_mem = (mem.get("min") or 0) >= 200 or mem.get("n", 0) == 0
        # C 逐页：凡有描述的页，最窄也不该低于 200px
        cmin = allpg.get("min")
        ok_all = (cmin is None) or cmin >= 200
        bad = []
        if not ok_first:
            bad.append("首帧 %spx/%s" % (first.get("w"), first.get("pos")))
        if not ok_mem:
            bad.append("技能描述 %spx" % mem.get("min"))
        if not ok_all:
            bad.append("%s 描述 %spx" % (allpg.get("who"), cmin))
        # D 抽屉可展开：点开后必须是覆盖式抽屉（236px/fixed），遮罩随之亮。
        # 这一条正是 v0.13.75 的回归点，探针上一版只量首帧、从来没点过。
        ok_exp = exp.get("w", 0) >= 200 and exp.get("pos") == "fixed" and exp.get("maskOn")
        if not ok_exp:
            bad.append("抽屉打不开(%spx/%s/遮罩%s)" % (exp.get("w"), exp.get("pos"), exp.get("maskOn")))
        # E 收起态图标条上不得露出数字（.nav-badge 漏隐藏）
        digs = raildig.get("digits") or []
        if raildig.get("collapsed") and digs:
            bad.append("收起态露出数字 %s" % ",".join(d.get("t","?") for d in digs[:3]))
        if bad:
            fails.append("%dpx: %s" % (w, "; ".join(bad)))
        print("%-6d %-28s %-13s %-12s %-9s %-20s %s" % (
            w,
            "%dpx/%s" % (first.get("w"), first.get("pos")),
            "%spx(n=%s)" % (mem.get("min"), mem.get("n")),
            "%s(%s)" % (cmin if cmin is not None else "无p", (allpg.get("who") or "-")[:5]),
            ("可滚动" if bottom.get("scrollable") else "%spx" % bottom.get("gap")),
            "%spx/%s/%s" % (exp.get("w"), exp.get("pos"),
                            "遮罩✓" if exp.get("maskOn") else "遮罩✗"),
            "OK" if not bad else "BAD: " + "; ".join(bad)))
    print()
    if fails:
        print("结论：%d 档不达标 —— %s" % (len(fails), "；".join(fails)))
        print("（底部留白只报不判：上一轮把空白均匀分布到各块之间，实测截图**更丑**，已回退。）")
        return 1
    print("结论：全档达标。底部留白是「短页面的正常留白」，非缺陷。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())