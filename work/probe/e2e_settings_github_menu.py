#!/usr/bin/env python3
"""设置→GitHub 子菜单真渲染取证（v0.13.42 建、v0.13.43 改手风琴出页）。

与模型页同源的教训：**后端 /api/settings/github 全绿 ≠ 页面能用**
（09-23 事故：后端 20/20 PASS 时页面仍被整块盖住）。每条判据都是可断言的量：
  ① 从左侧「设置」手风琴进「GitHub」子页 ⇒ **零浮层**，且模型页不在 on（不叠加）；
  ② 表单被服务端现值填充（地址非空、来源标签非空、key 一栏只显示掩码）；
  ③ 点「测试连接」⇒ 状态区出现「试连成功」（真打 GitHub，口令预置免 prompt 阻塞）；
  ④ 改落点 + 保存 ⇒ diff 区出「→」行，且**刷新页面后输入框仍是新值**（持久化证据）；
  ⑤ 清除 ⇒ 落点回到内置默认（回落证据）；
  ⑥ 导航离开 ⇒ 零浮层残留（手机没有 ESC：关不掉＝没有关闭）。
两档视口各跑一遍（1440 / 390）。
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests"))
from _cdp_min import CDP, launch_chrome, page_target  # noqa: E402

URL = os.getenv("PROBE_URL", "http://127.0.0.1:3199/")
REMOTE_PORT = int(os.getenv("PROBE_CDP", "9483"))
PASSCODE = os.getenv("PROBE_PASSCODE", "probe-pass-code")
NEW_BASE = os.getenv("PROBE_CLONE_BASE", "/tmp/hub-probe-clones")

READ = """() => {
  const on = ['detailDrawer','skillDocDrawer']
    .filter(id => document.getElementById(id) && document.getElementById(id).classList.contains('on'));
  const val = id => (document.getElementById(id) || {}).value || '';
  const txt = id => (document.getElementById(id) || {}).textContent || '';
  const st = document.getElementById('ghStatus');
  const df = document.getElementById('ghDiff');
  return JSON.stringify({
    overlays_on: on,
    gh_on: !!document.getElementById('page-settings-github').classList.contains('on'),
    model_on: !!document.getElementById('page-settings-model').classList.contains('on'),
    grp_open: (document.querySelector('.nav-acc-head[data-group="settings"]')||{})
                .getAttribute('aria-expanded'),
    api_base: val('ghApiBase'), git_host: val('ghGitHost'), clone_base: val('ghCloneBase'),
    src_labels: [txt('ghApiBaseSrc'), txt('ghGitHostSrc'), txt('ghCloneBaseSrc')]
                  .filter(s => s.trim()).length,
    token_state: txt('ghTokenState'),
    token_input_type: (document.getElementById('ghToken') || {}).type || '',
    status_text: st && st.style.display !== 'none' ? st.textContent : '',
    diff_visible: df && df.style.display !== 'none',
    diff_arrows: df ? (df.textContent.match(/→/g) || []).length : 0,
    vw: window.innerWidth
  });
}"""

# 手风琴真实点击路径：先展开「设置」组，再点子项（走侧栏委托 ⇒ go(page)）
OPEN_SET = ("document.querySelector('.nav-acc-head[data-group=\"settings\"]').click();"
            "document.querySelector('[data-sys=\"%s\"]').click();")


def seed_dialogs(c):
    """prompt/confirm 是**同步阻塞**的：CDP 里弹出来 Runtime.evaluate 就永远不返回
    （09-27 探针首跑即卡死在这里）。预置口令之外，再把两个对话框直接短路。"""
    c.eval("window.confirm = () => true")
    c.eval("window.prompt = () => %r" % PASSCODE)


def run(width, height, tag):
    proc = launch_chrome(URL, REMOTE_PORT, tempfile.mkdtemp(prefix="probe-%s-" % tag), width, height)
    c = None
    out = {"viewport": [width, height]}
    try:
        time.sleep(3)
        c = CDP(page_target(REMOTE_PORT, tries=60))
        c.send("Page.enable")
        c.send("Runtime.enable")
        c.eval("localStorage.setItem('hub.passcode', %r)" % PASSCODE)
        c.send("Page.reload", ignoreCache=True)
        time.sleep(4)
        seed_dialogs(c)
        for _ in range(30):
            if (c.eval("typeof settingsGithubLoad") or "") == "function":
                break
            time.sleep(0.5)
        c.eval(OPEN_SET % "settings-github")
        time.sleep(2.0)
        out["after_switch"] = json.loads(c.eval("(%s)()" % READ))
        # ③ 测试连接（真打 GitHub；口令已预置 ⇒ 不弹 prompt）
        click_eval = repr(c.eval('document.querySelector(\'[data-settings-act="gh-test"]\').click()'))
        for _ in range(30):
            st = json.loads(c.eval("(%s)()" % READ))
            if "试连" in st["status_text"]:
                break
            time.sleep(0.6)
        out["after_test"] = {"status_text": st["status_text"][:600],
                             "overlays_on": st["overlays_on"],
                             "toast": (c.eval("document.getElementById('toast').textContent") or "")[:200],
                             "click_eval": click_eval}
        # ④ 改落点 + 保存
        c.eval("document.getElementById('ghCloneBase').value = %r" % NEW_BASE)
        c.eval('document.querySelector(\'[data-settings-act="gh-apply"]\').click()')
        for _ in range(30):
            st = json.loads(c.eval("(%s)()" % READ))
            if st["diff_visible"] and st["diff_arrows"] > 0:
                break
            time.sleep(0.6)
        out["after_apply"] = {"diff_arrows": st["diff_arrows"], "diff_visible": st["diff_visible"],
                              "clone_base": st["clone_base"], "overlays_on": st["overlays_on"]}
        # 刷新页面 ⇒ 新值必须还在（DB 持久化，不是内存态）
        c.send("Page.reload", ignoreCache=True)
        time.sleep(4)
        seed_dialogs(c)
        for _ in range(30):
            if (c.eval("typeof settingsGithubLoad") or "") == "function":
                break
            time.sleep(0.5)
        st = json.loads(c.eval("(%s)()" % READ))
        out["after_reload"] = {"gh_on": st["gh_on"], "clone_base": st["clone_base"],
                               "api_base": st["api_base"], "sources": st["src_labels"]}
        # ⑤ 清除 ⇒ 回落到内置默认
        c.eval('document.querySelector(\'[data-settings-act="gh-clear"]\').click()')
        time.sleep(2.0)
        st = json.loads(c.eval("(%s)()" % READ))
        out["after_clear"] = {"clone_base": st["clone_base"]}
        # ⑥ 导航离开 ⇒ 零残留
        c.eval("document.getElementById('btnNavHome').click()")
        time.sleep(1.0)
        st = json.loads(c.eval("(%s)()" % READ))
        out["after_nav_away"] = {"gh_on": st["gh_on"], "overlays_on": st["overlays_on"]}
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
    res = {"desktop": run(1440, 900, "gh-wide"), "narrow": run(390, 844, "gh-narrow")}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    for tag, r in res.items():
        a = r["after_switch"]
        assert a["overlays_on"] == [], f"{tag}: 进设置页竟有浮层 {a['overlays_on']}"
        assert a["gh_on"] and not a["model_on"] and a["grp_open"] == "true", \
            f"{tag}: 没进 GitHub 设置页（或两页同时 on）"
        assert a["api_base"].startswith("https://"), f"{tag}: 地址没被现值填充"
        assert a["src_labels"] >= 2, f"{tag}: 来源标签没渲染"
        assert "已设置" in a["token_state"] or "未设置" in a["token_state"], \
            f"{tag}: key 状态没渲染"
        assert a["token_input_type"] == "password", f"{tag}: key 输入框不是 password"
        assert "试连成功" in r["after_test"]["status_text"], \
            f"{tag}: 试连没成功：{r['after_test']['status_text']}"
        assert r["after_test"]["overlays_on"] == [], f"{tag}: 试连后浮层变了"
        assert r["after_apply"]["diff_arrows"] > 0, f"{tag}: 保存后没出 diff"
        assert r["after_apply"]["clone_base"] == NEW_BASE, f"{tag}: 保存后输入框没回填新值"
        assert r["after_reload"]["gh_on"], f"{tag}: 刷新后没回到 GitHub 设置页"
        assert r["after_reload"]["clone_base"] == NEW_BASE, f"{tag}: 刷新后新值丢失（没落库）"
        assert r["after_clear"]["clone_base"] == "/fs/1000/ftp/技术文档", \
            f"{tag}: 清除后没回落：{r['after_clear']['clone_base']}"
        assert not r["after_nav_away"]["gh_on"] and r["after_nav_away"]["overlays_on"] == [], \
            f"{tag}: 导航后仍有残留"
    print("ALL ASSERTIONS PASS")


if __name__ == "__main__":
    main()
