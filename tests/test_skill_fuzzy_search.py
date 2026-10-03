"""技能中心搜索的模糊匹配闸门（`skillMatchScore` / `_levenshtein` / `_fuzzyTolerance`）。

【为什么是执行级测试而不是 grep 源码】模糊匹配的判据是**行为**：
`crawl4ai` 能不能召回 `crawl4ai`。grep 只能证明「代码里写了 _levenshtein」，
证不了「手误真的命中」——而 09-23 的 `vitals_loop` 与 09-24 的 TDZ 两起事故
都是同一个家族：**代码存在 ≠ 会被执行**。所以这里把函数从分片里**原样抽出**，
交给 node 真跑一遍。函数体若被改坏或删掉，node 段会红而不是静默通过。

【覆盖的判据】
  1. 用户点名的那个例子：`crawl4ai` → 召回 `crawl4ai`（原 includes() 实测 0 条）；
  2. **精确不回归**：精确子串仍压倒近似（老行为不能被模糊改掉）；
  3. 短查询不容忍（`cc` 容忍 1 会召回一片）；
  4. 空查询全匹配（不炸，等价旧行为）；
  5. 无关词零召回（模糊不能变成「什么都召回」）。

分层：L0 纯逻辑（node 里跑纯函数，不碰网络/磁盘）。
"""
import json
import re
import subprocess
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
SHARD = _REPO / "static" / "hub" / "04-terminal-ws.js"

# 三个函数 + 其依赖（_levenshtein / _fuzzyTolerance），按源码原样抽出
_FUNCS = ("_levenshtein", "_fuzzyTolerance", "skillMatchScore")


def _extract(name: str) -> str:
    """从分片里原样抠出某个顶层函数定义（含其前的注释块）。

    用括号配对而不是正则找 `function X(`：函数体内有字符串、有嵌套括号、
    也有正则字面量，任何简化都会抠出**半截**函数——而半截函数照样能被 node 解析，
    于是测试变成假绿（与 `_js_min.py` docstring 里那两起「闸门在残缺文本上跑」同族）。
    """
    src = SHARD.read_text(encoding="utf-8")
    m = re.search(r"^function %s\(" % re.escape(name), src, re.M)
    assert m, "分片里找不到函数 %s ⇒ 模糊匹配被删了？" % name
    i = src.index("{", m.start())
    depth, j = 0, i
    while j < len(src):
        c = src[j]
        if c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0: break
        j += 1
    assert depth == 0, "函数 %s 括号不配对，抠出来的是半截" % name
    return src[m.start():j + 1]


def _run_node(body: str):
    """把断言体丢给 node 跑。返回 (returncode, stdout, stderr)。"""
    js = "\n".join(_extract(f) for f in _FUNCS) + "\n" + body
    p = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


