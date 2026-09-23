#!/usr/bin/env python3
"""P2-8 取证：白屏自愈的空行判定必须按**视口**行，不是按缓冲区绝对行。
   需要本机 chromium（不需要服务）。

症状：画面明明全白，自愈却不发那对 resize ⇒ 用户一直对着白屏。
根因：`term.buffer.active.getLine(0)` 是**绝对坐标**（scrollback 最老一行）。
      旧写法 `for i in 0..rows-1: getLine(i)` 在有历史时读到的是早已滚出屏幕的旧行，
      与用户此刻看到的画面无关 ⇒ blank 偏低 ⇒ 判据 `blank*3 >= rows*2` 不成立 ⇒ 误抑制。

探针设计（三场景，关键是新旧两种口径**在同一份产物里同场对比**）：
  A 有 scrollback 且视口全白  → 新口径 blank=rows（会自愈）；旧口径 blank≈0（被误抑制）
  B 视口有内容               → 新口径 blank=0（不乱打扰 pty）
  C 全新空终端（无 scrollback）→ 新口径 blank=rows（与改前一致，防回归）
旧口径在页内以 oldBlank() 重现（它是**对照组**，不是被测实现；被测实现由
_hub_extract 从 static/hub.js 原样抽出，删掉真代码里的那行本探针就会变红）。

跑法：venv/bin/python tests/verify_term_heal_viewport.py     退出码 0/1/2 同其他探针
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
import _hub_extract as HX                                    # noqa: E402

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "static" / "vendor"
CHROME = (os.environ.get("HUB_CHROME") or shutil.which("chromium")
          or shutil.which("chromium-browser") or shutil.which("google-chrome-stable"))

BODY = r"""
/* 对照组：改前的绝对行口径，原样重现，仅用于证明差异真实存在 */
function oldBlank(t) {
  const b = t.buffer.active; let blank = 0;
  for (let i = 0; i < t.rows; i++) { const l = b.getLine(i); if (!l || !l.translateToString(true).trim()) blank++; }
  return blank;
}
function w(t, s) { return new Promise(r => t.write(s, r)); }
function mk(elId) {
  const t = new Terminal({ cols: 40, rows: 10, scrollback: 5000, allowProposedApi: true });
  t.open(document.getElementById(elId));
  return t;
}
(async () => {
  const out = {}; let err = '';
  try {
    /* A：先灌 60 行历史（产生 scrollback），再 ED2 清屏（视口清空、历史留着） */
    const a = mk('a');
    await w(a, Array.from({length: 60}, (_, i) => 'SCROLLBACK-' + i + ' 有内容的历史行').join('\r\n'));
    await w(a, '\x1b[2J\x1b[H');
    await new Promise(r => setTimeout(r, 120));
    out.A = { new: termViewportBlankRows(a), old: oldBlank(a), rows: a.rows,
              viewportY: a.buffer.active.viewportY, len: a.buffer.active.length };
    /* B：整屏有内容 ⇒ 不该自愈 */
    const b = mk('b');
    await w(b, Array.from({length: 12}, (_, i) => 'FULL-' + i + ' 铺满整屏的画面').join('\r\n'));
    await new Promise(r => setTimeout(r, 120));
    out.B = { new: termViewportBlankRows(b), old: oldBlank(b), rows: b.rows,
              viewportY: b.buffer.active.viewporty === undefined ? b.buffer.active.viewportY : -1 };
    /* D：只有一行 shell 提示符（真实常见态）。判据是"≥2/3 行为空才算白屏"，
       所以 10 行里 1 行有内容 ⇒ blank=9 ⇒ 会自愈。这不是 bug：
       termHealBlank() 只在每次重连的回放后调一次（不是轮询），代价是两帧 resize；
       而改前的绝对行口径在"有 scrollback"时会让它**永不触发**，白块长挂。 */
    const d = mk('d');
    await w(d, 'gztxt@zzst:~$ \r\n');
    await new Promise(r => setTimeout(r, 120));
    out.D = { new: termViewportBlankRows(d), rows: d.rows,
              wouldHeal: termViewportBlankRows(d) * 3 >= d.rows * 2 };
    /* C：全新空终端（无 scrollback，视口即全白） */
    const c = mk('c');
    await new Promise(r => setTimeout(r, 120));
    out.C = { new: termViewportBlankRows(c), old: oldBlank(c), rows: c.rows,
              viewportY: c.buffer.active.viewportY };
  } catch (e) { err = String((e && e.message) || e); }
  document.getElementById('out').textContent = 'RESULT ' + JSON.stringify({ err, out });
})();
"""


def main():
    if not CHROME:
        print("  环境不满足：找不到 chromium（HUB_CHROME=/path 可指定）")
        return 2
    src = HX.read_hub()
    fn = HX.extract_function(src, "termViewportBlankRows")
    if not fn:
        print("  环境不满足：从 hub.js 抽不到 termViewportBlankRows（函数被改名/删除？）")
        return 2
    html = HX.build_page(fn, BODY)
    with tempfile.TemporaryDirectory() as td:
        shutil.copytree(VENDOR, Path(td) / "vendor")
        page = Path(td) / "heal.html"
        page.write_text(html, encoding="utf-8")
        cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--disable-dev-shm-usage", "--virtual-time-budget=8000",
               "--dump-dom", "file://" + str(page)]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                 errors="replace").stdout
        except Exception as e:
            print(f"  chromium 调用失败：{type(e).__name__}: {e}")
            return 2
    m = re.search(r"RESULT (\{.*\})</pre>", out, re.S)
    if not m:
        print("  没拿到结果，页面尾部：\n" + out[-500:])
        return 2
    d = json.loads(m.group(1))
    if d["err"]:
        print("  页内异常：", d["err"])
        return 1
    o = d["out"]
    fails = 0

    def check(name, ok, detail=""):
        nonlocal fails
        print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
        if not ok:
            fails += 1

    for k in ("A", "B", "C", "D"):
        print("  %s: %s" % (k, json.dumps(o[k], ensure_ascii=False)))
    A, B, C, D = o["A"], o["B"], o["C"], o["D"]
    check("A 场景前提成立：确有 scrollback 且视口已不在第 0 行",
          A["viewportY"] > 0 and A["len"] > A["rows"], f"viewportY={A['viewportY']} len={A['len']}")
    check("A 新口径判全白 ⇒ 会自愈", A["new"] == A["rows"], f"blank={A['new']}/{A['rows']}")
    check("A 旧口径判为有内容 ⇒ 这就是被误抑制的现场（红绿对照）",
          A["old"] * 3 < A["rows"] * 2, f"旧口径 blank={A['old']}（若沿用旧写法自愈根本不会触发）")
    check("B 整屏有内容时新口径为 0（不乱发 resize 打扰 pty）",
          B["new"] == 0, f"blank={B['new']}")
    check("D 单行提示符按判据会触发自愈（记录语义：每次重连仅一次，代价两帧 resize）",
          D["wouldHeal"] is True, f"blank={D['new']}/{D['rows']} ⇒ 自愈={D['wouldHeal']}")
    check("C 无 scrollback 时新口径与旧口径一致（防回归）",
          C["new"] == C["rows"] and C["old"] == C["rows"] and C["viewportY"] == 0,
          f"new={C['new']} old={C['old']} viewportY={C['viewportY']}")
    print("\n" + ("全部通过 ✅" if fails == 0 else f"失败 {fails} 项 ❌"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
