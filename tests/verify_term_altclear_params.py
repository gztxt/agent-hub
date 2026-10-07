#!/usr/bin/env python3
"""备用屏判定「任一参数命中」的边界闸门（v0.13.91，PT-20261008-02）。

v0.13.91 把 `termAltScreenBlock` 的判据从「只看 `p[0]`」改成「任一参数命中」
（`vals.some(...)`），并加了一句命中即 `term.clear()`。**这两处都有回归面**：

  P1 合并的非备用屏 DECSET 必须**照常放行**（`?1000;1002;1003;1006h` = 常见鼠标组合）。
     若 `.some` 误判成命中，会把鼠标模式整条吞掉 —— 比备用屏严重得多的破坏。
  P2 合并里**含** 1049 时必须被吞（`?1003;1049h`）：宁可丢同串的 1003，也不能进备用屏。
     这是**有意的取舍**（见 03-agents-cards.js 注释），本判据把它钉住，防后人"顺手"改回只看 p[0]。
  P3 无参数 / 空串 `\\x1b[?h`、重复 `?1049h` 不得抛异常、不得进备用屏。
  P4 备用屏进入被吞后仍必须**同步清屏**（本次修复的正面判据，防只吞不清回退）。

跑法：python tests/verify_term_altclear_params.py
退出码：0 全绿 / 1 有 FAIL / 2 环境不满足。
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
import _hub_extract as HX  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "static" / "vendor"
CHROME = (os.environ.get("HUB_CHROME") or shutil.which("chromium")
          or shutil.which("chromium-browser") or shutil.which("google-chrome-stable"))


def extract_src():
    src = HX.read_hub()
    const = HX.extract_line(src, "const TERM_ALT_BLOCKED =")
    fn = HX.extract_function(src, "termAltScreenBlock")
    if not const or not fn:
        return None, "抽不到 TERM_ALT_BLOCKED / termAltScreenBlock"
    if os.environ.get("HUB_ALT_CLEAR_DISABLE") == "1":
        # 红向自证：摘掉同步清屏 ⇒ P4 必须转红（P1/P2/P3 不受影响）
        fn = fn.replace("try { term.clear(); } catch (e) {", "try { /*disabled*/ } catch (e) {")
    return const + "\n" + fn, None


BODY = r"""
function mk(){ const t=new Terminal({cols:60,rows:8,allowProposedApi:true});
  t.open(document.getElementById("a")); return t; }
function dump(t){ return {type:t.buffer.active.type,
  mouse:(t.modes&&t.modes.mouseTrackingMode)||null,
  baseY:t.buffer.active.baseY, len:t.buffer.active.length}; }
function w(t,s){ return new Promise(r=>t.write(s,r)); }
(async () => {
  let err = "", out = [];
  try {
    window.term = mk();
    termAltScreenBlock();
    // 基线：灌超过一屏的行，证明清屏真的会动 baseY（P4 需要"有东西可清"）
    await w(term, ("line\r\n").repeat(30));
    const before = dump(term);
    // P1：合并的纯鼠标 DECSET —— 必须放行 + 鼠标生效
    await w(term, "\x1b[?1000;1002;1003;1006h");
    const p1 = dump(term);
    await w(term, "\x1b[?1000;1002;1003;1006l");
    // P2：合并里含 1049 —— 必须被吞（仍在主屏）
    await w(term, "\x1b[?1003;1049h");
    const p2 = dump(term);
    await w(term, "\x1b[?1003l");
    // P3：空参 / 重复 —— 不抛、不进备用屏
    await w(term, "\x1b[?h");
    const p3a = dump(term);
    await w(term, "\x1b[?1049h");
    await w(term, "\x1b[?1049h");
    const p3b = dump(term);
    out = {before:before, p1:p1, p2:p2, p3a:p3a, p3b:p3b};
  } catch (e) { err = String(e && e.message || e); }
  document.getElementById("out").textContent = "RESULT " + JSON.stringify({err:err, out:out});
})();
"""


def main():
    if not CHROME:
        print("  环境不满足：找不到 chromium")
        return 2
    src, why = extract_src()
    if why:
        print(f"  环境不满足：{why}")
        return 2
    html = HX.build_page(src, BODY)
    with tempfile.TemporaryDirectory() as td:
        shutil.copytree(VENDOR, Path(td) / "vendor")
        page = Path(td) / "params.html"
        page.write_text(html, encoding="utf-8")
        try:
            out = subprocess.run(
                [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--disable-dev-shm-usage", "--virtual-time-budget=8000",
                 "--dump-dom", "file://" + str(page)],
                capture_output=True, text=True, timeout=120, errors="replace").stdout
        except Exception as e:
            print(f"  chromium 调用失败：{type(e).__name__}: {e}")
            return 2
    m = re.search(r"RESULT (\{.*?\})</pre>", out, re.S)
    if not m:
        print("  没拿到结果。页面尾部：\n" + out[-400:])
        return 2
    d = json.loads(m.group(1))
    fails = 0

    def check(name, ok, detail=""):
        nonlocal fails
        print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
        if not ok:
            fails += 1

    if d["err"]:
        check("测试页无异常", False, d["err"])
        return 1
    o = d["out"]
    print("  采样：" + json.dumps(o, ensure_ascii=False))
    check("P1 合并鼠标 DECSET 放行且生效（?1000;1002;1003;1006h）",
          o["p1"]["type"] == "normal" and o["p1"]["mouse"] not in (None, "none"),
          f"type={o['p1']['type']} mouse={o['p1']['mouse']}")
    check("P2 含 1049 的合并串被吞（仍在主屏）", o["p2"]["type"] == "normal",
          f"type={o['p2']['type']}")
    check("P3a 空参 ?h 不抛不崩", o["p3a"]["type"] == "normal", f"type={o['p3a']['type']}")
    check("P3b 重复 ?1049h 仍不进备用屏", o["p3b"]["type"] == "normal",
          f"type={o['p3b']['type']}")
    # P4：清屏真的发生了 —— 有内容时 baseY>0，最后一次 1049h 命中后 baseY 归 0
    check("P4 命中备用屏时确实同步清屏（baseY 被清掉）",
          o["before"]["baseY"] > 0 and o["p3b"]["baseY"] == 0,
          f"before.baseY={o['before']['baseY']} after.baseY={o['p3b']['baseY']}")
    print("\n" + ("全部通过 ✅" if fails == 0 else f"失败 {fails} 项 ❌"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())