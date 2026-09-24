#!/usr/bin/env python3
"""P4 资产面板闸门：static/hub/07-asset-panel.js 的「四态可区分」不变量。

分层（tests/tiers.py）：
  L0 静态不变量 —— 只读 static/hub.js 与 templates/index.html 的真文本。不起服务、不用 node、
                   不读宿主目录，干净 runner 上结论一模一样。
  L1 host       —— 真跑 node，执行从 hub.js **原样抽出**的 assetStateOf / assetResultsHtml。
                   node 是宿主能力 ⇒ @host_only，不混进 L0 的"零跳过"闸门。

为什么这个面板值得单独一层（而不是"看完截图就算过"）：
09-22 的 ccpocket-bridge 事故定性是「active / 端口在听 / /health ok 而会话起不来，静默 21 天」。
前端把这条路封死的唯一办法，是让 `后端降级` 与 `真的没有` **在文案上不可混淆**。
所以本闸门的重点不是"渲染出了东西"，而是**红向**：喂 degraded 与 404 两种载荷时，
输出里绝不许出现"没有 / 零命中"这类会被读成"资产为空"的字样。
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "tests"))

from _hub_extract import extract_function, read_hub   # noqa: E402
import tiers                                          # noqa: E402

SHARD = _REPO / "static" / "hub" / "07-asset-panel.js"
TPL = _REPO / "templates" / "index.html"
SHARD05 = _REPO / "static" / "hub" / "05-chat-and-history.js"
SHARD01 = _REPO / "static" / "hub" / "01-core-boot.js"

# 本仓主题里**真实存在**的色 token（:root 里逐个核过）。
# 面板只准用这些；写个不存在的 var 会静默退化成继承色 ⇒ 警告变普通文字 = 新的静默不可用。
THEME_TOKENS = ("--ok", "--muted", "--warn", "--danger-text")


def _text(p):
    return p.read_text(encoding="utf-8")


class TestAssetPanelL0(unittest.TestCase):
    """静态不变量：接线完整 + 四态文案互斥 + 不用未定义 token + 守浮层/分档红线。"""

    @classmethod
    def setUpClass(cls):
        cls.hub = read_hub()
        cls.js = _text(SHARD)
        cls.html = _text(TPL)

    # ── 接线：新分片必须真被装配，否则"写了但线上没有" ──
    def test_shard_is_registered_in_all_four_places(self):
        """SYS_PAGES / PAGE_LABELS / go() 懒加载 / 页面 section —— 缺任何一个都会静默不显示。"""
        s5 = _text(SHARD05)
        self.assertIn("['assets', '资产', 'layers']", s5, "SYS_PAGES 没挂 assets ⇒ 菜单里进不去")
        self.assertIn("assets: '资产'", s5, "PAGE_LABELS 缺 assets ⇒ 面包屑空白")
        self.assertIn("if (page === 'assets') loadAssets();", _text(SHARD01),
                      "go() 里没有懒加载分支 ⇒ 进页面永远显示未加载")
        self.assertIn('id="page-assets"', self.html, "模板里没有该 section ⇒ go() 会回落总览")

    def test_hubjs_contains_shard_body(self):
        """产物形态判据：hub.js 必须真的带上了新分片的函数体（拼接没漏）。"""
        for fn in ("assetStateOf", "assetResultsHtml", "loadAssets", "initAssetPanel"):
            self.assertIsNotNone(extract_function(self.hub, fn),
                                 "hub.js 里找不到 %s ⇒ 忘记跑 scripts/build_hubjs.sh" % fn)

    def test_state_words_are_mutually_distinct(self):
        """六态文案必须字面互斥，且'确实零命中'只准出现在 empty 一态。
        idle 是第五态：memory/kb 检索端点缺 q 按契约回 422，若把它当降级报，
        用户会在「什么都没输」的时候看到一条故障——语义上诚实但会误导。"""
        m = {"ok": "有结果", "empty": "确实零命中", "idle": "待输入检索词",
             "unwired": "按设计未接入", "degraded": "部分后端不可用", "down": "端点不可用"}
        for k, v in m.items():
            self.assertIn(v, self.js, "六态文案缺 %s(%s)" % (k, v))
        self.assertEqual(len(set(m.values())), 6)

    def test_unwired_is_not_marked_as_failure(self):
        """`available:false` 带 why = 设计裁定（kb 的 wigolo、skill 的 TDAI 注册表），
        带 error 才是故障。混成一色会让首屏变一屏假告警，从而埋掉真告警。"""
        self.assertIn("unwired", self.js)
        self.assertIn("v.why && !v.error", self.js.replace(" ", " "),
                      "未接入/故障二分的判据不见了 ⇒ 又会把设计裁定涂黄")

    def test_idle_paths_declare_why_no_request(self):
        """idle 文案必须说清"为何不发请求"，不能只写"暂无"。"""
        self.assertIn("needsQ", self.js, "needsQ 标记丢了 ⇒ 空查询会去収 422")
        self.assertIn("422", self.js, "idle 文案必须点名契约原因（缺 q 回 422）")

    def test_only_defined_theme_tokens(self):
        """红向：曾经写过 var(--warn-text)/var(--ok-text)/var(--text-3) —— 主题里**没有**，
        浏览器会退化成继承色，警告条静默变普通文字。此断言把这类"发明 token"钉死。"""
        import re
        used = set(re.findall(r"var\((--[a-z0-9-]+)[,)]", self.js))
        html = self.html
        undefined = [t for t in used
                     if (t + ":") not in html and t not in THEME_TOKENS]
        self.assertEqual(undefined, [],
                         "面板用了主题里不存在的色 token：%s" % undefined)

    def test_no_bare_localStorage_and_no_second_breakpoint(self):
        """分档五不变量：不许裸 localStorage、不许第二处断点定义。
        ★ 只对**去注释后的代码**判卷：本文件头注释里就写着「不新增 matchMedia」，
        拿原文做子串判断会把自律声明当成违规（第一轮就这么红过一次）。"""
        import sys as _sys
        _sys.path.insert(0, str(_REPO / "tests"))
        from _js_min import strip_comments
        code = strip_comments(self.js)
        self.assertNotIn("localStorage.", code, "必须走 lsGet/lsSet 守卫")
        self.assertNotIn("matchMedia", code, "断点唯一真相在 01 的 HUB_NARROW_MQ")
        self.assertNotIn("innerWidth", code, "不许另写宽度判据")

    def test_no_inline_onclick_in_new_panel(self):
        """浮层三条配套红线：新面板交互一律 data-asset 委托，不许 inline onclick。"""
        seg = self.html[self.html.index('id="page-assets"'):]
        seg = seg[:seg.index("</section>")]
        self.assertNotIn("onclick=", seg, "页面内联 onclick 会旁路事件委托")
        self.assertIn('data-asset="search"', seg)
        self.assertIn('data-asset="refresh"', seg)

    def test_no_new_media_query_added(self):
        """@media 只准待在唯一断点定义处；新页面靠 .mem-grid 自身响应式，不另写一份。"""
        import sys as _sys
        _sys.path.insert(0, str(_REPO / "tests"))
        from _js_min import strip_comments
        code = strip_comments(self.js)
        self.assertNotIn("@media", code)
        self.assertNotIn("max-width", code)

    def test_acl_wording_matches_backend_semantics(self):
        """P3 改了 ACL 判定（deny 优先、`*` 通配算规则），前端文案必须同步。
        红向：旧文案「无规则默认放行」在真有 `*` deny 的库里是假的 ——
        这正是 09-24 侦察报出「配置看起来生效、其实是白名单」的成因。"""
        for p in (SHARD05, TPL):
            t = _text(p)
            self.assertNotIn("无规则 = 所有 agent 默认放行", t, "%s 还在写旧口径" % p.name)
            self.assertNotIn("无规则默认放行）", t, "%s 的 ACL 标题未同步 P3 语义" % p.name)
        self.assertIn("deny 优先", _text(TPL), "模板 ACL 标题未写明 deny 优先")


@tiers.host_only
class TestAssetPanelRendering(unittest.TestCase):
    """L1：把 hub.js 里的真函数抽出来在 node 里跑，喂四种载荷看文案是否可区分。"""

    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("SKIP(host-dependent): 本机没有 node，渲染判定无法执行")
        hub = read_hub()
        parts = [extract_function(hub, n) for n in
                 ("escapeHtml", "assetStateOf", "assetResultsHtml",
                  "backendLedger", "itemListHtml")]
        if any(p is None for p in parts):
            raise AssertionError("hub.js 里抽不到面板函数（或被改名）⇒ 渲染判据无法针对真代码执行")
        cls.src = "\n".join(parts)
        # 一个最小 src 形状（与 ASSET_SOURCES[0] 同构），只为跑渲染函数
        cls.src += ("\nconst SRC0 = {label:'记忆', items:d=>d.memories||[],"
                    "itemText:it=>it.content||'',itemMeta:()=>''};")

    def _run(self):
        js = self.src + "\n" + r"""
