#!/usr/bin/env python3
"""设置→模型 子菜单真渲染取证（v0.13.41 建、v0.13.43 改手风琴）。

为什么必须跑真渲染而不是只看 /api/settings/models 返回：09-23 那次事故里
「后端全绿 + 前端整块被盖住」同时成立。这里每一条判据都写成**可断言的量**：
  ① 从左侧「设置」手风琴进「模型」子页 ⇒ **零浮层**（抽屉形态已拆，09-23 的正身）；
  ② agent 按钮 ≥6 个（含 2 个置灰的不可写 agent）；
  ③ 点一个可写 agent ⇒ #setModelStep 可见且 #setModelSel 的 option > 3；
  ④ 点「预览变更」⇒ #setDiffBox 可见且含 "→" 的 diff 行，保存按钮解禁；
  ⑤ 点「总览」导航离开 ⇒ 零浮层残留（手机上没有 ESC，导航必须收场）。
两档视口各跑一遍（1440 桌面 / 390 窄屏），窄屏额外量侧栏收起与遮罩关闭。
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests"))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

URL = os.getenv("PROBE_URL", "http://127.0.0.1:3199/")
REMOTE_PORT = int(os.getenv("PROBE_CDP", "9481"))

READ = """() => {
  const on = ['detailDrawer','skillDocDrawer']
    .filter(id => document.getElementById(id) && document.getElementById(id).classList.contains('on'));
  const pg = document.getElementById('page-settings-model');
  const agents = [...document.querySelectorAll('#setAgentList [data-settings-agent]')];
  const sel = document.getElementById('setModelSel');
  const diff = document.getElementById('setDiffBox');
  const step = document.getElementById('setModelStep');
  const sb = document.getElementById('sidebar');
  return JSON.stringify({
    overlays_on: on,
    page_on: !!(pg && pg.classList.contains('on')),
    grp_open: (document.querySelector('.nav-acc-head[data-group="settings"]')||{})
                .getAttribute('aria-expanded'),
    vw: window.innerWidth,
    collapsed: sb.classList.contains('collapsed'),
    mask_on: !!(document.getElementById('sideMask')||{}).classList
             && document.getElementById('sideMask').classList.contains('on'),
    agents: agents.map(b => b.dataset.settingsAgent),
    agents_disabled: agents.filter(b => b.getAttribute('aria-disabled') === 'true')
                           .map(b => b.dataset.settingsAgent),
    step_visible: step && step.style.display !== 'none',
    options: sel ? sel.options.length : -1,
    apply_disabled: !!document.getElementById('setApplyBtn').disabled,
    diff_visible: diff && diff.style.display !== 'none',
    diff_rows: diff ? (diff.textContent.match(/→/g) || []).length : 0
  });
}"""

OPEN_SET = ("document.querySelector('.nav-acc-head[data-group=\"settings\"]').click();"
            "document.querySelector('[data-sys=\"%s\"]').click();")


def run(width, height, tag):
    proc = launch_chrome(URL, REMOTE_PORT, tempfile.mkdtemp(prefix="probe-%s-" % tag), width, height)
    c = None
    out = {"viewport": [width, height]}
    try:
        time.sleep(3)
        c = CDP(page_target(REMOTE_PORT, tries=60))
        c.send("Page.enable")
        c.send("Runtime.enable")
        c.eval("localStorage.clear()")
        c.send("Page.reload", ignoreCache=True)
        time.sleep(4)
        for _ in range(30):
            if (c.eval("(typeof settingsModelsLoad!=='undefined')") or "") == "function":
                break
            time.sleep(0.5)
        # ① 走手风琴真实点击路径（不直接调 go()）
        c.eval(OPEN_SET % "settings-model")
        time.sleep(1.2)
        for _ in range(20):                       # agent 清单异步到位
            st = json.loads(c.eval("(%s)()" % READ))
            if len(st["agents"]) >= 6:
                break
            time.sleep(0.5)
        out["after_open"] = st
        # ③ 点第一个可写的 agent
        c.eval("document.querySelector('#setAgentList [data-settings-agent=\"claude\"]').click()")
        time.sleep(1.5)
        st2 = json.loads(c.eval("(%s)()" % READ))
        out["after_pick_agent"] = {k: st2[k] for k in
                                   ("step_visible", "options", "apply_disabled", "overlays_on")}
        # ④ 预览
        c.eval("document.querySelector('[data-settings-act=\"preview\"]').click()")
        time.sleep(1.5)
        st3 = json.loads(c.eval("(%s)()" % READ))
        out["after_preview"] = {k: st3[k] for k in
                                ("diff_visible", "diff_rows", "apply_disabled", "overlays_on")}
        # ⑤ 导航离开 ⇒ 零浮层残留
        c.eval("document.getElementById('btnNavHome').click()")
        time.sleep(1.0)
        st4 = json.loads(c.eval("(%s)()" % READ))
        out["after_nav_away"] = {k: st4[k] for k in
                                 ("page_on", "overlays_on", "mask_on", "collapsed")}
    finally:
        if c:
            try:
                c.ws.close()
            except Exception:
                pass
        try:
            proc.terminate()
        except Exception:
            pass
    return out


def main():
    res = {"desktop": run(1440, 900, "wide"), "narrow": run(390, 844, "narrow")}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    # 机器判据写在这里，免得"看了截图"就算过
    for tag, r in res.items():
        a = r["after_open"]
        assert a["overlays_on"] == [], f"{tag}: 进设置页竟有浮层 {a['overlays_on']}"
        assert a["page_on"] and a["grp_open"] == "true", f"{tag}: 手风琴没展开/没进模型页"
        assert len(a["agents"]) >= 6, f"{tag}: agent 清单没渲染"
        assert set(a["agents_disabled"]) >= {"codebuddy", "qwenpaw"}, \
            f"{tag}: 不可写 agent 没置灰"
        assert r["after_pick_agent"]["step_visible"] and r["after_pick_agent"]["options"] > 3, \
            f"{tag}: 选 agent 后模型下拉没出来"
        assert r["after_pick_agent"]["apply_disabled"], f"{tag}: 没预览就能保存（预览门失效）"
        assert r["after_preview"]["diff_visible"] and r["after_preview"]["diff_rows"] > 0, \
            f"{tag}: 预览没出 diff"
        assert not r["after_preview"]["apply_disabled"], f"{tag}: 预览后保存仍禁用"
        n = r["after_nav_away"]
        assert not n["page_on"] and n["overlays_on"] == [], f"{tag}: 导航后仍有残留 {n}"
    # 窄屏额外：进页后侧栏必须收起、遮罩必须关（否则正文被压）
    assert res["narrow"]["after_open"]["collapsed"] and not res["narrow"]["after_open"]["mask_on"], \
        "窄屏进设置页后侧栏没收起 / 遮罩没关"
    assert not res["desktop"]["after_open"]["collapsed"], "桌面不该被强制收侧栏"
    print("ALL ASSERTIONS PASS")


if __name__ == "__main__":
    main()
