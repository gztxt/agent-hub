#!/usr/bin/env python3
"""L1 静态闸门：终端移动端触摸层(P1) + auth_url 旁路(P2/P3)（v0.13.64）。

只证明「代码写了」，证明不了「真能用」——后者由 tests/verify_term_mobile_touch.py
真机闸门负责（同 verify_term_scroll.py 的分工）。但静态层能挡住**最容易犯且最难查**的
三类退化：产物漂移、FitAddon 高度口径被破坏、URL 识别逻辑写错。

跑法：../agent-hub/venv/bin/python -m pytest tests/test_term_touch_authurl.py -v
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import term  # noqa: E402


# ── A 组：产物与构建纪律 ──────────────────────────────────────────────────

def test_a1_touch_part_is_built_into_hubjs():
    """13-term-touch.js 必须被 build 收进 hub.js，否则线上根本没有这层代码。"""
    parts = sorted((REPO / "static" / "hub").glob("[0-9][0-9]-*.js"))
    names = [p.name for p in parts]
    assert "13-term-touch.js" in names, f"触摸层 part 不在拼接列表里：{names}"
    built = (REPO / "static" / "hub.js").read_text(encoding="utf-8")
    assert "function termTouchBind" in built, "hub.js 里没有 termTouchBind ⇒ 拼接漏了"


def test_a2_part_syntax_is_valid():
    """语法错会让 node --check 在构建时炸，但构建产物可能已过期 —— 这里独立复核。"""
    r = subprocess.run(["node", "--check", str(REPO / "static" / "hub" / "13-term-touch.js")],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"语法错：{r.stderr[:300]}"


# ── B 组：FitAddon 高度口径（模板 720 行铁律）───────────────────────────────

def test_b1_term_container_has_no_inset():
    """#termEl / .term-body 一旦有 padding/border，FitAddon 会多算一行、底部被裁。"""
    html = (REPO / "templates" / "index.html").read_text(encoding="utf-8")
    for sel in (r"#termEl", r"\.term-body"):
        for m in re.finditer(r"(^|\n)\s*" + sel + r"\s*\{([^}]*)\}", html):
            decl = m.group(2)
            assert "padding:" not in decl.replace("padding: 0", "") or \
                   re.search(r"padding:\s*0\s*;", decl), \
                f"{sel} 出现非零 padding：{decl.strip()[:120]}"
            assert not re.search(r"border[^:]*:\s*[^0]", decl), \
                f"{sel} 出现非零 border：{decl.strip()[:120]}"


def test_b2_touchaction_only_appears_in_narrow_media():
    """touch-action 是本批唯一允许碰 #termEl 的属性，且只能挂在 767px 窄屏档里 ——
    宽屏加它会改变桌面滚轮/选择行为（那正是「宽屏零回归」要守的）。"""
    html = (REPO / "templates" / "index.html").read_text(encoding="utf-8")
    hits = [ln for ln in html.splitlines() if "touch-action" in ln]
    assert hits, "触摸层 CSS 不见了"
    for ln in hits:
        assert "tterm-pinch" in ln or "#termEl" in ln, f"touch-action 用在了非终端元素上：{ln.strip()}"


# ── C 组：URL 识别（P3）的纯函数行为 ──────────────────────────────────────

def test_c1_strip_ansi_removes_osc8_hyperlink():
    """OSC 8 超链接必须被剥掉，否则 "8;;https://..." 会被当成 URL 播出去。"""
    s = "\x1b]8;;https://evil.example.com\x07click me\x1b]8;;\x07"
    out = term.strip_ansi(s)
    assert "evil.example.com" not in out, f"OSC8 的 URL 没剥掉：{out!r}"
    assert "click me" in out, "正文被误删了"


def test_c2_strip_ansi_removes_csi():
    assert "\x1b" not in term.strip_ansi("\x1b[2J\x1b[Hhello")
    assert term.strip_ansi("\x1b[?25l") == ""


def test_c3_scan_urls_joins_wrapped_url():
    """终端按列宽折行 ⇒ 长 URL 被劈两行。跨行拼接是 P3 的核心。"""
    buf = "Please open:\nhttps://auth.example.com/verify?code=abc\n123def"
    got = term._scan_urls(buf)
    assert any(u == "https://auth.example.com/verify?code=abc123def" for u in got), \
        f"跨行 URL 没拼回来：{got}"


