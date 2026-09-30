"""L0 hermetic：xterm 5.5.0 → 6.0.0 整组升级的闸门（P2-A，PT-20260930-01）。

为什么这批闸门必须是静态的而不是"跑起来看":终端一白屏就是全站核心路径断，
而真实白屏只在**有 WebGL/GPU 的客户端**上复现——CI 与 hermetic 环境恒为 DOM 档，
动态断言会全绿放行。静态闸门锁的是**代码里那些"迟早会漂"的接缝**：

  ① canvas addon 已从 vendor 删除 ⇒ 模板不得再引用它、前端不得再读 `window.CanvasAddon`
     （留一个 `if (window.CanvasAddon && …)` 分支不报错，但永远走不到 = 死代码，
      哪天有人重新放回 canvas.js 它就悄悄复活成第二档 ⇒ 与 vendor 不同源）
  ② `?term=canvas` / localStorage 里的老偏好必须**显式降级 dom 并 warn**，
     不得静默改写（AGENTS.md 分档偏好第 5 条：存量不得改变交互分支的可见性）
  ③ WebGL 图集止血线必须在：上游 `clearTextureAtlas()` 公开存在于 5.5 与 6.0，
     而本项目此前从未调用（显存单调增长属未修态）
  ④ 版本登记与实际文件对账（README 表里的 md5/版本与仓内文件逐字节一致）
"""
import hashlib
import json
import re
import unittest
from pathlib import Path

from _js_min import strip_comments

REPO = Path(__file__).resolve().parents[1]
HUB = REPO / "static" / "hub"
VENDOR = REPO / "static" / "vendor"
TPL = (REPO / "templates" / "index.html").read_text(encoding="utf-8")
TERM_JS = HUB / "03-agents-cards.js"

#: 6.0 全套的期望版本号（与 static/vendor/README.md 的登记表一致）
EXPECTED_VERSIONS = {
    "xterm.js": ("@xterm/xterm", "6.0.0"),
    "xterm.css": ("@xterm/xterm", "6.0.0"),
    "fit.js": ("@xterm/addon-fit", "0.11.0"),
    "addon-webgl.js": ("@xterm/addon-webgl", "0.19.0"),
    "addon-search.js": ("@xterm/addon-search", "0.16.0"),
    "addon-unicode11.js": ("@xterm/addon-unicode11", "0.9.0"),
    "addon-web-links.js": ("@xterm/addon-web-links", "0.12.0"),
    "addon-clipboard.js": ("@xterm/addon-clipboard", "0.2.0"),
}


def _code(p: Path) -> str:
    """去注释的源码（注释里可以自由提到 canvas / 5.5.0，不该因此判红）。"""
    return strip_comments(p.read_text(encoding="utf-8"))


class TestCanvasAddonRemoved(unittest.TestCase):
    """canvas addon 随 6.0 移除（三条取证见 vendor/README.md）。"""

    def test_canvas_file_absent(self):
        self.assertFalse((VENDOR / "addon-canvas.js").exists(),
                         "addon-canvas@0.7.0 的 peerDependencies 锁 @xterm/xterm ^5.0.0，不兼容 6.0")

    def test_no_canvas_script_tag(self):
        self.assertNotIn("addon-canvas", TPL, "模板不得再加载已删除的 canvas addon")

    def test_no_canvas_global_read(self):
        """代码里不再读 window.CanvasAddon —— 留着就是与 vendor 不同源的死分支。"""
        for p in sorted(HUB.glob("*.js")):
            src = _code(p)
            self.assertNotIn("CanvasAddon", src,
                             "%s 仍引用 CanvasAddon：canvas addon 已删，该分支永远走不到" % p.name)

    def test_canvas_never_assigned_as_renderer_name(self):
        """canvas 只能作为**待降级的旧值**出现，不得再被赋成实际渲染器名。"""
        src = _code(TERM_JS)
        for bad in ("termRendererName = 'canvas'", "termRendererName='canvas'",
                    "return 'canvas'", "pref === 'canvas') tries"):
            self.assertNotIn(bad, src, "canvas 不得再作为渲染器名出现：%s" % bad)
        self.assertIn("termRendererName = name", src,
                      "实际渲染器名只应由回落链的循环变量赋出")
        self.assertIn("termRendererName = 'dom'", src)

    def test_fallback_chain_has_no_canvas_entry(self):
        src = _code(TERM_JS)
        m = re.search(r"let tries = \[(.*?)\];", src, re.S)
        self.assertIsNotNone(m, "termLoadRenderer 必须仍有显式回落链（webgl → dom）")
        self.assertIn("'webgl'", m.group(1))
        self.assertNotIn("canvas", m.group(1), "回落链里还有 canvas 项")

    def test_dom_is_the_terminal_fallback(self):
        """webgl 挂不上必须落 DOM（慢但可见），而不是抛错或留白。"""
        src = _code(TERM_JS)
        self.assertIn("termRendererName = 'dom'", src)
        self.assertIn("未挂上 WebGL 渲染器，停留在 DOM 渲染", src)


