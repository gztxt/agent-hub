"""`/api/skill/list?q=`（Python）与前端搜索框（JS `fuzzyMatch`）的**跨语言口径对账**。

【为什么需要这个文件】v0.13.72 把前端搜索框改成模糊后，`q` 仍是纯子串
⇒ 同一台机器、同一份数据，UI 搜得到而 MCP 门面（`hubmcp.py`，pi/Claude 注入走它）
搜不到。v0.13.73 给 Python 补了一份**手写**的同口径实现。

**手写两份 = 天生会漂。** 而「同源」不能靠自觉，得靠闸门：把**同一批夹具**
分别喂给 JS 与 Python，逐条比对「命中 / 不命中」的判定，任何一侧口径变了就红。

【为什么不是「只测 Python」】只测 Python 只能证它自己符合文档；
两侧**同时**跑同批夹具，才能证「它们彼此相同」——这才是「口径同源」的可执行定义。

【夹具的纪律（踩过的坑，写在这里免得重犯）】
本文件早一版用过**完全等于**查询词的字段去测权重，那种夹具走的是精确快路，
权重压根没参与计算，断言恒真。凡声称「这是近似路径」的用例，
查询词必须与字段文本**不等**。
"""
import subprocess
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "tests"))

import skill  # noqa: E402
from _js_min import strip_comments  # noqa: E402

CORE = _REPO / "static" / "hub" / "01-core-boot.js"
_JS_FUNCS = ("_levenshtein", "_fuzzyTolerance", "_fuzzyFieldScore", "fuzzyMatch")

#: 夹具集。`(说明, 条目, 查询, 期望命中)` —— 两侧跑**同一批**。
#: 刻意覆盖：精确命中 / 手误（近似）/ 短词不容忍 / 无关词 / 多字段命中 / 空查询 /
#: 中文标点分隔（这条是为了钉住 `，`、`、` 漂移，见 `_SEP_RE` 注释）/ 短名。
FIXTURES = [
    ("精确-名称", {"name": "crawl4ai", "description": "抓网页"}, "crawl4ai", True),
    ("手误-数字1替数字4", {"name": "crawl4ai", "description": "抓网页"},
     "crawl1ai", True),
    ("近似-差一个字母", {"name": "mermaid-diagram", "description": "图"}, "mermald", True),
    ("近似-转置", {"name": "agent-dispatch", "description": "派发"}, "agent-dipatch", True),
    ("近似-路径命中", {"name": "x", "description": "y", "path": "/fs/1000/crawl4ai/skill"},
     "crawl1ai", True),
    ("近似-描述命中", {"name": "x", "description": "用 crawl4ai 抓网页"}, "crawl1ai", True),
    ("短词不容忍", {"name": "crawler", "description": "x"}, "cc", False),
    ("无关词", {"name": "crawl4ai", "description": "抓网页"}, "zzzzqqqq", False),
    ("空查询-全匹配", {"name": "crawl4ai", "description": "x"}, "", True),
    ("纯空格查询", {"name": "crawl4ai", "description": "x"}, "   ", True),
    # 下面两条刻意写「不该命中」，防止实现偷偷放宽成「什么都返回」
    ("超长无关词", {"name": "crawler", "description": "x"}, "abcdefghijklmnop", False),
    ("子串短于5字", {"name": "markitdown", "description": "x"}, "markit", True),
    # 中文标点必须参与切词。
    # 【这三条为何这么写】第一版写的是「name='抓网页、转Markdown', q='markdown'」——
    # 结果**不判别**：q 是整段的精确子串，走的是 `_q_score` 里的精确快路（返 1000），
    # 压根没进切词逻辑；把 `，`/`、` 从 `_SEP_RE` 删掉测试照样绿。
    # ⇒ 夹具必须满足「**q 不是整段子串，但接近某个被标点切出来的段**」，
    # 本组用 `AAA抓网页、crawl4ai` + q=`crawl1ai`：正确切词得 60，漂移版得 0。
    ("顿号切词", {"name": "AAA抓网页、crawl4ai", "description": "x"}, "crawl1ai", True),
    ("中文逗号切词", {"name": "AAA抓网页，crawl4ai", "description": "x"}, "crawl1ai", True),
    ("全角冒号切词", {"name": "AAA抓网页：crawl4ai", "description": "x"}, "crawl1ai", True),
    # realpath 是 v0.13.74 补的字段：不传它就搜不到「按来源仓名」
    ("realpath-来源仓", {"name": "ask-matt", "path": "/home/gztxt/.claude/skills/ask-matt/SKILL.md",
                   "realpath": "/fs/1000/ftp/技术文档/mattpocock-skills/skills/engineering/ask-matt/SKILL.md"},
     "mattpocock-skills", True),
]


