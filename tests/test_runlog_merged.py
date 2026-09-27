#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L0 hermetic：系统菜单「运行日志」页**已删除**，内容并入设置→日志（v0.13.47）。

照抄 test_kb_frontend_pages.py 的分层口径：零宿主依赖、不出网、不渲染 DOM——
真渲染留端侧验收。**不允许 SKIP**。

为什么这批用例是**反向闸门**（断言"不许回来"）而不是正向存在性：
删页面最容易删一半 —— 菜单项没了但 section 还在（白屏入口）、section 没了但
go() 钩子还在（进页静默退回总览，不报错那种）。逐项钉死"残留即红"，
再正向钉住新入口必须接得住原能力（source=rest 视图 + subject 过滤），
否则"合并"就退化成"丢功能"。
"""
import pathlib
import re
import sys
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))


class TestRunlogRemovedFromSystemMenu(unittest.TestCase):
    """① 系统菜单里的运行日志：section / 导航项 / go() 钩子 / 分片，四者都不许残留。"""

    def setUp(self):
        self.html = (_REPO / "templates" / "index.html").read_text(encoding="utf-8")
        self.js = (_REPO / "static" / "hub.js").read_text(encoding="utf-8")
        self.boot = (_REPO / "static" / "hub" / "01-core-boot.js").read_text(encoding="utf-8")

    def test_section_gone(self):
        self.assertNotIn('id="page-runlog"', self.html,
                         "page-runlog section 必须随菜单项一起删除，留着=进页白屏")
        for dom_id in ("runlogBody", "rlSource", "rlSubject", "rlStatus",
                       "rlWindow", "rlHint", "rlMoreBtn"):
            self.assertNotIn(f'id="{dom_id}"', self.html,
                             f"#{dom_id} 是运行日志页的私用 DOM，必须一起清掉")

    def test_nav_entry_gone(self):
        self.assertNotIn("['runlog', '运行日志'", self.js, "SYS_PAGES 里不许再有 runlog")
        self.assertNotIn("runlog: '运行日志'", self.js, "PAGE_LABELS 里不许再有 runlog")

    def test_go_hook_gone(self):
        self.assertNotIn("page === 'runlog'", self.js,
                         "go() 里残留 loadRunlog 钩子 ⇒ 路由到一个不存在的页（静默退回总览）")

    def test_shard_gone(self):
        self.assertFalse((_REPO / "static" / "hub" / "08-runlog.js").exists(),
                         "08-runlog.js 分片必须删除（build 按字典序拼接，留着会进产物）")
        for fn in ("loadRunlog", "loadRunlogMore", "renderRunlog", "runlogFetch"):
            self.assertNotIn(f"function {fn}(", self.js, f"{fn} 随页面删除，不许留在产物里")


class TestRunlogMergedIntoSettingsLogs(unittest.TestCase):
    """② 原能力必须在设置→日志页接住：运行日志来源视图 + subject 过滤。"""

    def setUp(self):
        self.html = (_REPO / "templates" / "index.html").read_text(encoding="utf-8")
        self.boot = (_REPO / "static" / "hub" / "01-core-boot.js").read_text(encoding="utf-8")

    def test_rest_source_option_present(self):
        self.assertIn('id="page-settings-logs"', self.html)
        self.assertIn('<option value="rest">', self.html,
                      "设置→日志必须有「运行日志（三中心检索）」来源，否则原能力没接住")

    def test_subject_select_present(self):
        self.assertIn('id="logSubject"', self.html,
                      "subject 下拉缺失 ⇒ 运行日志的按路筛选丢了")

    def test_subject_plumbed_into_request_and_change(self):
        self.assertIn("p.set('subject', v('logSubject'))", self.boot,
                      "选了 subject 却没进查询串 ⇒ 下拉是摆设")
        self.assertIn("id === 'logSubject'", self.boot,
                      "subject 切换必须走同一个 change 委托出口重拉")

    def test_no_inline_handler_added(self):
        """新加的 subject 下拉不许带 inline onclick —— 交互出口只有一个（委托）。"""
        seg = self.html[self.html.index('id="page-settings-logs"'):]
        seg = seg[:seg.index("</section>")]
        self.assertNotIn("onclick", seg, "设置→日志页内不许出现 inline onclick")


class TestSharedDisciplineStillHolds(unittest.TestCase):
    """③ 删页面时顺手保住的既有纪律（跨页不变量，不是本页私有）。"""

    def setUp(self):
        self.html = (_REPO / "templates" / "index.html").read_text(encoding="utf-8")
        self.js = (_REPO / "static" / "hub.js").read_text(encoding="utf-8")
        self.boot = (_REPO / "static" / "hub" / "01-core-boot.js").read_text(encoding="utf-8")

    def test_api_error_carries_http_status(self):
        """api() 挂 err.http（additive）：日志页靠 401/503 区分鉴权态与故障态。"""
        self.assertIn("err.http = r.status", self.boot)
        self.assertIn("err.payload = data", self.boot)

    def test_inline_handlers_reference_real_functions(self):
        for m in re.finditer(r'onclick="(?!if\()([a-zA-Z_$][\w$]*)\(', self.html):
            fn = m.group(1)
            self.assertIn(f"function {fn}(", self.js,
                          f"HTML 引用 {fn}() 但 hub.js 未定义（点击即报错）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
