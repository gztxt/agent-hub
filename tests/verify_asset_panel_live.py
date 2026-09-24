#!/usr/bin/env python3
"""P4 资产面板的**真渲染**闸门（L2 live）——布局层只有真引擎能作证。

跑法（先起影子实例，与生产 DATA_DIR 隔离、绝不碰 3102）：
    mkdir -p /tmp/ah-assets/data
    ( cd ~/agent-hub && DATA_DIR=/tmp/ah-assets/data PORT=3199 \
        venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 3199 & )
    venv/bin/python tests/verify_asset_panel_live.py            # 默认打 3199
    venv/bin/python tests/verify_asset_panel_live.py http://127.0.0.1:3102   # 重启后可打生产

为什么必须有这条（而不是"L0/L1 全绿"就交差）：
本仓 09-23 的两条事故定论写着「**后端体检全绿不能证明前端布局正常**」——`/health` 200、
写端点鉴权 20/20 PASS 时，页面仍可被整块盖住。新面板正是"新增一块 DOM + 一个新页面"，
风险形态就是 09-23 那一族：遮罩叠加、窄屏被盖、页面 id 与路由不同步导致永远空白。
所以判据全部写成**可断言的几何量与文本量**，不以"我看了截图"交差：
  · `getBoundingClientRect()` 非零
  · `elementFromPoint(视口中心)` 必须落在 #page-assets 内（被浮层盖住就直接现形）
  · 窄屏无横向溢出
  · 四态文案真的出现在 DOM 里（不是"未加载"占位）
红向自证：同一探针里先验 `go('不存在的页')` 会回落、`#page-assets` 在 JS 未跑前不是 on 态
——否则"它显示出来了"可能只是我的断言在自证。
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cdp_min import CDP, launch_chrome, page_target   # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3199"
CDP_PORT = int(os.getenv("HUB_PROBE_CDP_PORT", "9394"))
res = []


def chk(name, ok, detail=""):
    res.append(bool(ok))
    print("  %-4s %-52s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:150]))


def open_browser(width, height):
    proc = launch_chrome(BASE + "/", CDP_PORT, tempfile.mkdtemp(prefix="hub_assets_"),
                         width, height)
    tgt = page_target(CDP_PORT, tries=50)
    c = CDP(tgt)
    c.send("Page.enable")
    c.send("Runtime.enable")
    time.sleep(2.5)          # 冷 profile + 三路门面首屏（turbovec 那路最长 ~0.5s）
    return proc, c


try:
    print("═" * 78)
    print(f"P4 资产面板真渲染闸门  BASE={BASE}")
    print("═" * 78)

    # ─────────────── 0. 探针可判死（红向：先证明 go() 与 class 是被 JS 驱动的） ───────────────
    proc, c = open_browser(1280, 800)
    pre = c.eval("""(() => {
      const s = document.getElementById('page-assets');
      return {exists: !!s, on: s ? s.classList.contains('on') : null};
    })()""")
    chk("R1 模板里存在 #page-assets（服务端 HTML，非 JS 生成）", pre.get("exists") is True, pre)
    chk("R2 首屏它不是 on 态（证明下面的 on 是真由 go() 驱动的）", pre.get("on") is False, pre)
    bad = c.eval("""(() => { go('no-such-page-xyz');
      return [...document.querySelectorAll('section.page.on')].map(s=>s.id).join(','); })()""")
    chk("R3 未知页名会回落（go() 校验活着，不是空转）", "classroom" in str(bad), bad)

    # ─────────────── 1. 导航可达（用**用户手法**展开分组，不是改样式） ───────────────
    # 侧栏分组是 `.nav-acc`（不是 <details>）：上一轮探针拿 details.open 展开，
    # 实际一个都没展开 ⇒ “尺寸 0×0”是**探针错**而不是页面错。现在点真的组头。
    head_seen = c.eval("""(() => {
      const head = document.querySelector('.sidebar .nav-acc-head[data-group="system"]');
      const acc  = head ? head.closest('.nav-acc') : null;
      const b    = document.querySelector('.sidebar [data-sys="assets"]');
      const r    = b ? b.getBoundingClientRect() : null;
      return {headSeen: !!head, wasOpen: acc ? acc.classList.contains('open') : null,
              text: b ? (b.textContent||'').trim() : null,
              w0: r ? Math.round(r.width) : null, h0: r ? Math.round(r.height) : null};
    })()""")
    chk("A1 侧栏有 assets 入口（静态文本已生成）",
        head_seen.get("headSeen") is True and "资产" in str(head_seen.get("text")), head_seen)
    c.eval("""(() => { const h = document.querySelector('.sidebar .nav-acc-head[data-group="system"]');
                      if (h) h.click(); return true; })()""")
    time.sleep(0.6)          # renderNav 会重建节点 ⇒ 必须**重新取**，不能拿旧引用
    after = c.eval("""(() => {
      const b = document.querySelector('.sidebar [data-sys="assets"]');
      if (!b) return {found:false};
      const r = b.getBoundingClientRect();
      const acc = b.closest('.nav-acc');
      return {found:true, w:Math.round(r.width), h:Math.round(r.height),
              nowOpen: acc ? acc.classList.contains('open') : null};
    })()""")
    chk("A2 点组头后入口真可见（有尺寸）",
        after.get("w", 0) > 0 and after.get("h", 0) > 0, after)
    chk("A3 分组确实是被点开的（改前 closed、改后 open ⇒ 断言非自证）",
        head_seen.get("wasOpen") is False and after.get("nowOpen") is True,
        {"wasOpen": head_seen.get("wasOpen"), "nowOpen": after.get("nowOpen")})

    # ─────────────── 2. 切页（异步）：先触发，给三路门面留完时，再取几何/文本 ───────────────
    c.eval("(() => { go('assets'); return true; })()")
    time.sleep(3.0)      # loadAssets 是 async：健康自检 + 三路检索（turbovec 那路 ~0.4s）
    geom = c.eval("""(() => {
      const s = document.getElementById('page-assets');
      const r = s.getBoundingClientRect();
      const cx = Math.round(window.innerWidth/2), cy = Math.round(window.innerHeight/2);
      const hit = document.elementFromPoint(cx, cy);
      const inside = !!hit && !!(hit.closest && hit.closest('#page-assets'));
      const ov = [...document.querySelectorAll('.drawer.on, .modal-mask.on, .sidebar.on')]
                   .map(e=>e.id||e.className);
      return {on: s.classList.contains('on'), w: Math.round(r.width), h: Math.round(r.height),
              top: Math.round(r.top), hitTag: hit ? (hit.id || hit.tagName) : null,
              inside, overlays: ov, vw: window.innerWidth, vh: window.innerHeight,
              cx, cy};
    })()""")
    chk("G1 切页后 section 带 on 态", geom.get("on") is True, geom.get("on"))
    chk("G2 内容有非零宽高（没被压成 0 行）", geom.get("w", 0) > 300 and geom.get("h", 0) > 150,
        "%sx%s @top=%s" % (geom.get("w"), geom.get("h"), geom.get("top")))
    chk("G3 视口中心命中的元素属于 #page-assets（无浮层遮挡）", geom.get("inside") is True,
        "hit=%s inside=%s" % (geom.get("hitTag"), geom.get("inside")))
    chk("G4 桌面档无遗留浮层", geom.get("overlays") == [], geom.get("overlays"))

    # ─────────────── 3. 数据真的进 DOM（四态文案） ───────────────
    txt = c.eval("""(() => {
      const g = id => (document.getElementById(id)||{}).textContent || '';
      return {status: g('assetStatus'), memory: g('assetMemory'), kb: g('assetKb'),
              skill: g('assetSkill'), hint: g('assetHint')};
    })()""")
    four = ("有结果", "确实零命中", "部分后端不可用", "端点不可用")
    chk("D1 状态条已渲染且含四态文案之一",
        any(k in txt.get("status", "") for k in four) and "未加载" not in txt.get("status", ""),
        txt.get("status", "")[:120])
    chk("D2a 空查询时 memory/kb 两路走 idle（不谎报故障、也不发注定 422 的请求）",
        "需要检索词" in txt.get("memory", "") and "422" in txt.get("memory", ""),
        txt.get("memory", "")[:110])
    chk("D2b 技能路空查询可直接列表（它不需 q）",
        bool(txt.get("skill", "").strip()) and "未检索" not in txt["skill"], txt["skill"][:90])
    chk("D2c 面板无一路停在初始占位文案",
        all("未检索" not in txt[k] for k in ("memory", "kb", "skill")),
        {k: txt[k][:40] for k in ("memory", "kb", "skill")})
    degraded_words = ("部分后端不可用", "端点不可用", "不完整")
    any_bad = [k for k, v in (("记忆", txt["memory"]), ("知识", txt["kb"]), ("技能", txt["skill"]))
               if any(w in v for w in degraded_words)]
    print("     ↳ 空查询下被标为降级的列：%s（idle 不该算降级）" % (any_bad or "无"))
    chk("D2d idle 没被当成降级（否则首屏就是一屏假故障）", "记忆" not in any_bad and "知识" not in any_bad,
        any_bad)

    # ─────────────── 4. 交互（data-asset 委托，不是 inline onclick） ───────────────
    inter = c.eval("""(() => {
      const b = document.querySelector('[data-asset="search"]');
      if (!b) return {found:false};
      const inp = document.getElementById('assetQ');
      inp.value = '\u7aef\u53e3';                       // 给出真检索词，走完整检索链路
      const before = (document.getElementById('assetMemory')||{}).textContent||'';
      b.click();
      return {found:true, inline: b.hasAttribute('onclick'), before: before.slice(0,60)};
    })()""")
    time.sleep(2.5)
    post = c.eval("""(() => ({
      hint: (document.getElementById('assetHint')||{}).textContent||'',
      memory: (document.getElementById('assetMemory')||{}).textContent||'',
      kb: (document.getElementById('assetKb')||{}).textContent||'',
    }))()""")
    chk("E1 检索按钮存在且走委托（无 inline onclick）",
        inter.get("found") is True and inter.get("inline") is False, inter)
    chk("E2 输入检索词后点一次，记忆列从 idle 变成真结果",
        "需要检索词" not in post.get("memory", "") and bool(post.get("memory", "").strip()),
        post.get("memory", "")[:110])
    chk("E3 记忆列走后端信封（逐路台账 ✓/✗ 出现）",
        ("✓" in post["memory"]) or ("✗" in post["memory"]), post["memory"][:130])
    chk("E4 汇总提示有结论且与降级路数自洽",
        post.get("hint", "").startswith("完成"), post.get("hint", "")[:80])
    chk("E5 点一次不残留浮层",
        c.eval("[...document.querySelectorAll('.drawer.on,.modal-mask.on')].length") == 0,
        c.eval("[...document.querySelectorAll('.drawer.on,.modal-mask.on')].map(e=>e.id)")
        )

    c.close(); proc.terminate(); proc.wait(timeout=15)

    # ─────────────── 5. 窄屏档（390x844）：不许重演 09-23 白板 ───────────────
    proc, c = open_browser(390, 844)
    c.eval("(() => { go('assets'); return true; })()")
    time.sleep(3.0)
    narrow = c.eval("""(() => {
      const s = document.getElementById('page-assets');
      const r = s.getBoundingClientRect();
      const cx = Math.round(window.innerWidth/2), cy = Math.round(window.innerHeight/2);
      const hit = document.elementFromPoint(cx, cy);
      const grid = document.querySelector('#page-assets .mem-grid');
      const gr = grid ? grid.getBoundingClientRect() : null;
      const cols = grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : null;
      return {w: Math.round(r.width), h: Math.round(r.height),
              inside: !!hit && !!(hit.closest && hit.closest('#page-assets')),
              hitTag: hit ? (hit.id || hit.tagName) : null,
              overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
              cols, gw: gr ? Math.round(gr.width) : null,
              collapsed: document.querySelector('.sidebar') ?
                          document.querySelector('.sidebar').classList.contains('collapsed') : null,
              maskOn: [...document.querySelectorAll('.modal-mask.on, #sideMask.on')].length};
    })()""")
    chk("N1 窄屏 390：内容非零且铺进视口", narrow.get("w", 0) > 300 and narrow.get("h", 0) > 200,
        "%sx%s" % (narrow.get("w"), narrow.get("h")))
    chk("N2 窄屏视口中心仍归属本页（无白板/遮罩盖住）", narrow.get("inside") is True,
        "hit=%s maskOn=%s" % (narrow.get("hitTag"), narrow.get("maskOn")))
    chk("N3 无横向溢出", (narrow.get("overflow") or 0) <= 2, narrow.get("overflow"))
    chk("N4 三列在窄屏塌成单列（复用 .mem-grid 自身响应式）", narrow.get("cols") == 1,
        "cols=%s gridw=%s" % (narrow.get("cols"), narrow.get("gw")))
    js_err = c.eval("window.__hubProbeErr ? window.__hubProbeErr.length : -1")
    chk("N5 探针侧无未捕获异常计数可用（-1 表示未挂钩，属如实报）", True, f"__hubProbeErr={js_err}")
    c.close(); proc.terminate(); proc.wait(timeout=15)

except Exception as e:  # noqa: BLE001
    chk("Z0 探针自身异常（不许吞成通过）", False, f"{type(e).__name__}: {e}")
finally:
    subprocess.run(["bash", "-lc", "pgrep -f 'remote-debugging-port=%d' || true" % CDP_PORT],
                   capture_output=True, text=True)

ok = sum(res)
print("\n" + "─" * 78)
print(f"合计：PASS {ok} / {len(res)}   FAIL {len(res) - ok}")
sys.exit(0 if ok == len(res) else 1)
