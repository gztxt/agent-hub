"""版本与文档漂移闸门（2026-10-05，v0.13.79）。

【为什么需要】
本仓的文档漂移是**已发生过的**，不是假想：

  · `CHANGELOG.md` 曾停在 v0.13.65，而代码已到 v0.13.78 ⇒ **漂 13 个版本**。
    而那 13 版的根因**早就写好了** —— 就挂在 `src/main.py` 的 `VERSION` 字面量
    后面（400 行注释块），只是**不在 CHANGELOG 里**。已用
    `scripts/extract_changelog.py` 逐字搬运归位（368 行）。
  · `tests/README.md` 的分层计数记 983/50/42，实际已是 1162/55/44。

两条漂移的**共同形态**是「文档里的数字/版本号靠人记得更新」。那就让机器盯着。

【判据为什么读源码文本而不 import】
L0 hermetic **禁 `import src.main`**（一 import 就跑 lifespan：开真库、起后台探针、
绑端口 —— `tests/README.md` 的分层表第一行）。所以 `VERSION` 用**正则读文本**取，
这也是它能进 L0 的前提。

【本闸门不覆盖什么（诚实声明）】
它**拦不住** `tests/README.md` 里 L0/L1/L2 三个数字自身的漂移 ——
那三个数没有单一真源可对（`run_tier.py` 的输出才是真源，而它不是文件）。
已在 `tests/README.md:14-24` 把「必须实测」与两处历史留痕写清楚。
"""
import re
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "tests"))
from _js_min import strip_comments  # noqa: E402

MAIN_PY = _REPO / "src" / "main.py"
CHANGELOG = _REPO / "CHANGELOG.md"
README_MD = _REPO / "tests" / "README.md"
EXTRACT_SCRIPT = _REPO / "scripts" / "extract_changelog.py"


def _version() -> str:
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"',
                  MAIN_PY.read_text(encoding="utf-8"), re.M)
    assert m, "src/main.py 里找不到 VERSION 字面量"
    return m.group(1)