def _extract_fn(src: str, name: str) -> str:
    """括号配对抠出顶层函数（不走正则，避免抠出半截而假绿）。"""
    import re
    m = re.search(r"^function %s\(" % re.escape(name), src, re.M)
    assert m, "找不到 JS 函数 %s" % name
    i = src.index("{", m.start())
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    assert depth == 0, "JS 函数 %s 括号不配对" % name
    return src[m.start():j + 1]


def _js_match(item: dict, q: str) -> int:
    """把一条夹具交给真 JS 跑，返回**得分**（不是布尔）。"""
    src = strip_comments(CORE.read_text(encoding="utf-8"))
    body = "\n".join(_extract_fn(src, f) for f in _JS_FUNCS)
    body += """
const item = %s;
const q = %s;
const pairs = [['name',3],['description',1],['path',1],['route',1],['realpath',1]]
  .filter(p => item[p[0]] !== undefined)
  .map(p => [item[p[0]], p[1]]);
console.log(fuzzyMatch(pairs, q));
""" % (_js_literal(item), _js_literal(q))
    p = subprocess.run(["node", "-e", body], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, "node 执行失败：%s" % p.stderr[:400]
    return int(p.stdout.strip())


def _js_literal(x) -> str:
    import json
    return json.dumps(x, ensure_ascii=False)


class SkillListQFuzzyParityTest(unittest.TestCase):
    """跨语言口径对账：同一批夹具，JS 与 Python 必须给出**同一个**判定。"""

    def test_each_fixture_matches_between_js_and_python(self):
        """逐条比对**得分**（不只是命不命中）。

        比分数比 bool 强得多：权重改错 3→1 两侧仍可能都「命中」，
        但分数会不等 ⇒ 这里红。排序（建议 3）直接依赖分数，所以分数必须钉住。
        """
        for label, item, q, expected in FIXTURES:
            with self.subTest(label=label):
                js = _js_match(item, q)
                py = skill._q_score(item, q)
                # ① 两侧得分必须完全相同（这是「口径同源」的可执行定义）
                self.assertEqual(js, py,
                                 "口径漂移！JS=%s Python=%s（夹具：%s）" % (js, py, label))
                # ② 且都必须符合夹具声明的期望（防「两侧一起错」也绿）
                self.assertEqual(py > 0, expected,
                                 "两侧一致但与期望不符：命中=%s 期望=%s（夹具：%s）"
                                 % (py > 0, expected, label))

    def test_tolerance_rule_is_identical(self):
        """容忍度阶梯本身要对齐：`≤4` 不容忍 / `≥5` 容忍 1 / `≥8` 容忍 2。"""
        src = strip_comments(CORE.read_text(encoding="utf-8"))
        body = "\n".join(_extract_fn(src, f) for f in _JS_FUNCS)
        body += """
console.log([1,2,3,4,5,6,7,8,12].map(n => _fuzzyTolerance('x'.repeat(n))).join(','));
"""
        p = subprocess.run(["node", "-e", body], capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr[:300])
        js = [int(x) for x in p.stdout.strip().split(",")]
        py = [skill._fuzzy_tolerance("x" * n) for n in (1, 2, 3, 4, 5, 6, 7, 8, 12)]
        self.assertEqual(js, py, "容忍度阶梯漂移：JS=%s Python=%s" % (js, py))

    def test_q_no_longer_silently_drops_typos_on_the_real_path(self):
        """**回归守卫**：`q` 回到纯子串时这里红（v0.13.73 的病根原样复现）。"""
        item = {"name": "crawl4ai", "description": "抓网页"}
        self.assertTrue(skill._match_q(item, "crawl1ai"),
                        "q 又退回纯子串了 ⇒ MCP 门面搜不到 UI 搜得到的东西")
        self.assertFalse("crawl1ai" in item["name"],
                         "夹具坏了：手误串与真名相同 ⇒ 本用例恒真")

    def test_fields_covered_match_documented_set(self):
        """口径文档与实现必须同步：字段清单变了，两侧与注释都得跟着变。"""
        self.assertEqual(
            tuple(k for k, _w in skill._Q_FIELDS),
            ("name", "description", "path", "route", "realpath"),
            "q 参与匹配的字段变了 —— 前端 skillFields()、端点 description 与夹具都要同步")

    def test_frontend_skillfields_uses_the_same_field_table(self):
        """前端技能中心必须传**同一张表**（含 realpath），否则对账永远对不上。"""
        src = strip_comments((_REPO / "static" / "hub" / "04-terminal-ws.js").read_text(encoding="utf-8"))
        body = _extract_fn(src, "skillFields")
        for key in ("name", "description", "path", "route", "realpath"):
            self.assertIn(key, body, "前端 skillFields() 少了 %s" % key)
        self.assertNotIn("[[s.name, 3], [s.description, 1]]", src,
                         "还有地方用旧的两字段简写 ⇒ 会与后端口径分家")


if __name__ == "__main__":
    unittest.main()