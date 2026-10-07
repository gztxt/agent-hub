#!/usr/bin/env python3
"""吞备用屏时「同步清屏」闸门（v0.13.91，PT-20261008-02）。

用户报障原句：「agent-hub 的 jcode 启动嵌入式终端时会带入乱码」（2026-10-08）。
根因（字节级 + 渲染双重取证，见 03-agents-cards.js 的 ★ 注释）：本仓只吞
`?1049h` **进入**、从不切备用屏缓冲，于是 TUI 在**旧画面**上按绝对坐标作画 ——
hub 头部（`jcode · 终端` / `提示：点「新会话」…`）与 jcode 的 `Connecting to server...`
留在第 0~2 行，和 TUI 首帧叠在一起。真终端里这些会被备用屏的空白画布盖掉。

做法：吞掉备用屏进入的**同一刻**同步 `term.clear()`（等价于那块空白画布）。
必须是同步调用：`term.write('\\x1b[2J')` 会被 xterm 的异步 write 队列排到本帧之后，
把刚画好的 TUI 一起抹掉（本探针的 V3 档就是那个反例）。

判据（每条可断言，不以「我看了截图」交差）：
  A1 备用屏进入被吞：仍在主屏（normal），从不进 alternate
  A2 旧画面被清：hub 头部那两行、jcode 的 `Connecting to server...` 不再出现
  A3 TUI 自己没被误伤：同帧内、清屏之后写下的 TUI 文本仍在
  A4 红向自证：把 `term.clear()` 摘掉（HUB_ALT_CLEAR_DISABLE=1）后 A2 立刻转红

跑法（本机 chromium）：
  venv/bin/python tests/verify_term_altclear.py          # 期望全绿
  HUB_ALT_CLEAR_DISABLE=1 venv/bin/python tests/verify_term_altclear.py   # 期望 A2 红
退出码：0 全绿 / 1 有 FAIL / 2 环境不满足（无 chromium 或抽不到真代码）
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

#: 模拟 hub termConnect 写在 jcode 之前的头部（真串见 04-terminal-ws.js）
HEADER = ("\\x1b[90mjcode \\u00b7 \\u7ec8\\u7aef\\x1b[0m\\r\\n"
          "\\x1b[90m\\u63d0\\u793a\\uff1a\\u70b9\\u300c\\u65b0\\u4f1a\\u8bdd\\u300d\\u62c9\\u8d77 "
          "jcode \\u7684\\u539f\\u751f\\u7ec8\\u7aef\\x1b[0m\\r\\n")

#: **真实回放前缀**（245B，从真 hub 影子实例的 ring 里抓的原始字节，非手抄）：
#: 这是客户端下发 resize 之前、服务端 `send_bytes(ring)` 回放出去的那一段 ——
#: `OSC11 查询 + DA1 查询 + "Connecting to server..." + ?1049h + 一串 DECSET/同步块`。
#: 用户报障的「乱码」就发生在这个窗口（jcode 要等客户端 resize 后才会发 `\x1b[2J` 整屏重画）。
#: 抓法：POST /api/term/sessions → WS 连上后先收首帧（= ring），取到第一个 `\x1b[2J` 之前的部分。
REAL_PREFIX_HEX = (
    "1b5d31313b3f071b5b63436f6e6e656374696e6720746f207365727665722e2e2e0d0a1b5b3f31303439"
    "681b5b3e37751b5b3f32303034681b5b3f31303034681b5b3f31303030681b5b3f31303032681b5b3f31"
    "303033681b5b3f31303135681b5b3f31303036681b5d303b6a636f6465071b5b3f32303236681b5b3339"
    "6d1b5b34396d1b5b35396d1b5b306d1b5b3f32356c1b5b3f323032366c1b5b3f32303236681b5b33396d"
    "1b5b34396d1b5b35396d1b5b306d1b5b3f32356c1b5b3f323032366c1b5b3f32303236681b5b33396d1b"
    "5b34396d1b5b35396d1b5b306d1b5b3f32356c1b5b3f323032366c1b5b3f3230323668")
#: 真实前缀之后 jcode 画的 TUI 首帧形状（取关键结构：绝对定位 + CJK 逐列，防宽字符误判）。
#: 真机上这段在客户端 resize 之后到达，本探针把它接在同一帧里，A3 才有东西可断言。
TUI_TAIL = ("\\x1b[6;2H\\x1b[1mjcode\\x1b[22m \\u00b7 client"
            "\\x1b[7;2Hserver: Camp"
            "\\x1b[18;2H/fs/1000/ftp/\\xe6\\x8a\\x80\\x1b[18;17H\\xe6\\x9c\\xaf"
            "\\x1b[18;19H\\xe6\\x96\\x87\\x1b[18;21H\\xe6\\xa1\\xa3")


def _js_escape(raw: bytes) -> str:
    """把原始字节转成可嵌进 JS 双引号字符串的 \\xNN 转义。"""
    return "".join("\\x%02x" % b for b in raw)


#: 喂给页面的一整帧 = 真实回放前缀 + TUI 首帧。`?1049h` 在真实前缀里，
#: 修复前「吞而不清」⇒ "Connecting to server..." 与头部留在屏上；修复后同步清屏。
FRAME = _js_escape(bytes.fromhex(REAL_PREFIX_HEX)) + TUI_TAIL


def extract_src():
    """从 static/hub.js 原样抽出常量与真函数（不手抄，删真代码本探针必红）。"""
    src = HX.read_hub()
    const = HX.extract_line(src, "const TERM_ALT_BLOCKED =")
    fn = HX.extract_function(src, "termAltScreenBlock")
    # v0.13.92：函数新增「有选区吞鼠标跟踪 DECSET」分支，引用 02 片的 TERM_MOUSE_MODES，
    # 抽真函数时要一并注入（否则独立页 ReferenceError、拿不到结果）。
    mouse = HX.extract_line(src, "const TERM_MOUSE_MODES =")
    if not const or not fn:
        return None, "抽不到 TERM_ALT_BLOCKED / termAltScreenBlock"
    if os.environ.get("HUB_ALT_CLEAR_DISABLE") == "1":
        # 红向自证：把同步清屏那一句摘掉，其余原样
        fn = fn.replace("try { term.clear(); } catch (e) {", "try { /*disabled*/ } catch (e) {")
    return const + "\n" + (mouse or "") + "\n" + fn, None


BODY = """
const HEADER = "__HEADER__";
const FRAME  = "__FRAME__";
const HDRMARK = "jcode \\u00b7 \\u7ec8\\u7aef";      // hub 头部第 0 行
const CONNMARK = "Connecting to server...";
const TUIMARK = "jcode \\u00b7 client";               // TUI 自己的首行
function w(t, s) { return new Promise(r => t.write(s, r)); }
(async () => {
  let err = "";
  let res = null;
  try {
    window.term = new Terminal({ cols: 100, rows: 26, allowProposedApi: true });
    term.open(document.getElementById("a"));
    termAltScreenBlock();                 // 真函数：注册吞 + 清屏
    await w(term, HEADER);                // 1) hub 头部（会被 TUI 启动清掉）
    await w(term, FRAME);                 // 2) jcode 首帧（内含 ?1049h）
    await new Promise(r => setTimeout(r, 300));
    const b = term.buffer.active;
    const lines = [];
    for (let i = 0; i < b.length; i++) { const l = b.getLine(i); lines.push(l ? l.translateToString(true) : ""); }
    const text = lines.join("\\n");
    res = {
      type: b.type,
      hasHdr: text.indexOf(HDRMARK) >= 0,
      hasConn: text.indexOf(CONNMARK) >= 0,
      hasTui: text.indexOf(TUIMARK) >= 0,
      head: lines.slice(0, 4),
    };
  } catch (e) { err = String(e && e.message || e); }
  document.getElementById("out").textContent = "RESULT " + JSON.stringify({ err: err, res: res });
})();
"""


def main():
    if not CHROME:
        print("  环境不满足：找不到 chromium（可用 HUB_CHROME=/path 指定）")
        return 2
    src, why = extract_src()
    if why:
        print(f"  环境不满足：{why}（探针依赖真代码，不做手抄副本）")
        return 2
    body = BODY.replace("__HEADER__", HEADER).replace("__FRAME__", FRAME)
    html = HX.build_page(src, body)
    with tempfile.TemporaryDirectory() as td:
        shutil.copytree(VENDOR, Path(td) / "vendor")
        page = Path(td) / "altclear.html"
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
    r = d["res"]
    print(f"  屏首 4 行：{r['head']}")
    check("A1 仍在主屏（备用屏进入被吞）", r["type"] == "normal", f"type={r['type']}")
    check("A2 旧画面已清（hub 头部 / Connecting 残影消失）",
          (not r["hasHdr"]) and (not r["hasConn"]),
          f"hdr={r['hasHdr']} conn={r['hasConn']}")
    check("A3 清屏之后的 TUI 自己没被误伤", r["hasTui"])
    print("\n" + ("全部通过 ✅" if fails == 0 else f"失败 {fails} 项 ❌"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())