class ChangelogTracksVersion(unittest.TestCase):
    def test_changelog_top_matches_version(self):
        """CHANGELOG 顶部版本必须等于 `src/main.py::VERSION`。"""
        ver = _version()
        m = re.search(r"^## v(\S+)", CHANGELOG.read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(m, "CHANGELOG.md 顶部没有 `## vX` 段落")
        self.assertEqual(m.group(1), ver,
                         "CHANGELOG 顶部是 v%s，而 src/main.py::VERSION 是 %s "
                         "⇒ 文档漂移了（bump 后忘了写 CHANGELOG）"
                         % (m.group(1), ver))

    def test_no_prerelease_markers_left_in_top_section(self):
        """顶部段落不该还留着「本批尚未在生产生效」这类未完成标记。

        这不是格式洁癖：那种标记留在顶部会让读者以为当前版本还没落地，
        而实际它可能早已切生产 —— 与「版本漂移」同族的误导。
        """
        # ★ 读**全文**，不做 [:4000] 截断（2026-10-09 修）。
        #   原写法把前 4000 字符切片后再找当前版本段落，于是「段落长度 > 4000」
        #   会让 re.search 找不到 ⇒ 报「找不到当前版本的段落」——
        #   而真实原因是段落长，不是段落不存在。
        #   这是**假红**：它逼着写文档的人把证据链删短，而不是让人修判据。
        #   工作区纪律明写「假红的闸门比没有闸门更坏（会逼人改断言）」。
        #   本判据要检查的是「当前版本段落**里**有没有陈旧状态标记」，
        #   与文档总长、与其他段落长度都无关 ⇒ 按语义取段落，不该有字数上限。
        #   触发这件事的实测：v0.13.101 段落 4181 字符（历史最长 v0.13.99 = 3726）。
        head = CHANGELOG.read_text(encoding="utf-8")
        ver = _version()
        m = re.search(r"^## v%s\b.*?(?=^## )" % re.escape(ver), head, re.M | re.S)
        self.assertIsNotNone(m, "找不到当前版本的段落")
        seg = m.group(0)
        for marker in ("尚未在生产生效", "待重启", "本批**尚未"):
            self.assertNotIn(marker, seg,
                             "当前版本段落里还留着 %r ⇒ 状态标记过期了" % marker)


class VersionCommentIsSelfContained(unittest.TestCase):
    """当前版本的根因注释必须自带全部关键字 —— 不依赖已被归档的历史块。

    这是 `tests/test_term_scroll_sensitivity.py` 那套「版本钉」的**镜像**判据：
    它管「根因不许随 bump 蒸发」，本类管「归档之后当前版仍自洽」。
    两者缺一，就会出现「上一版的根因在 CHANGELOG、这一版的根因在指针后面」
    这种两边都没有的空洞。
    """

    def test_version_line_has_a_root_cause_note(self):
        src = MAIN_PY.read_text(encoding="utf-8")
        i = src.index('VERSION = "%s"' % _version())
        seg = src[i:i + 3000]
        self.assertRegex(seg, r"#\s+[①②③④]",
                         "当前版本的 VERSION 注释里没有分条根因（①②③④）")

    def test_pointer_to_changelog_is_well_formed(self):
        """留指针时要说清「搬去哪、怎么搬的」，否则下一个人会重复搬运或以为丢了。

        ⚠ 这条**不用 skipTest** 表达「已归档 / 未归档」两种状态 ——
        L0 hermetic 的铁律是「出现 SKIP 即分层放错」（run_tests.sh 退出码 2），
        而「未归档」不是「本机条件不满足」，它是**另一种合法状态**，
        该用断言表达而不是跳过。（第一版写成 skipTest，当场被闸门判红。）
        """
        src = MAIN_PY.read_text(encoding="utf-8")
        # ⚠ 判据用「归档至」而不是「已归档至」：main.py 里的实际措辞是
        #   「已于 2026-10-05 归档至 ── CHANGELOG.md（脚本 …）──」
        # 第一版查「已归档至」差一个字而判红 —— 判据过严就是逼人改文案求绿。
        m = re.search(r"归档至", src)
        self.assertIsNotNone(m,
                             "main.py 的 VERSION 注释里没有归档指针 —— "
                             "若历史根因仍留在 main.py，那是归档前的状态，"
                             "请先跑 scripts/extract_changelog.py 再留指针")
        seg = src[max(0, m.start() - 200):m.start() + 400]
        self.assertIn("CHANGELOG.md", seg, "指针没指向 CHANGELOG.md")
        self.assertIn("extract_changelog.py", seg,
                      "指针应说明用哪个脚本搬的，否则下个人会手工重写一遍")


class ExtractorIsIdempotent(unittest.TestCase):
    """抽取脚本必须幂等 —— 否则第二次跑就会把 CHANGELOG 撑成两份。"""

    def test_script_exists(self):
        self.assertTrue(EXTRACT_SCRIPT.is_file(),
                        "scripts/extract_changelog.py 不存在（历史根因靠它归位）")

    def test_every_extractable_version_already_in_changelog(self):
        """main.py 里还能抽到的历史块，CHANGELOG 必须已经有了。

        ⚠ 脚本导入失败**不 skip**：那是真缺陷（闸门引用了不存在的模块），
        不是「本机条件不满足」。L0 出现 skip 即分层放错（run_tests.sh 退出码 2）。
        """
        sys.path.insert(0, str(EXTRACT_SCRIPT.parent))
        try:
            from extract_changelog import extract_blocks  # noqa: E402
        except ImportError as e:      # noqa: BLE001
            self.fail("scripts/extract_changelog.py 无法导入（%s）—— "
                      "它被 tests/test_changelog_drift.py 引用，缺失即闸门失效" % e)
        ver = _version()
        blocks = extract_blocks(MAIN_PY.read_text(encoding="utf-8"))
        chlog = CHANGELOG.read_text(encoding="utf-8")
        have = set(re.findall(r"^## (v[\d.]+)", chlog, re.M))
        missing = [v for v, _ in blocks
                   if v != ver and v not in have]
        self.assertEqual([], missing,
                         "这些版本在 main.py 里还有块，但 CHANGELOG.md 里没有 "
                         "对应段落 ⇒ 跑 scripts/extract_changelog.py 归位：%s" % missing)

    def test_no_duplicate_version_sections(self):
        """同一版本不得有两个**纯版本号**的段落（抽取脚本的幂等性最终由这条兜住）。

        ⚠ 判据为什么只查「纯版本号」标题（第一版误报 7 处）：
        本仓 CHANGELOG 里有大量**分批发布**条目，标题形如
            ## v0.13.27 — 批3：三中心 UI 统一（…）
            ## v0.13.27 — 批2：运行日志前端页（…）
        同一个版本号出现多次是**正常且正确**的（一次改动分批上线，各批一条）。
        已核对：v0.13.26 / v0.13.27 在改动前的 HEAD 里就各有 6 条，不是本批引入。
        ⇒ 真正要拦的是「抽取脚本把同一段内容写了两遍」，那种情况会出现
        **两个完全相同**的纯版本号标题。
        """
        chlog = CHANGELOG.read_text(encoding="utf-8")
        seen, dup = set(), []
        for v in re.findall(r"^## (v[\d.]+)\s*$", chlog, re.M):   # 只匹配「纯版本号」行
            if v in seen:
                dup.append(v)
            seen.add(v)
        self.assertEqual([], dup,
                         "CHANGELOG.md 里这些版本出现了两个**纯版本号**段落"
                         "（分批发布的「## vX — 批N：…」标题不在此列）：%s" % dup)


class ReadmeCountsAreLabelled(unittest.TestCase):
    """`tests/README.md` 的分层表必须带**实测日期**。

    拦的是「表里的数字没来源」这个形态：数字本身拦不住（没有单一真源可对），
    但**「哪天实测的」可以拦** —— 没有日期就说明有人直接抄了旧数。
    """

    def test_layer_table_has_a_measurement_date(self):
        src = README_MD.read_text(encoding="utf-8")
        m = re.search(r"数量（([^）]+)实测）", src)
        self.assertIsNotNone(m,
                             "tests/README.md 的分层表表头缺「（YYYY-MM-DD 实测）」标注")
        self.assertRegex(m.group(1), r"\d{4}-\d{2}-\d{2}",
                         "表头的实测日期不是 ISO 格式：%r" % m.group(1))

    def test_readme_mentions_force_color_trap(self):
        """⚠ 这条不是洁癖：本机 shell 的 `FORCE_COLOR=3` 会让 22 条用例假红。

        直接调 `pytest`（而不是 `run_tests.sh`）的人一定会踩，且会误以为
        「代码坏了」或「测试本来就红」。已在 README 与 run_tier.py 各记一处。
        """
        src = README_MD.read_text(encoding="utf-8")
        self.assertIn("FORCE_COLOR", src,
                      "tests/README.md 未提 FORCE_COLOR 假红坑 ⇒ 后来人还会踩")


if __name__ == "__main__":
    unittest.main(verbosity=2)