class TestCanvasPrefDegradesLoudly(unittest.TestCase):
    """`?term=canvas` 与 localStorage 老存量：显式降级 + warn，不静默改写。"""

    def test_query_canvas_warns_and_falls_back(self):
        src = _code(TERM_JS)
        self.assertIn("if (q === 'canvas')", src,
                      "termRendererPref 必须显式识别 ?term=canvas")
        seg = src.split("if (q === 'canvas')", 1)[1][:600]
        self.assertIn("console.warn", seg, "?term=canvas 降级必须 warn：旧链接持有者要看得见")
        self.assertIn("return 'dom'", seg, "?term=canvas 必须降级为 dom")

    def test_stored_canvas_warns_and_migrates(self):
        """存量 localStorage 同样处理，且必须**写回** dom，否则每次开页都重复告警。"""
        src = _code(TERM_JS)
        self.assertIn("if (s === 'canvas')", src, "localStorage 里的 canvas 偏好必须显式降级")
        seg = src.split("if (s === 'canvas')", 1)[1][:600]
        self.assertIn("console.warn", seg)
        self.assertIn("lsSet('hubTermRenderer', 'dom')", seg,
                      "降级后必须写回 dom：否则老存量每页刷一条 warn，永不自愈")

    def test_pref_regex_no_longer_accepts_canvas(self):
        src = _code(TERM_JS)
        for bad in ("/^(webgl|canvas|dom)$/", "/^(webgl|dom|canvas)$/"):
            self.assertNotIn(bad, src, "偏好白名单不得再接受 canvas：%s" % bad)


class TestWebglAtlasSweep(unittest.TestCase):
    """WebGL 图集止血：上游有 API、本项目此前从未调用（显存单调增长 = 未修态）。"""

    def test_upstream_api_present_in_both_versions(self):
        """前置事实：5.5（现网旧版本，备份在 work/ 下）与 6.0 都暴露该 API。"""
        six = (VENDOR / "addon-webgl.js").read_text(encoding="utf-8")
        self.assertIn("clearTextureAtlas", six,
                      "6.0 webgl addon 应仍提供 clearTextureAtlas()；否则止血线要改方案")
        backup = REPO / "work" / "probe" / "vendor" / "backup-5.5.0" / "addon-webgl.js"
        if backup.is_file():
            self.assertIn("clearTextureAtlas", backup.read_text(encoding="utf-8"),
                          "5.5 旧版本同样有该 API（说明此前是「没调用」而非「没法调用」）")

    def test_sweep_calls_clear_texture_atlas(self):
        src = _code(TERM_JS)
        self.assertIn("addon.clearTextureAtlas()", src, "必须真的调 clearTextureAtlas()")

    def test_sweep_is_silent_on_failure_and_stops(self):
        """清理失败绝不能把终端带崩：try/catch 静默降级并停掉这条线。"""
        src = _code(TERM_JS)
        m = re.search(r"function termAtlasSweep\(addon\) \{(.*?)\n\}", src, re.S)
        self.assertIsNotNone(m, "必须有 termAtlasSweep()")
        body = m.group(1)
        self.assertIn("try {", body, "clearTextureAtlas 必须包在 try/catch 里")
        self.assertIn("catch", body)
        self.assertIn("clearInterval", body,
                      "清理失败后要停掉定时器：签名若变了，反复抛错刷屏")

    def test_sweep_only_while_terminal_visible(self):
        """后台标签页不做无用功（页面会长时间挂着终端）。"""
        src = _code(TERM_JS)
        seg = src.split("function termAtlasSweep", 1)[1][:800]
        self.assertIn("termVisible()", seg, "终端不可见时不必清图集")
        self.assertIn("document.hidden", seg, "页面在后台时不必清图集")