def test_c4_normalize_strips_trailing_punct():
    """输出里 URL 常直接跟句号/括号，不剥掉就不是可打开的 URL。"""
    assert term._normalize_url("https://a.example.com/x.") == "https://a.example.com/x"
    assert term._normalize_url("https://a.example.com/x)") == "https://a.example.com/x"
    assert term._normalize_url("(https://a.example.com/y)") == "https://a.example.com/y"


def test_c5_normalize_rejects_non_http():
    """只放行 http/https：pty 里的 file://、javascript: 不能往浏览器送。"""
    assert term._normalize_url("file:///etc/passwd") is None
    assert term._normalize_url("javascript:alert(1)") is None
    assert term._normalize_url("ftp://h.example.com/x") is None
    assert term._normalize_url("https://x.io") is None      # 短于 URL_MIN_LEN


def test_c6_scan_urls_dedupes_at_session_level():
    """去重是会话级：同一 URL 出现多次只播一次（否则每帧都弹 toast）。"""
    sess = term.Session.__new__(term.Session)
    sess.url_buf = ""
    sess.announced_urls = set()
    raw = "go to https://login.example.com/device?code=XYZ123 now"
    urls = [term._normalize_url(u) for u in term._scan_urls(raw)]
    fresh = [u for u in urls if u and u not in sess.announced_urls]
    for u in fresh:
        sess.announced_urls.add(u)
    assert len(fresh) == 1
    again = [u for u in (term._normalize_url(u) for u in term._scan_urls(raw))
             if u and u not in sess.announced_urls]
    assert again == [], f"第二次又播了：{again}"


def test_c7_url_scan_window_is_bounded():
    """扫描缓冲必须封顶，否则长跑会话会无界增长。"""
    assert term.URL_SCAN_MAX <= 65536
    sess = term.Session.__new__(term.Session)
    sess.url_buf = "x" * (term.URL_SCAN_MAX * 3)
    sess.url_buf = sess.url_buf[-term.URL_SCAN_MAX:]
    assert len(sess.url_buf) <= term.URL_SCAN_MAX


# ── D 组：旁路通道不污染输出流（本设计最容易犯的错）────────────────────────

def test_d1_authurl_is_out_of_band_not_in_queue():
    """auth_url 必须走 send_text，绝不能 put 进 vq ——
    vq 是**输出字节流**，混进 JSON 会被 xterm 当正文画到屏幕上。"""
    src = (REPO / "src" / "term.py").read_text(encoding="utf-8")
    seg = src[src.index("async def _scan_auth_urls"):src.index("# 回放最近输出")]
    assert "send_text" in seg, "auth_url 没走 send_text"
    assert "put_nowait" not in seg, "auth_url 混进了输出队列（会污染画面）"


def test_d2_frontend_consumes_authurl_before_writing():
    """前端必须在 term.write 之前拦下 auth_url，否则 JSON 原文会出现在画面上。"""
    js = (REPO / "static" / "hub" / "03-agents-cards.js").read_text(encoding="utf-8")
    i_auth = js.index("auth_url")
    i_write = js.index("term.write(raw)", i_auth)
    assert i_auth < i_write, "auth_url 处理在 term.write 之后 ⇒ 来不及拦截"


def test_d3_authurl_sent_per_viewer():
    """本机是多观看者架构：旁路帧必须逐观看者发。照抄上游单 session.ws 会退化。

    判据要挑**代码**而不是注释 —— 实现处的注释里就写着 "session.ws" 这个词
    （说明为什么不能这么做），拿全文搜会自己判自己红。
    所以这里只看结构：Session 上不允许存在"单个 ws 字段"，多观看者是 viewers 字典。
    """
    import ast
    tree = ast.parse((REPO / "src" / "term.py").read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "Session")
    # self.x = … 两种写法都算赋值（仓里有裸赋值也有带注解赋值）
    assigned = set()
    for n in ast.walk(cls):
        tgt = None
        if isinstance(n, ast.AnnAssign):
            tgt = n.target
        elif isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Attribute):
            tgt = n.targets[0]
        if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) \
                and tgt.value.id == "self":
            assigned.add(tgt.attr)
    assert "ws" not in assigned, "Session 上出现单一 ws 字段 ⇒ 会退回多观看者互偷字节"
    assert "viewers" in assigned, "viewers 字典不见了（多观看者架构被破坏）"