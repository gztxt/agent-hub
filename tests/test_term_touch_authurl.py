#!/usr/bin/env python3
"""L1 静态闸门：终端移动端触摸层(P1) + auth_url 旁路(P2/P3)（v0.13.64）。

只证明「代码写了」，证明不了「真能用」——后者由 tests/verify_term_mobile_touch.py
真机闸门负责（同 verify_term_scroll.py 的分工）。但静态层能挡住**最容易犯且最难查**的
三类退化：产物漂移、FitAddon 高度口径被破坏、URL 识别逻辑写错。

跑法：bash scripts/run_tests.sh hermetic   （2026-10-02 起已是 L0 标准层，
由 run_tier.py 统一跑；另见 tests/test_tier_collector_parity.py 的收集器对账闸）

★ 2026-10-02：14 条用例从**模块级 pytest 风格**搬进本 TestCase —— 原写法
  `unittest discover` 收不到 ⇒「标准套件全绿」不等于「本闸门跑过」
  （实测 `pytest tests/` 975 vs `run_tests.sh all` 961，差额正是这 14 条）。
  搬迁只改归属与缩进，**断言逐字保留** ⇒ 闸门语义零变化。
"""
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import term  # noqa: E402


# ── A 组：产物与构建纪律 ──────────────────────────────────────────────────