class TestVendorVersionLedger(unittest.TestCase):
    """vendor 版本登记与实际文件对账 —— 防"文档说 6.0、盘上是 5.5"。"""

    def test_all_expected_files_exist(self):
        for f in EXPECTED_VERSIONS:
            self.assertTrue((VENDOR / f).is_file(), "vendor 缺 %s" % f)

    def test_readme_table_matches_disk(self):
        """README 表里的 md5 必须等于仓内文件的 md5 —— 逐字节对账。"""
        readme = (VENDOR / "README.md").read_text(encoding="utf-8")
        rows = re.findall(r"\| `([\w.-]+)` \| `([^`]+)` \| \*\*([\w.]+)\*\* \| `([^`]+)` \| `([0-9a-f]{32})` \|",
                          readme)
        self.assertEqual(len(rows), len(EXPECTED_VERSIONS),
                         "README 版本表条目数与实际 vendor 文件数不符（多半是漏登记）")
        for fname, pkg, ver, _sub, md5 in rows:
            self.assertIn(fname, EXPECTED_VERSIONS, "README 登记了不存在的文件 %s" % fname)
            self.assertEqual((EXPECTED_VERSIONS[fname][0], ver), (pkg, EXPECTED_VERSIONS[fname][1]),
                             "%s 的包名/版本与 README 不一致" % fname)
            disk = hashlib.md5((VENDOR / fname).read_bytes()).hexdigest()
            self.assertEqual(md5, disk, "%s 的 README md5 与盘上文件不符" % fname)

    def test_license_lists_every_vendored_package(self):
        lic = (VENDOR / "LICENSE").read_text(encoding="utf-8")
        for pkg, ver in EXPECTED_VERSIONS.values():
            self.assertIn("%s %s" % (pkg, ver), lic,
                          "LICENSE 缺 %s %s 的一节" % (pkg, ver))
        self.assertNotIn("addon-canvas", lic, "已删除的 canvas addon 不该留在 LICENSE")

    def test_clipboard_has_no_external_base64_dependency(self):
        """addon-clipboard@0.2.0 声明依赖 js-base64，但产物已由 webpack 内联。

        这条是动态断言做不到的：若哪天上游改成外链，模板会少一个 <script>，
        运行时报 "Base64 is not defined"，而单测只看得到文件存在。
        """
        src = (VENDOR / "addon-clipboard.js").read_text(encoding="utf-8")
        self.assertIn("3.7.8", src, "addon-clipboard 未内联 js-base64：需检查是否要额外引入")
        self.assertNotIn('define("js-base64"', src, "clipboard 改为外部依赖了，需引入 js-base64")


class TestTemplateTokensInSync(unittest.TestCase):
    """模板的 ?v= 提手必须等于文件内容 md5 前 8 位（由 build_hubjs.sh 维护）。"""

    def test_all_static_tokens_match_content(self):
        stale = []
        for m in re.finditer(r"/static/([\w./-]+)\?v=([0-9a-z]+)", TPL):
            rel, tok = m.group(1), m.group(2)
            f = REPO / "static" / rel
            self.assertTrue(f.is_file(), "模板引用了不存在的静态文件 %s" % rel)
            real = hashlib.md5(f.read_bytes(), usedforsecurity=False).hexdigest()[:8]
            if real != tok:
                stale.append("%s?v=%s（应为 %s）" % (rel, tok, real))
        self.assertEqual(stale, [], "?v= 提手与文件内容脱节：跑 scripts/build_hubjs.sh")


if __name__ == "__main__":
    unittest.main()
