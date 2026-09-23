"""侧栏抽屉「窄屏被宽屏偏好污染」回归闸门（2026-09-23 事故）。

事故一句话：折叠态存在一个**与宽度无关**的全局键 `hub.sidebar` 里，而且**加载时就写盘**。
于是一次桌面访问把"展开"存成全局偏好 → 手机打开时 stored==='0' → 不收起 →
CSS `@media (max-width:767px) .sidebar:not(.collapsed)` 是 `position:fixed;
width:236px; z-index:46` 的白色覆盖层，在 390px 视口上盖掉 61% —— 用户看到「整页被
白板糊住」。resize 只重画终端，抽屉态永不重算，所以刷新也不会好。

分两层（口径见 tests/README.md + tiers.py）：
  L0 静态不变量：只读 static/hub.js 与 templates/index.html 的**真文本**，不起服务、
     不用 node、不读宿主目录 —— 干净 runner 上结论必须一模一样。
  L1 host：真跑 node，执行从 hub.js **原样抽出**的 `sidebarWantCollapsed`，并拿
     `git show HEAD:` 里的**旧实现**做红-绿对照（手抄的旧代码只能证明我抄得对）。
     node 是宿主能力 ⇒ 打 @host_only，绝不混进 L0 的"零跳过"闸门。
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tiers                                     # noqa: E402
from _hub_extract import extract_function, read_hub   # noqa: E402

REPO = Path(__file__).resolve().parents[1]
HUB = REPO / "static" / "hub.js"
TPL = REPO / "templates" / "index.html"


def _text(p):
    return p.read_text(encoding="utf-8")


def _init_sidebar_body(src):
    fn = extract_function(src, "initSidebar")
    if not fn:
        raise AssertionError("抽不到 initSidebar（改名/删除会让本闸门失去意义）")
    return fn


def _old_git(path, rev="HEAD"):
    r = subprocess.run(["git", "-C", str(REPO), "show", "%s:%s" % (rev, path)],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _old_git_find(path="static/hub/06-manager-tasks.js", marker="setItem('hub.sidebar',"):
    """按内容回溯最近一个仍含 `marker` 的版本，返回该版本的文件正文（找不到给空串）。"""
    r = subprocess.run(["git", "-C", str(REPO), "log", "--format=%H", "--", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return ""
    for rev in r.stdout.split():
        body = _old_git(path, rev)
        if marker in body:
            return body
    return ""


class SidebarStaticInvariants(unittest.TestCase):
    """L0：四条不变量的静态形态。每条都对应上面事故的一个必要条件，断一条即失效。"""

    @classmethod
    def setUpClass(cls):
        cls.js = _text(HUB)
        cls.html = _text(TPL)
        cls.body = _init_sidebar_body(cls.js)

    def test_pref_key_is_partitioned_by_viewport(self):
        """不变量 1：一档一键。窄屏永不读宽屏写下的值。"""
        self.assertIn("'hub.sidebar.narrow'", self.js)
        self.assertIn("'hub.sidebar.wide'", self.js)
        self.assertRegex(self.js, r"sidebarPrefKey\s*=\s*\(\)\s*=>\s*mqNarrow\.matches")

    def test_width_blind_key_never_written_anymore(self):
        """污染路径必须从根上没有：不允许再写那个与宽度无关的旧键。
           （旧键仍允许**读** —— 只作宽屏一次性迁移用。）"""
        self.assertNotRegex(self.js, r"setItem\(\s*['\"]hub\.sidebar['\"]\s*,")

    def test_load_path_does_not_persist(self):
        """不变量 2：加载不写盘。老代码正是在加载这一步把桌面的"展开"固化成全局偏好。
           判据取 initSidebar 里 resolve 的实参 —— 必须显式传 persist=false。"""
        m = re.search(r"const resolve\s*=\s*\(\)\s*=>\s*apply\((.*?)\);", self.body, re.S)
        self.assertIsNotNone(m, "initSidebar 里找不到 resolve 的解析调用")
        self.assertTrue(m.group(1).rstrip().endswith(", false"),
                        "加载路径没传 persist=false ⇒ 一打开页面就固化偏好，事故会复发")

    def test_no_second_breakpoint_definition_in_js(self):
        """不变量 3：断点只有一处定义。`innerWidth < 768` 这类自己抄一份的写法必须绝迹。"""
        self.assertNotIn("innerWidth < 768", self.js)
        self.assertNotRegex(self.js, r"innerWidth\s*<=\s*767")

    def test_js_and_css_agree_on_the_breakpoint(self):
        """断点漂移闸门：JS 的 matchMedia 值，必须等于**真的**包住
           `.sidebar:not(.collapsed){position:fixed}` 那条覆盖层的 @media 值。
           两边各自写各自的数字，就是这次事故的另一半（改一边忘另一边没人知道）。"""
        js_n = re.search(r"matchMedia\(\s*['\"]\(max-width:\s*(\d+)px\)['\"]", self.js)
        self.assertIsNotNone(js_n, "JS 侧没有 matchMedia 断点定义")
        idx = None
        for m in re.finditer(r"\.sidebar:not\(\.collapsed\)\s*\{[^}]*position:\s*fixed", self.html, re.S):
            idx = m.start()
        self.assertIsNotNone(idx, "CSS 里找不到窄屏抽屉覆盖层规则（规则被改名？闸门需同步）")
        css_n = None
        for m in re.finditer(r"@media\s*\(max-width:\s*(\d+)px\)", self.html[:idx]):
            css_n = int(m.group(1))
        self.assertIsNotNone(css_n, "覆盖层规则不在任何 @media 内")
        self.assertEqual(int(js_n.group(1)), css_n,
                         "JS 断点 %s ≠ CSS 断点 %s ⇒ 抽屉态与样式在中间宽度打架"
                         % (js_n.group(1), css_n))

    def test_breakpoint_change_is_re_acted_on(self):
        """跨断点必须重算（转屏/拖窗口/桌面缩放）—— 老代码只挂 resize 重画终端。"""
        self.assertRegex(self.body, r"mqNarrow\.addEventListener\(\s*['\"]change['\"]")
        self.assertRegex(self.body, r"addListener", "还要留 Safari<14 的老接口")


@tiers.host_only
class SidebarBehaviourTruthTable(unittest.TestCase):
    """L1：拿真代码在真 node 里跑判定，并和 git HEAD 里的旧实现做红-绿对照。"""

    NEW_FN = None
    OLD_EXPR = None

    @classmethod
    def setUpClass(cls):
        if shutil_which_node() is None:
            raise unittest.SkipTest("SKIP(host-dependent): 本机没有 node，判定表无法执行")
        fn = extract_function(_text(HUB), "sidebarWantCollapsed")
        assert fn, "抽不到 sidebarWantCollapsed"
        cls.NEW_FN = fn
        # 红基线不能钉死在 HEAD：本修复一提交，HEAD 就没有旧代码了。
        # 改为**按内容回溯**：找最近一个仍写着与宽度无关旧键的版本 —— 那才是真旧实现。
        cls.OLD_SRC = _old_git_find()
        m = re.search(r"apply\(\s*(stored === '1'[^\n]*?)\s*\);", cls.OLD_SRC or "")
        cls.OLD_EXPR = m.group(1) if m else None

    def _run(self, js):
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, "node 报错：" + r.stderr.strip()[:400])
        return json.loads(r.stdout.strip())

    def test_new_decision_table(self):
        cases = [
            # narrow, stored(本档), legacy, 期望 collapsed
            [True, None, "0", True,  "手机 + 桌面污染值'0' ⇒ 收起（09-23 白板事故本体）"],
            [True, None, "1", True,  "手机 + 旧值'1' ⇒ 收起"],
            [True, None, None, True, "手机首次 ⇒ 默认收成图标条"],
            [True, "0", "1", False, "手机上的显式选择优先于任何 legacy"],
            [True, "1", "0", True,  "手机显式收起"],
            [False, None, "0", False, "桌面 legacy'0' ⇒ 展开（存量习惯不夺走）"],
            [False, None, "1", True,  "桌面 legacy'1' ⇒ 收起"],
            [False, None, None, False, "桌面首次 ⇒ 展开"],
            [False, "1", "0", True,  "本档键优先于 legacy"],
            [False, "0", "1", False, "本档键优先于 legacy（反向）"],
        ]
        js = (self.NEW_FN + "\nconst C=" + json.dumps(cases) +
              ";process.stdout.write(JSON.stringify(C.map(c=>sidebarWantCollapsed(c[0],c[1],c[2]))))")
        got = self._run(js)
        for c, g in zip(cases, got):
            self.assertEqual(g, c[3], "用例不通过：%s（narrow=%s stored=%r legacy=%r 实得 %r）"
                             % (c[4], c[0], c[1], c[2], g))

    def test_old_implementation_is_the_red_baseline(self):
        """红对照：旧逻辑在**同一份输入**上必须判成"展开" —— 证明缺陷真存在于旧代码，
           而不是我为了给绿灯编了个故事。
           旧表达式里 `narrow` 是闭包里的**函数**（`narrow()`），不是布尔值 ——
           包时必须供同名同形的闭包，否则红是红的，但是假红。旧表达式本身逐字取自 git。"""
        self.assertTrue(self.OLD_EXPR,
                        "旧版本里找不到那行 apply(...) ⇒ 红基线取不到，本闸门退化为自证")
        js = ("function oldWant(narrowVal, stored){ const narrow = () => narrowVal; return %s; }\n"
              % self.OLD_EXPR +
              "const C=[[true,'0'],[true,'1'],[true,null]];"
              "process.stdout.write(JSON.stringify(C.map(c=>oldWant(c[0],c[1]))))")
        old = self._run(js)
        self.assertEqual(old, [False, True, True],
                         "旧逻辑在窄屏带污染值时没判成'展开'⇒ 要么旧缺陷不成立，要么红基线取错了")
        # 同一输入下新旧结论必须相反，否则这次修复什么都没改变
        js2 = (self.NEW_FN + "\nprocess.stdout.write(JSON.stringify(sidebarWantCollapsed(true,null,'0')))")
        self.assertEqual(self._run(js2), True, "新逻辑未纠正旧逻辑的窄屏误判")

    def test_old_code_polluted_storage_on_load(self):
        """红对照第二半：旧实现确实存在"与宽度无关的键 + 加载即写盘"这两个必要条件。"""
        self.assertTrue(self.OLD_SRC, "取不到旧版本源码")
        self.assertRegex(self.OLD_SRC, r"setItem\(\s*'hub\.sidebar'\s*,")
        self.assertIn("innerWidth < 768", self.OLD_SRC)


def shutil_which_node():
    import shutil
    return shutil.which("node")


if __name__ == "__main__":
    unittest.main(verbosity=2)