function out(tag, s) { console.log(tag + "||" + s.replace(/\s+/g, " ")); }
const err404 = new Error("HTTP 404");
const errNet = new Error("Failed to fetch");
out("S_404", assetStateOf(null, err404));
out("S_NET", assetStateOf(null, errNet));
out("S_OK", assetStateOf({count: 2, backends: [{name: "tdai_l1", ok: true, count: 2, ms: 5}]}));
out("S_EMPTY", assetStateOf({count: 0, backends: [{name: "local", ok: true, count: 0, ms: 1}]}));
out("S_DEG", assetStateOf({count: 0, degraded: ["tdai_l1"],
                           backends: [{name: "local", ok: true, count: 0, ms: 1},
                                      {name: "tdai_l1", ok: false, count: 0, ms: 9, error: "conn refused"}]}));
out("S_DEG_ARR", assetStateOf({count: 3, degraded: ["turbovec"], backends: []}));
out("R_404", assetResultsHtml(SRC0, null, "down", err404));
out("R_DEG", assetResultsHtml(SRC0, {count: 0, degraded: ["tdai_l1"], note: "一路后端不可用",
      backends: [{name: "local", ok: true, count: 0, ms: 1},
                 {name: "tdai_l1", ok: false, count: 0, ms: 9, error: "conn refused"}]}, "degraded", null));
