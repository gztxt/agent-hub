#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L0 hermetic：运行日志前端页（page-runlog）静态闸门。

照抄 test_kb_frontend_pages.py 的分层口径：零宿主依赖、不出网、不渲染 DOM——
真渲染留端侧验收。**不允许 SKIP**。

为什么这些用例必须存在：
1. **section 必须真的存在**：SYS_PAGES 有 runlog 但 index.html 没有 section ⇒
   go() 静默退回总览（09-23 事故第二幕），面板永远看不到。
2. **鉴权 UX 三态**：本页是 GET 但后端按写判——前端漏带 token/漏做 401 分支，
   用户看到的是「加载失败」而实际是「没口令」，两种修法完全不同。
3. **api() 的 err.http additive**：既有调用方只读 .message，挂新字段不许破坏它们。
"""
import pathlib
import re
import sys
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))


class TestRunlogFrontend(unittest.TestCase):

    def setUp(self):
        self.html = (_REPO / "templates" / "index.html").read_text(encoding="utf-8")
        self.js = (_REPO / "static" / "hub.js").read_text(encoding="utf-8")
        self.boot = (_REPO / "static" / "hub" / "01-core-boot.js").read_text(encoding="utf-8")
        self.runlog_js = (_REPO / "static" / "hub" / "08-runlog.js").read_text(encoding="utf-8")

    def test_section_exists(self):
        self.assertIn('id="page-runlog"', self.html,
                      "page-runlog section 缺失 ⇒ go() 静默退回总览")

    def test_sys_pages_registry(self):
        self.assertIn("['runlog', '运行日志'", self.js)

    def test_page_labels_cover(self):
        self.assertIn("runlog: '运行日志'", self.js)

    def test_lazy_hook_paired(self):
        self.assertIn("page === 'runlog'", self.js,
                      "go() 必须挂 loadRunlog 钩子，漏挂=页面空白（不报错的那种）")

    def test_loader_functions_defined(self):
        for fn in ("loadRunlog", "loadRunlogMore", "renderRunlog",
                   "runlogAuthGate", "runlogTokenRetry", "runlogFetch"):
            self.assertIn(f"function {fn}(", self.js, f"{fn} 缺失 ⇒ 面板点了没反应")

    def test_dom_ids_used_by_js_exist_in_html(self):
        for dom_id in ("runlogBody", "rlSource", "rlSubject", "rlStatus",
                       "rlWindow", "rlHint", "rlMoreBtn"):
            self.assertIn(f'id="{dom_id}"', self.html,
                          f"#{dom_id} 在 JS 里被引用但 HTML 缺失 ⇒ null 崩溃")

    def test_inline_handlers_reference_real_functions(self):
        for m in re.finditer(r'onclick="(?!if\()([a-zA-Z_$][\w$]*)\(', self.html):
            fn = m.group(1)
            self.assertIn(f"function {fn}(", self.js,
                          f"HTML 引用 {fn}() 但 hub.js 未定义（点击即报错）")

    def test_api_error_carries_http_status(self):
        """api() 错误对象挂 err.http（additive）：runlog 页靠 401/503 区分鉴权态。"""
        self.assertIn("err.http = r.status", self.boot)
        self.assertIn("err.payload = data", self.boot)

    def test_auth_gate_not_autoprompting(self):
        """loadRunlog 不直接调 termToken()——弹框只在用户点按钮时发生（05:297 教训）。"""
        body = re.sub(r"/\*.*?\*/", "", self.runlog_js, flags=re.S)  # 去注释后检查代码本体
        load_fn = re.search(r"async function loadRunlog\(.*?\n\}", body, re.S)
        self.assertTrue(load_fn, "loadRunlog 函数本体缺失")
        self.assertNotIn("termToken()", load_fn.group(0),
                         "loadRunlog 内不许直接 termToken()（每次进页都弹框）")
        retry_fn = re.search(r"function runlogTokenRetry\(\).*?\n\}", body, re.S)
        self.assertTrue(retry_fn)
        self.assertIn("termToken()", retry_fn.group(0), "重试按钮才允许弹 termToken()")

    def test_get_request_carries_token_manually(self):
        """GET 不走 api() 的自动 token（它只给写方法带）⇒ 本页 runlogFetch 自己带。"""
        self.assertIn("'x-hub-token'", self.runlog_js)
        self.assertIn("lsGet('hub.term.token')", self.runlog_js)

    def test_cursor_pagination_uses_before_id(self):
        self.assertIn("before_id", self.runlog_js, "翻页必须走 id 游标（不用 OFFSET）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
