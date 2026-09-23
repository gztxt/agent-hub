#!/usr/bin/env python3
"""回放闸门取证（L2 探针，需本机 chromium）：
   证明 `termWriteReplay()` 里那套注册真的能挡住终端查询的自动应答。

关键设计（两条，都是为了"探针不许空过"）：
  ① **不抄代码**：直接从 `static/hub.js` 里原样抽出 `TERM_QUERY_OSC` 与
     `function termWriteReplay(raw){…}` 贴进测试页。这样谁把 DCS 那行删了，
     本探针就红；若改成手抄一份示例代码，删真代码探针照样绿（假绿）。
  ② **对照组必须留**：先做一次不设防写入（期望**有**应答包），再做一次经
     termWriteReplay 的写入（期望**无**应答包）。只有"红绿都在同一份产物里"
     才说明是闸门在起作用，而不是解析器被别的东西闷掉了。

测的查询族：
  DECRQPS  `ESC P $ q "q ESC \`  → xterm 答 `ESC P 1$r0"q ESC \`
  DECRQPS  `ESC P $ q r  ESC \`  → xterm 答 `ESC P 1$r1;10r ESC \`
  DSR      `ESC [ ? 6 n`         → xterm 答 `ESC [ 26;1 R`
（DECRQPS 的线序陷阱：中间码只有 `$`。多打一个空格 intermediates 就变成 "$ "，
  匹配不上处理器，会得到"无应答"的**假结论** —— 本探针第一版就踩过，故写在这。）

跑法：
  venv/bin/python tests/verify_replay_gate.py            # 用仓库 static/vendor
  退出码：0 全绿 / 1 有 FAIL / 2 环境不满足（无 chromium 或抽不到代码）
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
HUB = REPO / "static" / "hub.js"
VENDOR = REPO / "static" / "vendor"
CHROME = (os.environ.get("HUB_CHROME") or shutil.which("chromium")
          or shutil.which("chromium-browser") or shutil.which("google-chrome-stable"))

Q1 = "\\x1bP$q\"q\\x1b\\\\"      # 页内 JS 字面量（DECRQPS 保护属性）
Q2 = "\\x1bP$qr\\x1b\\\\"        # DECRQPS 滚动区
DSR = "\\x1b[?6n"                # DSR 光标位置


def extract_gate_source():
    """从 hub.js 原样抽出闸门实现（含它依赖的 TERM_QUERY_OSC）。
       抽取逻辑已提到 tests/_hub_extract.py 与视口探针共用，两份实现迟早不一致。"""
    src = HUB.read_text(encoding="utf-8")
    osc = HX.extract_line(src, "const TERM_QUERY_OSC =")
    if not osc:
        return None, "抽不到 TERM_QUERY_OSC"
    body = HX.extract_function(src, "termWriteReplay")
    if not body:
        return None, "抽不到 termWriteReplay"

    if "registerDcsHandler" not in body:
        print("  注：当前 hub.js 的闸门里没有 DCS 注册（本探针会据此判 FAIL）")
    return osc + "\n" + body, None


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="vendor/xterm.css"></head>
<body><div id="a"></div><div id="b"></div><pre id="out">PENDING</pre>
<script src="vendor/xterm.js"></script>
<script>
/* ==== 以下两段是从 static/hub.js 原样抽出来的真代码 ==== */
__GATE_SRC__
/* ======================= 抽取结束 ======================= */
function w(t, s) { return new Promise(r => t.write(s, r)); }
async function phase(elId, gated) {
  /* 必须挂到 window.term：抽出来的 termWriteReplay 定义在全局作用域，
     它内部的 `term` 按作用域链只认全局，函数内的 const 它看不见 */
  window.term = new Terminal({ cols: 40, rows: 10, allowProposedApi: true });
  term.open(document.getElementById(elId));
  const got = [];
  term.onData(d => got.push(JSON.stringify(d)));
  const payload = __Q1__ + __Q2__ + __DSR__;
  /* 真函数没有第二个参数、也不返回 Promise：它自己 write(raw, cb) 并在 cb 里 dispose */
  if (gated) { termWriteReplay(payload); } else { await w(term, payload); }
  await new Promise(r => setTimeout(r, 250));
  return got.slice();
}
(async () => {
  let err = "", control = [], gated = [];
  try {
    control = await phase("a", false);
    gated = await phase("b", true);
  } catch (e) { err = String(e && e.message || e); }
  document.getElementById("out").textContent = "RESULT " + JSON.stringify({ err, control, gated });
})();
</script></body></html>
"""


def main():
    if not CHROME:
        print("  环境不满足：找不到 chromium（可用 HUB_CHROME=/path 指定）")
        return 2
    src, why = extract_gate_source()
    if why:
        print(f"  环境不满足：{why}（探针依赖真代码，不做手抄副本）")
        return 2
    html = (PAGE.replace("__GATE_SRC__", src)
                .replace("__Q1__", "'%s'" % Q1).replace("__Q2__", "'%s'" % Q2)
                .replace("__DSR__", "'%s'" % DSR))
    with tempfile.TemporaryDirectory() as td:
        shutil.copytree(VENDOR, Path(td) / "vendor")
        page = Path(td) / "gate.html"
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
    print("  对照组（不设防）xterm 回了：")
    for x in d["control"]:
        print("     ", x)
    print("  实验组（经 termWriteReplay）回了：", d["gated"] or "（空）")

    joined = "".join(d["control"])
    check("对照组确实会作答（DECRQPS 两类都答）",
          "P1$r" in joined and joined.count("P1$r") >= 2,
          f"实得 {joined.count('P1$r')} 个 DCS 应答包")
    check("对照组 DSR 也作答（证明 onData 通路活着）",
          "26;1R" in joined or ";1R" in joined)
    check("实验组零应答（闸门真的吞掉了）",
          len(d["gated"]) == 0, f"实得 {len(d['gated'])} 包：{d['gated']}")
    print("\n" + ("全部通过 ✅" if fails == 0 else f"失败 {fails} 项 ❌"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
