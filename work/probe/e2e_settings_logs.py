#!/usr/bin/env python3
"""设置→日志子菜单端到端取证（v0.13.46）。

判据全部是可断言的量，不看截图：

  ① 点侧栏「设置 → 日志」⇒ 正文出页、零浮层（浮层唯一铁律）；
  ② 页内真的渲染出日志行（journald + 操作事件合并）；
  ③ 来源下拉切到「操作事件」⇒ 自动重拉（change 走委托），行里只剩 event；
  ④ 「只看错误」能出结果或给出"该筛选下没有条目"的页内说明（空 ≠ 白屏）；
  ⑤ 全程零 prompt（APP WebView 吞弹窗 ⇒ 端侧"点了没反应"）；
  ⑥ 口令清空再刷新 ⇒ 页内报错 + 焦点回到口令框，不弹窗。

口令从 .env 现读，先走页内口令框（v0.13.45 起的口径：不靠 prompt）。
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
REMOTE_PORT = int(os.getenv("PROBE_CDP", "9495"))


def passcode():
    for p in (os.path.join(ROOT, ".env"), os.path.expanduser("~/.agent-hub.env")):
        if os.path.exists(p):
            m = re.search(r"^HUB_PASSCODE=(.*)$", open(p, encoding="utf-8").read(), re.M)
            if m:
                return m.group(1).strip()
    return os.getenv("HUB_PASSCODE", "")


READ = """() => {
  const pg = document.getElementById('page-settings-logs');
  const rows = [...document.querySelectorAll('#logBody .set-diff-row')];
  return JSON.stringify({
    page_on: !!(pg && pg.classList.contains('on')),
    overlays_on: ['detailDrawer','skillDocDrawer']
      .filter(id => document.getElementById(id) && document.getElementById(id).classList.contains('on')),
    rows: rows.length,
    first_row: rows[0] ? rows[0].textContent.slice(0, 200) : '',
    stats_visible: (document.getElementById('logStats')||{style:{}}).style.display !== 'none',
    stats_text: (document.getElementById('logStats')||{textContent:''}).textContent.slice(0, 400),
    body_text: (document.getElementById('logBody')||{textContent:''}).textContent.slice(0, 300),
    non_rest_rows: /event:(?!rest)/.test((document.getElementById('logBody')||{textContent:''}).textContent),
    rest_rows: (/event:rest/.test((document.getElementById('logBody')||{textContent:''}).textContent)),
    toasts: [...document.querySelectorAll('#toast div')].map(t => t.className + '|' + t.textContent),
    pass_value: (document.getElementById('logPasscode')||{}).value,
    focused: document.activeElement ? document.activeElement.id : null,
    prompt_calls: window.__prompt_calls || []
  });
}"""

OPEN_LOGS = ("document.querySelector('.nav-acc-head[data-group=\"settings\"]').click();"
             "document.querySelector('[data-sys=\"settings-logs\"]').click();")


def main():
    pc = passcode()
    assert pc, "拿不到 HUB_PASSCODE（.env 缺失？）—— 没有口令日志端点必然 503/401"
    W = int(os.getenv("PROBE_W", "1440"))
    H = int(os.getenv("PROBE_H", "900"))
    proc = launch_chrome(URL, REMOTE_PORT, tempfile.mkdtemp(prefix="probe-logs-"), W, H)
    c = None
    report = {"url": URL, "w": W, "h": H}
    try:
        time.sleep(3)
        c = CDP(page_target(REMOTE_PORT, tries=60))
        c.send("Page.enable")
        c.send("Runtime.enable")
        # 钩住原生弹窗：既能取证「是不是靠 prompt 要口令」，也避免 headless 被阻塞
        c.eval("window.__prompt_calls=[];"
               "window.prompt=function(m){window.__prompt_calls.push(String(m||''));return '';};"
               "window.confirm=function(m){window.__prompt_calls.push('confirm:'+String(m||''));return true;};"
               "window.alert=function(m){window.__prompt_calls.push('alert:'+String(m||''));};")
        c.eval("localStorage.clear()")
        c.send("Page.reload", ignoreCache=True)
        time.sleep(4)
        for _ in range(30):
            if (c.eval("(typeof settingsLogsLoad!=='undefined')") or "") == "function":
                break
            time.sleep(0.5)
        c.eval(OPEN_LOGS)
        time.sleep(1.0)
        # 填页内口令框后刷新（走真实交互，不是预置 localStorage）
        c.eval("(function(){const i=document.getElementById('logPasscode');"
               "if(i) i.value=%s; return i?'ok':'NO_INPUT';})()" % json.dumps(pc))
        c.eval("document.querySelector('[data-settings-act=\"log-refresh\"]').click()")
        for _ in range(30):
            time.sleep(0.5)
            st = json.loads(c.eval("(%s)()" % READ))
            if st["rows"] > 0:
                break
        report["after_load"] = st

        # 切「来源」= 操作事件（change 事件 ⇒ 委托里自动重拉）
        c.eval("(function(){const s=document.getElementById('logSource');"
               "s.value='event';s.dispatchEvent(new Event('change', {bubbles: true}));return s.value;})()")
        time.sleep(2.5)
        for _ in range(20):
            st = json.loads(c.eval("(%s)()" % READ))
            if "操作事件" in st["stats_text"]:
                break
            time.sleep(0.5)
        report["after_event"] = st

        # v0.13.47：系统菜单里不许再有「运行日志」入口（页已删除，能力并到本页）
        report["nav"] = json.loads(c.eval(
            "JSON.stringify({items: [...document.querySelectorAll('[data-sys]')]"
            ".map(b => b.getAttribute('data-sys'))})"))

        # 切「运行日志（三中心检索 · rest）」——原系统菜单那一份，必须只出 rest
        c.eval("(function(){const s=document.getElementById('logSource');"
               "s.value='rest';s.dispatchEvent(new Event('change', {bubbles: true}));return s.value;})()")
        time.sleep(2.5)
        for _ in range(20):
            st = json.loads(c.eval("(%s)()" % READ))
            if st["rows"] > 0 or "没有条目" in st["body_text"]:
                break
            time.sleep(0.5)
        report["after_rest"] = st

        # 切「只看错误」
        c.eval("(function(){const s=document.getElementById('logSource');"
               "s.value='error';s.dispatchEvent(new Event('change', {bubbles: true}));return s.value;})()")
        time.sleep(2.5)
        for _ in range(20):
            st = json.loads(c.eval("(%s)()" % READ))
            if "ERR" in st["body_text"] or "没有条目" in st["body_text"]:
                break
            time.sleep(0.5)
        report["after_error"] = st

        # 复制（有结果时）；headless 无剪贴板权限也必须有 toast 反馈，不能静默
        c.eval("(function(){const s=document.getElementById('logSource');"
               "s.value='all';s.dispatchEvent(new Event('change', {bubbles: true}));})()")
        time.sleep(2.5)
        c.eval("document.querySelector('[data-settings-act=\"log-copy\"]').click()")
        time.sleep(1.0)
        report["after_copy"] = json.loads(c.eval("(%s)()" % READ))

        # 场景二：口令清空再刷新 ⇒ 页内报错 + 焦点回口令框，且不弹 prompt
        c.eval("localStorage.clear();window.__prompt_calls=[];"
               "(function(){const i=document.getElementById('logPasscode'); if(i) i.value='';})();")
        c.eval("document.querySelector('[data-settings-act=\"log-refresh\"]').click()")
        time.sleep(2.5)
        report["no_passcode"] = json.loads(c.eval("(%s)()" % READ))
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
    # ── 机器判据 ────────────────────────────────────────────────────
    al = report["after_load"]
    assert al["page_on"], "点了「日志」没进正文页"
    assert al["overlays_on"] == [], f"竟有浮层残留：{al['overlays_on']}"
    assert al["prompt_calls"] == [], f"不该弹任何 prompt：{al['prompt_calls']}"
    assert al["rows"] > 0, f"日志页一行都没渲染：{al['body_text'][:160]}"
    assert al["stats_visible"] and al["stats_text"], "统计行没出 ⇒ 用户分不清是没数据还是挂了"
    assert "runlog" not in report["nav"]["items"], \
        f"系统菜单里还留着运行日志入口（页面已删）：{report['nav']['items']}"
    ae = report["after_event"]
    assert "操作事件" in ae["stats_text"], f"切来源后统计没跟着变：{ae['stats_text'][:160]}"
    assert ae["rows"] > 0, "切到操作事件后没条目"
    ar = report["after_rest"]
    assert ar["rows"] > 0, f"切到运行日志（rest）来源后没条目：{ar['body_text'][:160]}"
    assert ar["rest_rows"], "rest 视图里看不到 event:rest 行 ⇒ 来源没生效"
    assert not ar["non_rest_rows"], "rest 视图里混进了别的 source ⇒ 过滤没接住"
    aerr = report["after_error"]
    assert ("ERR" in aerr["body_text"]) or ("没有条目" in aerr["body_text"]), \
        f"只看错误既没条目也没页内说明（白屏）：{aerr['body_text'][:160]}"
    ac = report["after_copy"]
    assert ac["toasts"], "点复制后没有任何反馈（静默失败 = 用户以为坏了）"
    assert ac["prompt_calls"] == [], f"复制不该弹窗：{ac['prompt_calls']}"
    np_ = report["no_passcode"]
    assert np_["prompt_calls"] == [], f"缺口令时不许弹 prompt：{np_['prompt_calls']}"
    assert "读取失败" in np_["stats_text"], f"缺口令时要页内报错：{np_['stats_text'][:160]}"
    assert np_["focused"] == "logPasscode", f"缺口令时焦点要回到口令框，实际 {np_['focused']}"
    print("ALL ASSERTIONS PASS")


if __name__ == "__main__":
    main()