out("R_EMPTY", assetResultsHtml(SRC0, {count: 0, backends: [{name: "local", ok: true, count: 0, ms: 1}],
      note: ""}, "empty", null));
out("R_OK", assetResultsHtml(SRC0, {count: 1, backends: [{name: "local", ok: true, count: 1, ms: 1}],
      memories: [{content: "端口 3102 占用"}]}, "ok", null));
"""
        p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, "node 报错：\n" + p.stderr[:800])
        out = {}
        for line in p.stdout.splitlines():
            if "||" in line:
                k, v = line.split("||", 1)
                out[k] = v
        self.assertTrue(out, "node 无任何输出，判定其实是空转")
        return out

    # ── 状态机 ──
    def test_states_are_distinguished(self):
        o = self._run()
        self.assertEqual(o["S_404"], "down")
        self.assertEqual(o["S_NET"], "degraded")
        self.assertEqual(o["S_OK"], "ok")
        self.assertEqual(o["S_EMPTY"], "empty")
        self.assertEqual(o["S_DEG"], "degraded")
        self.assertEqual(o["S_DEG_ARR"], "degraded",
                         "只有 degraded 数组、没有 backends 时也必须报降级")

    def test_degraded_never_renders_as_empty(self):
        """★ 本闸门的核心红向：降级态文案里不许出现"后端都正常 / 确实没命中"这一类读法。"""
        o = self._run()
        banned = ("确实没有命中", "确实零命中", "后端全部在线", "后端在线且全部正常")
        for tag in ("R_DEG", "R_404"):
            txt = o[tag]
            for b in banned:
                self.assertNotIn(b, txt, "%s 把故障渲染成了空态：%s" % (tag, b))
        self.assertIn("部分后端不可用", o["R_DEG"])
        self.assertIn("不完整", o["R_DEG"], "降级必须告知清单不完整")
        self.assertIn("conn refused", o["R_DEG"], "降级要点名是哪一路、什么错")
        self.assertIn("这不是", o["R_404"], "404 必须明说这不是“没资产”")
        self.assertIn("端点取不到", o["R_404"])

    def test_empty_state_still_says_zero_hits(self):
        """反向对照：真·零命中时才准说"没有命中" —— 否则四态又糊成一坨。"""
        o = self._run()
        self.assertIn("确实没有命中", o["R_EMPTY"])
        self.assertIn("后端全部在线", o["R_EMPTY"])
        self.assertNotIn("不可用", o["R_EMPTY"])

    def test_ok_state_shows_content_and_backend_ledger(self):
        o = self._run()
        self.assertIn("端口 3102 占用", o["R_OK"])
        self.assertIn("local✓", o["R_OK"].replace(" ", ""))

    def test_html_is_escaped(self):
        """载荷里带 HTML 必须被转义（门面数据来自磁盘/他方服务，不当可信输入）。"""
        js = self.src + """
const evil = {count:1, backends:[], memories:[{content:'<img src=x onerror=alert(1)>'}]};
console.log(assetResultsHtml(SRC0, evil, "ok", null));
"""
        p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[:400])
        self.assertNotIn("<img src=x", p.stdout, "未转义 ⇒ 磁盘/远端内容可注入面板")
        self.assertIn("&lt;img", p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
