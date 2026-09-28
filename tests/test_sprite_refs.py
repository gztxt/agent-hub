"""L0 · 图标引用完整性：**每个被引用的 sprite id 都必须在 index.html 里存在**。

起因（v0.13.52 实测）：系统菜单「知识库」行前面是空白 —— 根因不是 CSS、不是渲染时机，
是 `SYS_PAGES` 里给 kb 写的图标名 `book` **在 sprite 里根本没有对应 symbol**。
`<use href="#i-book"/>` 指向不存在的 id 时浏览器**静默什么都不画**（不报错、不留占位，
DOM 里 `<svg class="i">` 尺寸照旧是 16px），所以这道缺口 grep 不出来、肉眼只看到
"这一行没图标"，只有把「引用集合」与「定义集合」做差集才现形。

本闸门同时堵两个方向：
  ① JS 侧 —— `ico('x')` 字面量 + 图标表（SYS_PAGES / SET_PAGES / NAV_ICONS）里的名字；
  ② HTML 侧 —— 模板里直接写的 `<use href="#i-x"/>`。
只查「引用是否存在」，不查「symbol 是否被用到」（库里允许有备用图形，反向断言会假红）。
"""
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
HTML = REPO / "templates" / "index.html"
JS_PARTS = sorted((REPO / "static" / "hub").glob("[0-9][0-9]-*.js"))
JS_SERVED = REPO / "static" / "hub.js"

#: 图标表：常量名 → 怎么从数组体里取图标名
#:   "third" = 每个子数组的第 3 项（[page, 中文名, 图标]）
#:   "value" = 对象字面量的值（{group: 图标}）
ICON_TABLES = {"SYS_PAGES": "third", "SET_PAGES": "third", "NAV_ICONS": "value"}


def _symbol_ids() -> set:
    return set(re.findall(r'<symbol id="(i-[a-z0-9-]+)"', HTML.read_text(encoding="utf-8")))


def _array_body(src: str, const: str) -> str:
    """从 `const X = [` 起做括号配对，返回数组体（不含外层方括号）。"""
    m = re.search(r"const\s+" + re.escape(const) + r"\s*=\s*\[", src)
    if not m:
        return ""
    i, depth = m.end() - 1, 0
    while i < len(src):
        if src[i] == "[":
            depth += 1
        elif src[i] == "]":
            depth -= 1
            if depth == 0:
                return src[m.end():i]
        i += 1
    return src[m.end():]      # 配对不完整（写坏了）：退化为取到末尾，让断言去红


def _icon_names(src: str) -> set:
    names = set(re.findall(r"ico\(\s*'([a-z0-9-]+)'", src))
    for const, how in ICON_TABLES.items():
        body = _array_body(src, const)
        if not body:
            continue
        if how == "third":
            names |= set(re.findall(r"\[\s*'[^']*'\s*,\s*'[^']*'\s*,\s*'([a-z0-9-]+)'\s*\]", body))
        else:
            names |= set(re.findall(r":\s*'([a-z0-9-]+)'", body))
    return names


class T(unittest.TestCase):
    def setUp(self):
        self.html = HTML.read_text(encoding="utf-8")
        self.ids = _symbol_ids()

    def test_sprite_defined(self):
        self.assertGreaterEqual(len(self.ids), 40, "sprite 数量骤减说明模板被改坏")

    def test_js_icon_refs_exist(self):
        """JS 里出现的每个图标名都必须有 symbol（含数组表里那种不经过 ico('x') 字面量的）。"""
        missing = {}
        for f in JS_PARTS + [JS_SERVED]:
            src = f.read_text(encoding="utf-8")
            bad = sorted(n for n in _icon_names(src) if ("i-" + n) not in self.ids)
            if bad:
                missing[f.name] = bad
        self.assertEqual(missing, {}, "图标名在 sprite 里没有对应 symbol（静默空白图标）：%s" % missing)

    def test_html_use_refs_exist(self):
        """模板里直接写的 <use href="#i-x"/> 同样要命中。"""
        refs = set(re.findall(r'<use(?:\s[^>]*)?\s+href="#(i-[a-z0-9-]+)"', self.html))
        refs |= set(re.findall(r'href="#(i-[a-z0-9-]+)"', self.html))
        self.assertTrue(refs, "模板里一个 <use> 都没有 ⇒ 本用例的正则失效了")
        self.assertEqual(sorted(refs - self.ids), [], "HTML 里引用了不存在的 sprite id")


if __name__ == "__main__":
    unittest.main(verbosity=2)
