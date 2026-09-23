#!/usr/bin/env python3
"""P2-10/11 取证：真 DOM、真事件对象下，全局快捷键守卫认不认得出「正在输入」。
   需要本机 chromium（不需要服务）。

为什么还要一层浏览器探针：tests/test_term_focus_policy.py 守的是 hub.js 的**位置**
（守卫有没有排在 `/` 与 Ctrl+K 之前），但它证明不了 `e.target.closest('.xterm')`
在真浏览器里真的能命中 —— xterm 的键入发生在它自己造的隐藏 textarea 上，
事件能不能冒泡到 document、target 是谁，只有真引擎说了算。

三段断言，含一段**对照组**（证明旧写法确实在劫持，不是假设）：
  1 对照：在 .xterm 内的元素上派发冒泡 keydown，document 层监听器**确实收到**，
          且 e.target 落在 .xterm 里 ⇒ 改前那句 `e.preventDefault(); si.focus()` 生效，
          终端里敲 `/` 就是被这么抢走的
  2 实验：keyTargetIsEditing 对 input / textarea / select / contenteditable /
          .xterm 及其子孙 全部为 true，对普通 div 与 body 为 false
  3 真代码：termFocusWanted 真值表 —— 只有 opts.user 为真才抢焦点，
          {reconnect:true}（自动重连）与 undefined（自动挂载）都不抢 ⇒ 手机不弹软键盘
被测函数由 tests/_hub_extract.py 从 static/hub.js 原样抽取，手抄不算数。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hub_extract as HX                                      # noqa: E402

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "static" / "vendor"
CHROME = (os.environ.get("HUB_CHROME") or shutil.which("chromium")
          or shutil.which("chromium-browser") or shutil.which("google-chrome-stable"))

WANT = [("fn", "keyTargetIsEditing"), ("fn", "termFocusWanted")]

BODY = r"""
function mk(tag, attrs, html) {
  const e = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, v));
  if (html !== undefined) e.innerHTML = html;
  document.body.appendChild(e);
  return e;
}
/* 用真事件：只有真 KeyboardEvent 才有 closest()/isContentEditable 这些浏览器语义 */
function ask(el, key) {
  return new Promise(res => {
    function onDoc(e) {
      document.removeEventListener('keydown', onDoc);
      res({ got: true, targetInXterm: !!(e.target && e.target.closest && e.target.closest('.xterm')),
            editing: keyTargetIsEditing(e) });
    }
    document.addEventListener('keydown', onDoc);
    el.dispatchEvent(new KeyboardEvent('keydown', { key: key, bubbles: true, cancelable: true }));
  });
}
(async () => {
  const out = { els: {} };
  const termBox = mk('div', { class: 'xterm' }, '<div class="xterm-screen"><textarea class="xterm-helper-textarea"></textarea></div>');
  const cases = [
    ['xterm 外壳', termBox],
    ['xterm 内层 div', termBox.querySelector('.xterm-screen')],
    ['xterm 隐藏 textarea', termBox.querySelector('textarea')],
    ['普通 input', mk('input', { type: 'text' })],
    ['navSearch', mk('input', { id: 'navSearch', type: 'search' })],
    ['textarea', mk('textarea', {})],
    ['select', mk('select', {})],
    ['contenteditable', mk('div', { contenteditable: 'true' })],
    ['普通 div', mk('div', { class: 'card' })],
    ['body', document.body]
  ];
  for (const [name, el] of cases) out.els[name] = await ask(el, '/');

  /* 对照：改前没有任何守卫，这条事件到 document 就会被 preventDefault + 抢焦点 */
  const one = out.els['xterm 隐藏 textarea'];
  out.control = { docSaw: one.got, targetInXterm: one.targetInXterm };

  out.focus = {
    user: termFocusWanted({ user: true }),
    reconnect: termFocusWanted({ reconnect: true }),
    empty: termFocusWanted({}),
    none: termFocusWanted(undefined)
  };
  document.getElementById('out').textContent = 'RESULT ' + JSON.stringify(out);
})();
"""


def main():
    if not CHROME:
        print("  环境不满足：找不到 chromium（HUB_CHROME=/path 可指定）")
        return 2
    src = HX.read_hub()
    parts, miss = [], []
    for kind, what in WANT:
        got = HX.extract_function(src, what)
        (parts if got else miss).append(got or what)
    if miss:
        print("  环境不满足：hub.js 里抽不到 " + ", ".join(miss))
        return 2
    html = HX.build_page("\n".join(parts), BODY)
    with tempfile.TemporaryDirectory() as td:
        shutil.copytree(VENDOR, Path(td) / "vendor")
        page = Path(td) / "keys.html"
        page.write_text(html, encoding="utf-8")
        cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--disable-dev-shm-usage", "--virtual-time-budget=6000",
               "--dump-dom", "file://" + str(page)]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                 errors="replace").stdout
        except Exception as e:
            print(f"  chromium 调用失败：{type(e).__name__}: {e}")
            return 2
    m = re.search(r"RESULT (\{.*\})</pre>", out, re.S)
    if not m:
        print("  没拿到结果，页面尾部：\n" + out[-800:])
        return 2
    d = json.loads(m.group(1))
    fails = 0

    def check(name, ok, detail=""):
        nonlocal fails
        print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
        if not ok:
            fails += 1

    print("  逐元素实测 keyTargetIsEditing：")
    for k, v in d["els"].items():
        print("    %-18s got=%s targetInXterm=%-5s editing=%s" %
              (k, v["got"], v["targetInXterm"], v["editing"]))
    c = d["control"]
    check("对照：终端里的 keydown 确实冒泡到 document 且 target 在 .xterm 内（旧写法必劫持）",
          c["docSaw"] and c["targetInXterm"])
    should_true = ["xterm 外壳", "xterm 内层 div", "xterm 隐藏 textarea", "普通 input",
                   "navSearch", "textarea", "select", "contenteditable"]
    bad_t = [k for k in should_true if d["els"][k]["editing"] is not True]
    check("守卫认得出全部输入位（含 contenteditable 与 .xterm 三层子孙）", not bad_t, ",".join(bad_t) or "8/8")
    bad_f = [k for k in ("普通 div", "body") if d["els"][k]["editing"] is not False]
    check("守卫不误伤普通元素（否则整页快捷键失效）", not bad_f, ",".join(bad_f) or "2/2")
    f = d["focus"]
    check("termFocusWanted 真值表：只有 user:true 抢焦点",
          f["user"] is True and f["reconnect"] is False and f["empty"] is False and f["none"] is False,
          json.dumps(f))
    print("\n" + ("全部通过 ✅" if fails == 0 else f"失败 {fails} 项 ❌"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
