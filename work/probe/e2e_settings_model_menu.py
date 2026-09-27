#!/usr/bin/env python3
"""设置→模型子菜单真渲染取证（v0.13.41）。

为什么必须跑真渲染而不是只看 /api/settings/models 返回：09-23 那次事故里
「后端全绿 + 前端整块被盖住」同时成立。这里每一条判据都写成**可断言的量**：
  ① 打开设置后浮层**只有一个**（settingsDrawer.on 且 detailDrawer/skillDocDrawer 都不 on）；
  ② 「模型」子页默认选中，agent 按钮 ≥6 个（含 2 个置灰的不可写 agent）；
  ③ 点一个可写 agent ⇒ #setModelStep 可见且 #setModelSel 的 option > 3；
  ④ 点「预览变更」⇒ #setDiffBox 可见且含 "→" 的 diff 行；
  ⑤ 点遮罩 ⇒ 抽屉关闭（手机没有 ESC：点空白关不掉＝没有关闭）。
两档视口各跑一遍（1440 桌面 / 390 窄屏），窄屏额外量抽屉宽度占比。
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
  const on = ['detailDrawer','settingsDrawer','skillDocDrawer']
    .filter(id => document.getElementById(id).classList.contains('on'));
  const drawer = document.getElementById('settingsDrawer');
  const agents = [...document.querySelectorAll('#setAgentList [data-settings-agent]')];
  const sel = document.getElementById('setModelSel');
  const diff = document.getElementById('setDiffBox');
  const step = document.getElementById('setModelStep');
  return JSON.stringify({
    overlays_on: on,
    drawer_on: drawer.classList.contains('on'),
    drawer_w: Math.round(drawer.getBoundingClientRect().width),
    vw: window.innerWidth,
    tab_on: (document.querySelector('.set-tab.on') || {}).textContent || '',
    agents: agents.map(b => b.dataset.settingsAgent),
    agents_disabled: agents.filter(b => b.getAttribute('aria-disabled') === 'true')
                           .map(b => b.dataset.settingsAgent),
    step_visible: step && step.style.display !== 'none',
    options: sel ? sel.options.length : -1,
    apply_disabled: !!document.getElementById('setApplyBtn').disabled,
    diff_visible: diff && diff.style.display !== 'none',
    diff_rows: diff ? (diff.textContent.match(/→/g) || []).length : 0,
    mask_on: !!document.querySelector('.modal-mask.on')
  });
}"""


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
            if (c.eval("(typeof settingsTab!=='undefined')") or "") == "true":
                break
            time.sleep(0.5)
        # ① 打开设置（走侧栏按钮的真实点击路径，不直接调 openSettings）
        c.eval("document.querySelector('[data-settings]').click()")
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
        # ⑤ 逃生路径：遮罩开着就点遮罩（手机没有 ESC）；桌面档抽屉按常驻面板处理，走关闭按钮
        c.eval("const m=document.getElementById('sideMask');"
               "if(m && m.classList.contains('on')) m.click();"
               "else { const b=document.querySelector('#settingsDrawer .btn.ghost.sm');"
               "if(b) b.click(); }")
        time.sleep(1.0)
        st4 = json.loads(c.eval("(%s)()" % READ))
        out["after_mask_click"] = {k: st4[k] for k in ("drawer_on", "overlays_on", "mask_on")}
        out["drawer_width_ratio"] = round(st["drawer_w"] / max(1, st["vw"]), 3)
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
        assert r["after_open"]["overlays_on"] == ["settingsDrawer"], f"{tag}: 浮层不唯一"
        assert r["after_open"]["tab_on"].strip() == "模型", f"{tag}: 默认子页不是「模型」"
        assert len(r["after_open"]["agents"]) >= 6, f"{tag}: agent 清单没渲染"
        assert set(r["after_open"]["agents_disabled"]) >= {"codebuddy", "qwenpaw"}, \
            f"{tag}: 不可写 agent 没置灰"
        assert r["after_pick_agent"]["step_visible"] and r["after_pick_agent"]["options"] > 3, \
            f"{tag}: 选 agent 后模型下拉没出来"
        assert r["after_pick_agent"]["apply_disabled"], f"{tag}: 没预览就能保存（预览门失效）"
        assert r["after_preview"]["diff_visible"] and r["after_preview"]["diff_rows"] > 0, \
            f"{tag}: 预览没出 diff"
        assert not r["after_preview"]["apply_disabled"], f"{tag}: 预览后保存仍禁用"
        assert r["after_mask_click"]["drawer_on"] is False, f"{tag}: 点遮罩关不掉抽屉"
        assert r["after_mask_click"]["overlays_on"] == [], f"{tag}: 关闭后仍有浮层残留"
    print("ALL ASSERTIONS PASS")


if __name__ == "__main__":
    main()
