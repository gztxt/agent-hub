#!/usr/bin/env python3
"""设置→模型「保存」端到端取证（v0.13.44 报障：保存不生效）。

只做 preview 的旧探针（e2e_settings_model_menu.py）证明不了保存链路 —— 它到
预览就停了，而报障点正是在预览之后。这里把**保存这一步**也走完，并把判据写成
可断言的量：

  ① 点保存 ⇒ 出现 ok toast（若出现 err toast 就把文案原样打印出来，那是根因线索）；
  ② 保存后 Hub 侧 agent_models 表真的变（GET /api/settings/models 的 hub_model）；
  ③ 保存后 agent 自己的配置文件真的变（从 /api/settings/models 的 current 读回）；
  ④ 全程零浮层（抽屉已拆，浮层唯一铁律）。

口令从 agent-hub 的 .env 现读（HUB_PASSCODE），经 localStorage 预置，避免 CDP
被 prompt() 阻塞。落笔自带时间戳备份（modelcfg.apply_model 铁律）。
"""
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tests"))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

URL = os.getenv("PROBE_URL", "http://127.0.0.1:3199/")
REMOTE_PORT = int(os.getenv("PROBE_CDP", "9483"))
AGENT = os.getenv("PROBE_AGENT", "codex")
MODEL = os.getenv("PROBE_MODEL", "alibaba/qwen3.8-max")


def passcode():
    for p in (os.path.join(ROOT, ".env"), os.path.expanduser("~/.agent-hub.env")):
        if os.path.exists(p):
            m = re.search(r"^HUB_PASSCODE=(.*)$", open(p, encoding="utf-8").read(), re.M)
            if m:
                return m.group(1).strip()
    return os.getenv("HUB_PASSCODE", "")


READ = """() => {
  const pg = document.getElementById('page-settings-model');
  const toasts = [...document.querySelectorAll('.toast')].map(t => t.className + '|' + t.textContent);
  const sel = document.getElementById('setModelSel');
  return JSON.stringify({
    page_on: !!(pg && pg.classList.contains('on')),
    overlays_on: ['detailDrawer','skillDocDrawer']
      .filter(id => document.getElementById(id) && document.getElementById(id).classList.contains('on')),
    sel_value: sel ? sel.value : null,
    options: sel ? sel.options.length : -1,
    apply_disabled: !!document.getElementById('setApplyBtn').disabled,
    diff_visible: (document.getElementById('setDiffBox')||{style:{}}).style.display !== 'none',
    diff_text: (document.getElementById('setDiffBox')||{textContent:''}).textContent.slice(0, 400),
    pass_value: (document.getElementById('setPasscode')||{}).value,
    agent_meta: (document.getElementById('setAgentMeta')||{}).textContent || '',
    toasts: toasts
  });
}"""

OPEN_SET = ("document.querySelector('.nav-acc-head[data-group=\"settings\"]').click();"
            "document.querySelector('[data-sys=\"settings-model\"]').click();")