class SkillFuzzySearchTest(unittest.TestCase):
    def _assert_js(self, body: str, msg: str):
        rc, out, err = _run_node(body)
        self.assertEqual(rc, 0, "%s\n--- stderr ---\n%s\n--- body ---\n%s" % (msg, err, body))
        return out

    def test_crawl4ai_typo_now_matches(self):
        """用户点名的那个例子：`crawl1ai`（数字 1）应召回 `crawl4ai`。

        【本测试曾经是假绿，写法是教训】第一版把两边都**手打**成 `crawl4ai`，
        于是断言「手误能命中」实际在验「真名能命中自己的子串」——恒真，零价值。
        本机 AI 自己就踩过：写完跑绿，真去真盘一跑才发现 score=0。
        ⇒ 手误串一律用 `String.fromCharCode(49)` **拼出来**，不许手打；
        并额外断言「老的 includes() 本来是 false」，确保这条确实只在模糊路径上成立。
        """
        out = self._assert_js(
            "const TYPO='crawl'+String.fromCharCode(49)+'ai';"     # crawl1ai，数字 1
            "const item={name:'crawl4ai',description:'x'};"
            "const oldHit=item.name.toLowerCase().includes(TYPO)"
            "           ||String(item.description||'').toLowerCase().includes(TYPO);"
            "console.log(oldHit?'OLDHIT':'OLDNO', skillMatchScore(item,TYPO));",
            "crawl1ai 手误应命中 crawl4ai")
        oldflag, score = out.split()
        self.assertEqual(oldflag, "OLDNO",
                         "老 includes() 本来就能命中 ⇒ 这条测试不再专属于模糊路径，判据失效")
        self.assertGreater(int(score), 0, "搜 crawl1ai（数字 1）仍召回不到 crawl4ai ⇒ 模糊匹配没生效")

    def test_vacuous_fixture_guard(self):
        """防「夹具恒真」：本组里凡声称是手误的用例，手误串必须与真名**不等**。

        与其指望每个用例都记得手打对，不如把这条纪律本身做成用例。
        """
        out = self._assert_js(
            "const real='crawl4ai', typo='crawl'+String.fromCharCode(49)+'ai';"
            "console.log(real===typo);", "自检")
        self.assertEqual(out, "false",
                         "手误串与真名相等 ⇒ 本组测试已退化成恒真断言（第一版就是这么假绿的）")

    def test_exact_match_outranks_fuzzy(self):
        """精确子串必须压倒近似：否则模糊会把老行为（精确优先）改坏。

        【夹具坑】第二条**不能**写成 `crawl4ai-extra`——它含 `crawl4ai` 子串，
        本就该是 1000，拿它当「近似」会把一条正确行为判成错（写这条测试时真踩过）。
        真正的近似样本是与查询**等长、差一个字符**的名字。
        """
        out = self._assert_js(
            "const a={name:'crawl4ai',description:'x'};"
            "const b={name:'crawl4al',description:'y'};"
            "console.log(skillMatchScore(a,'crawl4ai'), skillMatchScore(b,'crawl4ai'));",
            "精确与近似分数应可比较")
        exact, near = (int(x) for x in out.split())
        self.assertEqual(exact, 1000, "精确子串命中必须是 1000（压倒近似）")
        self.assertGreater(near, 0, "crawl4al 与 crawl4ai 只差一个字符，应被判为近似命中")
        self.assertGreater(exact, near, "精确命中竟不高于近似命中 ⇒ 排序口径反了")

    def test_short_query_no_tolerance(self):
        """短词不容忍编辑距离：`cc` 容忍 1 会召回一片噪声。"""
        self._assert_js(
            "const it={name:'crawler',description:'x'};"
            "if (skillMatchScore(it,'cc')>0) { console.error('短查询误召回'); process.exit(1); }"
            "console.log(0);", "短查询 cc 不该召回 crawler")

    def test_unrelated_query_zero(self):
        """无关词零召回 —— 模糊不能退化成「什么都返回」。"""
        self._assert_js(
            "const it={name:'crawl4ai',description:'抓网页转 Markdown'};"
            "console.log(skillMatchScore(it,'zzzzqqqq'));", "无关词应 0 分")
        self.assertEqual(int(self._assert_js(
            "const it={name:'crawl4ai',description:'抓网页转 Markdown'};"
            "console.log(skillMatchScore(it,'zzzzqqqq'));", "x")), 0)

    def test_empty_query_matches_all(self):
        """空查询全匹配且不抛（等价旧行为，否则一进页面就炸）。"""
        self._assert_js(
            "const it={name:'x',description:'y'};"
            "if (!(skillMatchScore(it,'')>0)) process.exit(1);"
            "if (!(skillMatchScore(it,'   ')>0)) process.exit(2);"
            "console.log(1);", "空/纯空格查询应匹配全部")

    def test_route_filter_and_fuzzy_compose(self):
        """打分与 route 过滤是**与**关系：route 不中时不得因模糊而漏进来。"""
        self._assert_js(
            "const mk=(n,r)=>({name:n,description:'',routes:[r]});"
            "const keep=mk('crawl4ai','claude'), drop=mk('crawl4ai','pi');"
            "const q='crawl4ai';"
            "const f=(s)=>((s.routes||[]).includes('claude')) && skillMatchScore(s,q)>0;"
            "console.log(f(keep), f(drop));", "route 过滤须与模糊命中同时成立")
        self.assertEqual(self._assert_js(
            "const mk=(n,r)=>({name:n,description:'',routes:[r]});"
            "const f=(s)=>((s.routes||[]).includes('claude')) && skillMatchScore(s,'crawl4ai')>0;"
            "console.log(f(mk('crawl4ai','claude')), f(mk('crawl4ai','pi')));", "x"),
            "true false")

    def test_hubjs_contains_the_build(self):
        """产物形态判据：分片改了但忘了跑 build_hubjs.sh 时这里红（先例：test_asset_panel）。"""
        built = (_REPO / "static" / "hub.js").read_text(encoding="utf-8")
        for fn in _FUNCS:
            self.assertIn(fn, built,
                          "hub.js 里找不到 %s ⇒ 忘记跑 scripts/build_hubjs.sh" % fn)


if __name__ == "__main__":
    unittest.main()