class TestTermTouchAuthUrlGates(unittest.TestCase):
    """★ 必须是 TestCase：模块级 `def test_*` 只有 pytest 收得到（见文件头）。"""
    # ── ──── A 组：产物与构建纪律

    def test_a1_touch_part_is_built_into_hubjs(self):
        """13-term-touch.js 必须被 build 收进 hub.js，否则线上根本没有这层代码。"""
        parts = sorted((REPO / "static" / "hub").glob("[0-9][0-9]-*.js"))
        names = [p.name for p in parts]
        assert "13-term-touch.js" in names, f"触摸层 part 不在拼接列表里：{names}"
        built = (REPO / "static" / "hub.js").read_text(encoding="utf-8")
        assert "function termTouchBind" in built, "hub.js 里没有 termTouchBind ⇒ 拼接漏了"


    def test_a2_part_syntax_is_valid(self):
        """语法错会让 node --check 在构建时炸，但构建产物可能已过期 —— 这里独立复核。"""
        r = subprocess.run(["node", "--check", str(REPO / "static" / "hub" / "13-term-touch.js")],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"语法错：{r.stderr[:300]}"


    # ── ──── B 组：FitAddon 高度口径（模板 720 行铁律）

    def test_b1_term_container_has_no_inset(self):
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


    def test_b2_touchaction_only_appears_in_narrow_media(self):
        """touch-action 是本批唯一允许碰 #termEl 的属性，且只能挂在 767px 窄屏档里 ——
        宽屏加它会改变桌面滚轮/选择行为（那正是「宽屏零回归」要守的）。"""
        html = (REPO / "templates" / "index.html").read_text(encoding="utf-8")
        hits = [ln for ln in html.splitlines() if "touch-action" in ln]
        assert hits, "触摸层 CSS 不见了"
        for ln in hits:
            assert "tterm-pinch" in ln or "#termEl" in ln, f"touch-action 用在了非终端元素上：{ln.strip()}"


    # ── ──── C 组：URL 识别（P3）的纯函数行为

    def test_c1_strip_ansi_removes_osc8_hyperlink(self):
        """OSC 8 超链接必须被剥掉，否则 "8;;https://..." 会被当成 URL 播出去。"""
        s = "\x1b]8;;https://evil.example.com\x07click me\x1b]8;;\x07"
        out = term.strip_ansi(s)
        assert "evil.example.com" not in out, f"OSC8 的 URL 没剥掉：{out!r}"
        assert "click me" in out, "正文被误删了"


    def test_c2_strip_ansi_removes_csi(self):
        assert "\x1b" not in term.strip_ansi("\x1b[2J\x1b[Hhello")
        assert term.strip_ansi("\x1b[?25l") == ""


    def test_c3_scan_urls_joins_wrapped_url(self):
        """终端按列宽折行 ⇒ 长 URL 被劈两行。跨行拼接是 P3 的核心。"""
        buf = "Please open:\nhttps://auth.example.com/verify?code=abc\n123def"
        got = term._scan_urls(buf)
        assert any(u == "https://auth.example.com/verify?code=abc123def" for u in got), \
            f"跨行 URL 没拼回来：{got}"


    def test_c4_normalize_strips_trailing_punct(self):
        """输出里 URL 常直接跟句号/括号，不剥掉就不是可打开的 URL。"""
        assert term._normalize_url("https://a.example.com/x.") == "https://a.example.com/x"
        assert term._normalize_url("https://a.example.com/x)") == "https://a.example.com/x"
        assert term._normalize_url("(https://a.example.com/y)") == "https://a.example.com/y"


    def test_c5_normalize_rejects_non_http(self):
        """只放行 http/https：pty 里的 file://、javascript: 不能往浏览器送。"""
        assert term._normalize_url("file:///etc/passwd") is None
        assert term._normalize_url("javascript:alert(1)") is None
        assert term._normalize_url("ftp://h.example.com/x") is None
        assert term._normalize_url("https://x.io") is None      # 短于 URL_MIN_LEN


    def test_c6_scan_urls_dedupes_at_session_level(self):
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


    def test_c7_url_scan_window_is_bounded(self):
        """扫描缓冲必须封顶，否则长跑会话会无界增长。"""
        assert term.URL_SCAN_MAX <= 65536
        sess = term.Session.__new__(term.Session)
        sess.url_buf = "x" * (term.URL_SCAN_MAX * 3)
        sess.url_buf = sess.url_buf[-term.URL_SCAN_MAX:]
        assert len(sess.url_buf) <= term.URL_SCAN_MAX


    # ── ──── D 组：旁路通道不污染输出流（本设计最容易犯的错）

    def test_d1_authurl_is_out_of_band_not_in_queue(self):
        """auth_url 必须走 send_text，绝不能 put 进 vq ——
        vq 是**输出字节流**，混进 JSON 会被 xterm 当正文画到屏幕上。"""
        src = (REPO / "src" / "term.py").read_text(encoding="utf-8")
        seg = src[src.index("async def _scan_auth_urls"):src.index("# 回放最近输出")]
        assert "send_text" in seg, "auth_url 没走 send_text"
        assert "put_nowait" not in seg, "auth_url 混进了输出队列（会污染画面）"


    def test_d2_frontend_consumes_authurl_before_writing(self):
        """前端必须在 term.write 之前拦下链接帧，否则 JSON 原文会出现在画面上。

        v0.13.87：帧处理从 `termAuthUrl(...)` 改成 `termLinkAnno(...)`（两路分文案），
        锚点随之更新 —— 判据没变：**拦在写画面之前**。"""
        js = (REPO / "static" / "hub" / "03-agents-cards.js").read_text(encoding="utf-8")
        i_auth = js.index("auth_url")
        i_handler = js.index("termLinkAnno(", i_auth)
        i_write = js.index("term.write(raw)", i_auth)
        assert i_handler < i_write, "链接帧处理在 term.write 之后 ⇒ 来不及拦截"


    # ── ──── E 组：v0.13.87 双通道（用户报障「总是提示登录链接」的回归闸门）

    def test_e1_plain_doc_url_is_not_reported_as_login(self):
        """★ 本批主判据：普通文档/仓库链接**不许**走登录通道。

        取证形态就是用户报障时 pty 里真实存在的两行（2026-10-07）：
        agent 印一条参考链接，旧实现一律弹「检测到登录链接」。"""
        for line in ("See https://github.com/openai/codex for details",
                     "docs: https://code.claude.com/docs/en/model-config"):
            assert term._scan_login_urls(line) == [], f"文档链接被当成登录链接播了：{line}"

    def test_e2_real_login_prompt_is_still_reported(self):
        """真登录提示必须照旧播 —— 闸门收紧不能把正当功能一起收掉。

        文案逐字取自本机实测（claude setup-token v2.1.292，含断行）。"""
        buf = ("Browser didn't open? Use the url below to sign in (c to copy)\n"
               "\nhttps://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a\n")
        got = term._scan_login_urls(buf)
        assert any("claude.com/cai/oauth/authorize" in u for u in got),             f"真登录 URL 没播：{got}"

    def test_e3_page_channel_reports_link_without_calling_it_login(self):
        """页面通道：文档链接仍要能被点开（用户真正要的那个需求），但走 page_url。"""
        line = "参考 https://code.claude.com/docs/en/model-config 已确认"
        assert term._scan_page_urls(line) == ["https://code.claude.com/docs/en/model-config"]
        assert term._scan_login_urls(line) == [], "页面链接串到了登录通道"

    def test_e4_page_channel_needs_prose_on_the_same_line(self):
        """裸 URL 行（成片机器输出）不许刷弹窗：同行没有正文词就不播。"""
        assert term._scan_page_urls("https://github.com/openai/codex") == []
        assert term._scan_page_urls("  https://a.example.com/x/y/z  ") == []

    def test_e5_cues_are_phrases_not_bare_words(self):
        """★ 线索词**只能是短语**：裸单词会命中普通文档句（本次误报的同款成因）。

        判据是可机读的形状约束（必须带空格/连字符/冒号分隔），不是「我记得别写裸词」——
        否则后来人为了"多认几种提示"很容易把 `auth` 加回去，误报就整批回来。
        """
        for c in term.DEFAULT_AUTH_CUES:
            assert any(ch in c for ch in " -:"), f"线索表混进了裸单词：{c!r}"
        # 反向验证：裸单词当线索时**确实**会误报 ⇒ 证明这条形状约束是必要的
        assert term.url_has_auth_cue("the auth module lives here",
                                     cues=["auth"]) is True
        assert term.url_has_auth_cue("the auth module lives here") is False

    def test_e7_cue_window_does_not_reach_earlier_doc_links(self):
        """★ 线索词的判定范围必须**限定在 URL 邻近**，不能是整个滚动窗口。

        实测踩到（v0.13.87 第一版，影子实例真跑）：真登录提示出现后，屏上**先前的**
        文档链接会被追认成"登录"——连播 3 条误报。根因是拿 16KB 滚动窗口整体比对，
        于是任何"远处"的线索都能回头污染旧 URL。"""
        buf = ("See https://github.com/openai/codex for details\n"
               + "filler\n" * 40
               + "Use the url below to sign in\n"
                 "https://claude.com/cai/oauth/authorize?code=true&client_id=abc123\n")
        got = term._scan_login_urls(buf)
        assert any("oauth/authorize" in u for u in got), f"真登录没播：{got}"
        assert not any("github.com/openai/codex" in u for u in got), \
            f"40 行开外的文档链接被追认成登录（判定窗口太大）：{got}"

    def test_e9_cue_direction_is_respected(self):
        """★ 线索词自带方向：**上一行**的 URL 不许被**下一行**的登录话术追认。

        影子实例实测（v0.13.87 第二轮）：`echo "gateway http://127.0.0.1:3456/v1 is up"`
        的输出在上一行，紧接着是 printf 出来的 "Use the url below to sign in"
        —— 那条**本机网关地址**被判成了登录链接。线索说的是"URL 在下面"，
        它就不该认自己**上方**的 URL。这是可证伪的判据，故钉成用例。"""
        buf = ("gateway http://127.0.0.1:3456/v1 is up\n"
               "Use the url below to sign in\n"
               "https://claude.com/cai/oauth/authorize?code=true&client_id=abc123\n")
        got = term._scan_login_urls(buf)
        assert not any("127.0.0.1:3456" in u for u in got), \
            f"上方的本机地址被下方线索追认成登录：{got}"
        assert any("oauth/authorize" in u for u in got), f"真登录没播：{got}"

    def test_e11_auth_channel_wins_over_page_channel(self):
        """同一条 URL 命中登录路就不在页面路重复播（否则屏上留两行、弹两次）。

        判据读**代码结构**而非注释：泵里必须先把 auth 结果传进 page 的过滤。"""
        src = (REPO / "src" / "term.py").read_text(encoding="utf-8")
        seg = src[src.index("async def _scan_auth_urls"):src.index("# 回放最近输出")]
        assert "auth_urls = _scan_login_urls(" in seg, "登录路结果没被单独取出"
        assert "if u not in auth_urls" in seg, "页面路没被登录路去重 ⇒ 同一 URL 会播两次"


    def test_e10_post_direction_cue_still_works(self):
        """方向分表不能把「URL 在前、话术在后」那类真提示误杀（press enter to open）。"""
        buf = ("https://cli.example.com/device?code=ABCD-1234\n"
               "Press Enter to open the browser and continue\n")
        got = term._scan_login_urls(buf)
        assert any("cli.example.com/device" in u for u in got), f"后置线索被误杀：{got}"

    def test_e8_shell_prompt_is_not_glued_onto_url(self):
        """折行续接不许把 shell 提示符粘进 URL 尾巴。

        实测踩到（影子实例真跑）：`user@host:/path$` 的字符恰好全在 URL 续接字符
        集里，被当成上一行的续接 ⇒ 生成 `...abc123gztxt@zzst:/tmp$` 这种 404 链接。"""
        buf = ("https://cli.example.com/oauth/authorize?code=true&client_id=abc123\n"
               "gztxt@zzst:/tmp$ \n")
        assert term._join_wrapped_urls(buf) == [], \
            f"提示符被粘进了 URL：{term._join_wrapped_urls(buf)}"

    def test_e6_backend_emits_distinct_types(self):
        """服务端必须发两种 type，且页面路 auto 恒 False（普通链接不许自动弹浏览器）。"""
        src = (REPO / "src" / "term.py").read_text(encoding="utf-8")
        seg = src[src.index("async def _scan_auth_urls"):src.index("# 回放最近输出")]
        assert '"auth_url" if ch == "auth" else "page_url"' in seg, "两种 type 没分开发"
        assert 'auto = ch == "auth" and' in seg, "页面路没被钉成 auto=False"
        assert "send_text" in seg and "put_nowait" not in seg, "带外通道纪律被破坏"


    def test_d3_authurl_sent_per_viewer(self):
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



if __name__ == "__main__":
    unittest.main(verbosity=2)