def main():
    pc = passcode()
    assert pc, "拿不到 HUB_PASSCODE（.env 缺失？）—— 没有口令保存必然被 503 拒"
    W = int(os.getenv("PROBE_W", "1440"))
    H = int(os.getenv("PROBE_H", "900"))
    proc = launch_chrome(URL, REMOTE_PORT, tempfile.mkdtemp(prefix="probe-apply-"), W, H)
    c = None
    report = {"agent": AGENT, "model": MODEL}
    try:
        time.sleep(3)
        c = CDP(page_target(REMOTE_PORT, tries=60))
        c.send("Page.enable")
        c.send("Runtime.enable")
        # 故意**不预置** hub.passcode：走页内口令框这条新路径（v0.13.44），
        # 同时用来证明「保存不再弹任何 prompt」。
        c.eval("localStorage.clear()")
        c.send("Page.reload", ignoreCache=True)
        time.sleep(4)
        for _ in range(30):
            if (c.eval("(typeof settingsModelsLoad!=='undefined')") or "") == "function":
                break
            time.sleep(0.5)
        c.eval(OPEN_SET)
        time.sleep(1.5)
        for _ in range(20):
            st = json.loads(c.eval("(%s)()" % READ))
            if st["options"] > 3:
                break
            time.sleep(0.5)
        # 选 agent
        c.eval("document.querySelector('#setAgentList [data-settings-agent=%s]').click()"
               % json.dumps(AGENT))
        time.sleep(1.5)
        # 选模型（与现值不同的那个）
        c.eval("(function(){const s=document.getElementById('setModelSel');"
               "s.value=%s;s.dispatchEvent(new Event('change'));return s.value;})()"
               % json.dumps(MODEL))
        time.sleep(0.4)
        report["before_apply"] = json.loads(c.eval("(%s)()" % READ))
        # 预览
        c.eval("document.querySelector('[data-settings-act=\"preview\"]').click()")
        time.sleep(1.5)
        report["after_preview"] = json.loads(c.eval("(%s)()" % READ))
        # 保存：先把 prompt/alert/confirm 钩住（headless 里原生弹窗会阻塞
        # Runtime.evaluate；钩住既能取证「保存是不是靠弹窗要口令」，也不打断流程）
        c.eval("window.__prompt_calls=[];window.__pc=%s;"
               "window.prompt=function(m){window.__prompt_calls.push(String(m||''));return window.__pc;};"
               "window.confirm=function(m){window.__prompt_calls.push('confirm:'+String(m||''));return true;};"
               "window.alert=function(m){window.__prompt_calls.push('alert:'+String(m||''));};"
               % json.dumps(pc))
        # 点之前先量「保存按钮此刻是否真的可点」（窄屏被遮罩/侧栏盖住 ⇒ 点击落空
        # = 用户观感「点了没反应」。elementFromPoint 是静态快照，只作辅证）
        report["apply_btn_hit"] = json.loads(c.eval("""(() => {
          const b = document.querySelector('[data-settings-act="apply"]');
          const r = b.getBoundingClientRect();
          const el = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
          return JSON.stringify({
            rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
            vh: window.innerHeight,
            hit: el ? (el.tagName + (el.id ? '#'+el.id : '') + '.' + el.className) : null,
            is_self: !!(el && el.closest('[data-settings-act="apply"]'))
          });
        })()"""))
        # 填页内口令框（不经过 prompt）
        c.eval("(function(){const i=document.getElementById('setPasscode');"
               "if(!i) return 'NO_INPUT'; i.value=%s; return i.value?'filled':'empty';})()"
               % json.dumps(pc))
        c.eval("window.__applied=null;"
               "(function(){const f=settingsApplyModel;"
               "settingsApplyModel=function(){return f.apply(this,arguments).then(v=>{window.__applied={ok:true};return v;}).catch(e=>{window.__applied={ok:false,msg:String(e&&e.message||e)};throw e;})};})()")
        c.eval("document.querySelector('[data-settings-act=\"apply\"]').click()")
        for _ in range(20):
            time.sleep(0.5)
            if (c.eval("(window.__applied?1:0)") or "0") == "1":
                break
        time.sleep(1.0)
        report["apply_result"] = json.loads(c.eval("JSON.stringify(window.__applied)") or "null")
        report["prompt_calls"] = json.loads(c.eval("JSON.stringify(window.__prompt_calls||[])") or "[]")
        # 服务端真值：页面自己读回去，避免只在 DOM 上打转
        c.eval("window.__srv=null;fetch('/api/settings/models').then(r=>r.json())"
               ".then(d=>{window.__srv=(d.agents||[]).find(x=>x.id===%s)||null;})" % json.dumps(AGENT))
        for _ in range(20):
            time.sleep(0.3)
            if (c.eval("(window.__srv?1:0)") or "0") == "1":
                break
        report["server_state"] = json.loads(c.eval("JSON.stringify(window.__srv)") or "null")
        report["after_apply"] = json.loads(c.eval("(%s)()" % READ))
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
    print(json.dumps(report, ensure_ascii=False, indent=1))
    # ── 机器判据（不靠"看了截图"）──────────────────────────────────────
    aa = report["after_apply"]
    assert report["apply_btn_hit"]["is_self"], "保存按钮此刻被别的东西盖住 ⇒ 点击落空"
    assert aa["overlays_on"] == [], f"保存后竟有浮层残留：{aa['overlays_on']}"
    assert report["prompt_calls"] == [], \
        f"保存不该弹任何 prompt（pop-up 在 WebView 里会被吞）：{report['prompt_calls']}"
    assert aa["diff_visible"], "保存后结果块没显示 ⇒ 用户看不出到底成没成"
    assert "已生效" in aa["diff_text"], f"结果块没有『已生效』回显：{aa['diff_text'][:120]}"
    assert MODEL in aa["agent_meta"], f"状态行没刷新到新模型：{aa['agent_meta']}"
    sv = report["server_state"] or {}
    assert sv.get("hub_model") == MODEL, f"服务端 hub_model 没落上：{sv}"
    assert sv.get("current") == MODEL, f"agent 配置文件没落上：{sv}"
    assert aa["pass_value"] == "", "口令框用完必须清空，不能把明文口令留在页面上"
    print("ALL ASSERTIONS PASS")


if __name__ == "__main__":
    main()
