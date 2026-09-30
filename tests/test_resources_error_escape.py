"""L0 hermetic：资源页错误输出必须走统一助手且转义（P1-24，PT-20260930-01）。

两处真缺口（都不是"风格统一"，是会出事的）：

① loadResources 的 catch 直吐 e.message 进 innerHTML：
       listEl.innerHTML = '...加载失败：' + e.message + '</div>';
   后端 message 里带 & < > " ' 就破版，且与全站 boxFail 口径不一致
   （boxFail 有 escapeHtml + 「重试」按钮，资源页两样都没有）。

② updateAgentSummary 读的是 **textContent**（纯文本 "2 进程 · 120MB"），
   却拿它跑 /<b>(\d+)<\/b>\s*进程/ 这种**只可能匹配 innerHTML** 的正则 ——
   永远匹配不上，计数更新是死代码；即便碰巧匹配，把 textContent 的结果塞回
   innerHTML 也会把已转义的实体（&lt;）二次解释成标签。

③ 重试入口：boxFail 生成 onclick="loadResources()"，事件对象会被当 force 传进去。
   语义上重试确实该强制刷新，但不该靠"事件对象恰好 truthy"成立。

本测试锁：资源页不再出现裸的 e.message→innerHTML；计数更新走 textContent；
重试入口是显式函数。
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RES = REPO / "static" / "hub" / "11-resources.js"


def _src() -> str:
    s = RES.read_text(encoding="utf-8")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s


class TestNoRawErrorInjection(unittest.TestCase):
    def setUp(self):
        self.s = _src()

    def test_no_innerhtml_with_error_message(self):
        self.assertNotRegex(
            self.s, r"innerHTML\s*=[^;\n]*e\.message",
            "仍有 innerHTML 直接拼 e.message（后端 message 含实体字符就破版）")

    def test_load_failure_uses_boxfail(self):
        seg = self.s.split("async function loadResources(", 1)[1].split("\n}", 1)[0]
        self.assertIn("boxFail", seg, "加载失败没走统一 boxFail 助手")

    def test_retry_entry_is_explicit(self):
        self.assertNotIn('"loadResources"', self.s,
                         "重试仍用 onclick=loadResources()：事件对象会被当 force 传入")
        self.assertRegex(self.s, r"function resourcesRetry\(",
                         "缺显式重试入口")


class TestSummaryCountUsesTextContent(unittest.TestCase):
    def setUp(self):
        self.s = _src()
        self.seg = self.s.split("function updateAgentSummary(", 1)[1].split("\n}", 1)[0]

    def test_no_html_regex_against_textcontent(self):
        """核心锁：不允许对 textContent 跑只匹配 innerHTML 的正则。"""
        self.assertNotRegex(self.seg, r"textContent[^;]*\.match\([^)]*<b>",
                            "仍在对 textContent 跑含 <b> 的 HTML 正则 ⇒ 永远匹配不上（死代码）")

    def test_writes_back_as_text(self):
        self.assertNotRegex(self.seg, r"innerHTML\s*=",
                            "计数更新走 innerHTML ⇒ textContent 的实体会被二次解释")
        self.assertRegex(self.seg, r"textContent\s*=",
                         "计数更新没有写回 textContent")

    def test_plain_number_regex(self):
        self.assertRegex(self.seg, r"\\d\+\\s\*进程",
                         "计数正则应针对纯文本「N 进程」")


if __name__ == "__main__":
    unittest.main(verbosity=2)
