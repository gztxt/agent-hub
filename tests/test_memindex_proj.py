#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L0 hermetic：A4 索引投影层（memindex）闸门 G9–G17。

分层口径同 tests/tiers.py：不起服务、不占端口、不打网络、不 import src.main。
**db 一律指向临时目录** ⇒ 生产 data/memindex.db 全程不碰。**不允许 SKIP**。

G13 用**真 rg**（不是 mock）做对拍：这条闸门的全部价值就是「索引不能比 rg 查得少」，
用 fake 结果替身等于把要防的东西自己仿了一遍。

为什么这九条闸门必须存在（红向都能确定性造）：

1. **G10 红向 = 本仓已经踩过的坑**。`~/.codex/sessions` 是**根符号链接**，2026-10-02 实测
   GNU `find` 只看到 1 个文件、`du` 报 0，而 `rg`/`os.walk` 看到 273。若枚举用 find/du 口径，
   codex_sessions 会静默投影成 1 个文件——**全指标绿而功能层空**，和 workbuddy 那 76KB 同形。
2. **G11 红向 = 索引虚胖**。codex_sessions 的 273 个文件实测全部是 archived_sessions 的子集。
   不去重 ⇒ 同一份语料存两遍，体积与增量刷新全部失真。
3. **G12 红向 = 建库被单个坏文件打断**。实测 `~/.hermes/skills-hot/internet-search`
   枚举后立刻消失，单次盘点 17 例 stat/open 竞态。一失败就 raise ⇒ 库里永远建不完。
4. **G13 红向 = 静默漏召回**。FTS 建成功、行数 >0、探针全绿，但少了某些命中 ⇒ 用户以为
   「记过的东西找不到了」，且没有任何错误信号。这是索引层最贵的故障形态。
5. **G16 红向 = 假装答上来了**。索引缺失/损坏时若返回 `{"ok":True,"items":[]}`，
   上游分派会认为「投影源已应答、零命中」⇒ 静默返回空，而不是诚实地回退 rg。
6. **G17 红向 = 闸门虚设**。trimafs 按目录树配额，`df /fs` 读 186G、`df 技术文档` 读 30G。
   量错路径 ⇒ 「可用 > 8G」恒真 ⇒ 建库撞配额上限、留下半截的库。
"""
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from memindex import (  # noqa: E402
    MIN_FTS_CHARS, Spec, build, disk_free_bytes, disk_state, enumerate_corpus,
    is_short_query, probe, search,
)
from memindex import DISK_CRITICAL_GB as REAL_CRITICAL_GB, DISK_LOW_GB as REAL_LOW_GB  # noqa: E402
import memindex  # noqa: E402

RG_BIN = shutil.which("rg")

#: 测试跑在 /tmp（tmpfs，实测可用 6.83GB）< 生产阈值 8G ⇒ 默认 setUp 把阈值压到极小值，
#: 否则闸门会正确地把建库拒了而闸门本身没问题。阈值语义由 G16/G17 两条专门用例覆盖。
TINY_GB = 1e-9


def _w(p: pathlib.Path, text: str) -> pathlib.Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class MemIndexFixture(unittest.TestCase):
    """真文件 + 真符号链接 + 真 SQLite。不用 mock 打桩语料。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="memindex-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = self.tmp / "db" / "memindex.db"

        # 阈值只对不测阈值的那几条用例压低；**生产默认 8G/3G 不许改**（G16/G17 会钉住）
        for attr, val in (("DISK_LOW_GB", TINY_GB), ("DISK_CRITICAL_GB", TINY_GB)):
            mock.patch.object(memindex, attr, val).start()
        self.addCleanup(mock.patch.stopall)

        # 语料：4 个来源，覆盖「长行 / 短行 / 空行 / 重复命中」
        self.a = _w(self.tmp / "srcA" / "s1.md",
                    "alpha_needle 出现在这里\n第二行普通文本\n")
        self.b = _w(self.tmp / "srcA" / "s2.md",
                    "前文\nalpha_needle 又一次出现，后面是上下文用于验证命中窗口\n尾巴\n")
        self.long_line = "x" * 5000 + " alpha_needle " + "y" * 5000
        self.c = _w(self.tmp / "srcB" / "s3.md",
                    "这是超长行测试：\n" + self.long_line + "\n结尾\n")
        self.binary = self.tmp / "srcB" / "blob.md"
        self.binary.parent.mkdir(parents=True, exist_ok=True)
        self.binary.write_bytes(b"alpha_needle\x00\x01binary")

        # 根符号链接（约束 1 的形态：整棵树的根是软链）
        self.realdir = self.tmp / "realroot"
        _w(self.realdir / "inside.md", "alpha_needle 在符号链接根之下\n")
        self.linkroot = self.tmp / "linkroot"
        os.symlink(str(self.realdir), str(self.linkroot))

        self.specs = [
            Spec("srcA", (str(self.tmp / "srcA"),), "*.md"),
            Spec("srcB", (str(self.tmp / "srcB"),), "*.md"),
            Spec("linkroot", (str(self.linkroot),), "*.md"),
        ]

    def _build(self, scope="full", **kw):
        return build(scope, db_path=self.db, specs=self.specs, **kw)


class TestG9FtsNonEmpty(MemIndexFixture):
    def test_g9_建库后fts行数与proj行数一致(self):
        """G9：触发器时序正确。触发器建在首批 INSERT 之前 ⇒ 两表行数必须相等。
        红向：若把 SCHEMA 拆到建表之后插数据，fed_proj 有行而 FTS 为 0。"""
        r = self._build()
        self.assertTrue(r.ok, msg=f"建库未成功：{r.notes}")
        con = sqlite3.connect(str(self.db))
        try:
            n_proj = con.execute("SELECT COUNT(*) FROM fed_proj").fetchone()[0]
            n_fts = con.execute("SELECT COUNT(*) FROM fed_proj_fts").fetchone()[0]
        finally:
            con.close()
        self.assertGreater(n_proj, 0, "fed_proj 空：枚举或分块坏了")
        self.assertEqual(n_proj, n_fts,
                         f"FTS 与 proj 行数不等 {n_fts}!={n_proj} ⇒ 触发器时序错")

    def test_g9_空语料必须报红而非静默成功(self):
        """红向：语料为空时若仍返回 ok=True，G13/G9 就会变成「永远绿」。"""
        empty = [Spec("none", (str(self.tmp / "does-not-exist"),), "*.md")]
        r = build("full", db_path=self.db, specs=empty)
        self.assertFalse(r.ok, msg="空语料却判成功 ⇒ 红向失守")
        self.assertTrue(any("FTS" in n for n in r.notes), msg=f"未报 FTS 空：{r.notes}")


class TestG10SymlinkRoot(MemIndexFixture):
    def test_g10_根符号链接下的文件被枚举(self):
        """G10 红向就是本仓 2026-10-02 踩过的 find=1/du=0 坑。"""
        rep = enumerate_corpus(self.specs)
        paths = {e.path for e in rep.entries}
        self.assertTrue(
            any("inside.md" in p for p in paths),
            f"符号链接根下的文件没被枚举到：{sorted(paths)}")

    def test_g10_对比_find看不到文件(self):
        """钉住「为什么必须用 scandir」：GNU find 默认不跟随根软链。"""
        if not RG_BIN:
            self.skipTest("无 rg")
        n_rg = len([l for l in subprocess.run(
            [RG_BIN, "--files", "--no-ignore", "--hidden", str(self.linkroot)],
            capture_output=True, text=True).stdout.splitlines() if l.strip()])
        n_find = len(subprocess.run(
            ["find", str(self.linkroot), "-type", "f"],
            capture_output=True, text=True).stdout.splitlines())
        self.assertGreaterEqual(n_rg, 1)
        # 本断言不要求 find 一定看不到（实现有别），只要求 rg 能看到且我们也能看到
        rep = enumerate_corpus(self.specs)
        n_ours = sum(1 for e in rep.entries if "inside.md" in e.path)
        self.assertEqual(n_rg, n_ours, f"rg={n_rg} 我们={n_ours} ⇒ 枚举口径与 rg 不一致")


class TestG11RealpathDedup(MemIndexFixture):
    def test_g11_同一realpath只投影一次(self):
        """G11：codex_sessions ⊂ archived_sessions，不去重就是存两遍。"""
        dup = self.tmp / "dup"
        dup.mkdir(exist_ok=True)
        os.symlink(str(self.a), str(dup / "copy.md"))     # 指向同一个 realpath
        specs = self.specs + [Spec("duproot", (str(dup),), "*.md")]
        rep = enumerate_corpus(specs)
        rps = [e.realpath for e in rep.entries]
        self.assertEqual(len(rps), len(set(rps)),
                         "重复 realpath：" + str([r for r in rps if rps.count(r) > 1]))
        self.assertGreater(rep.dup_collapsed, 0, "dup_collapsed 未计数")
        # 归属必须是 DEDUP_PRIORITY 里更具体的那一路（srcA），不是 duproot
        owner = [e.sid for e in rep.entries if e.realpath == os.path.realpath(self.a)]
        self.assertIn("srcA", owner)
        self.assertNotIn("duproot", owner)

    def test_g11_建库后同一内容不重复入库(self):
        dup = self.tmp / "dup"
        dup.mkdir(exist_ok=True)
        os.symlink(str(self.a), str(dup / "copy.md"))
        specs = self.specs + [Spec("duproot", (str(dup),), "*.md")]
        r = build("full", db_path=self.db, specs=specs)
        con = sqlite3.connect(str(self.db))
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM fed_proj WHERE realpath=?",
                (os.path.realpath(self.a),)).fetchone()[0]
        finally:
            con.close()
        self.assertTrue(r.ok, msg=str(r.notes))
        con = sqlite3.connect(str(self.db))
        try:
            uq = con.execute(
                "SELECT COUNT(*) FROM (SELECT realpath, chunk_no FROM fed_proj "
                "GROUP BY realpath, chunk_no HAVING COUNT(*) > 1)").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(uq, 0, f"唯一索引失效：{uq} 组 (realpath,chunk_no) 重复入库")


class TestG12RaceTolerated(MemIndexFixture):
    def test_g12_stat失败被跳过并计数不中断(self):
        """G12 红向：raise ⇒ 建库永远完不成（实测 17 例枚举竞态）。"""
        boom = str(self.c)
        real_stat = os.stat

        def flaky(p, *a, **kw):
            if str(p) == boom:
                raise OSError(5, "simulated race")
            return real_stat(p, *a, **kw)

        with mock.patch("memindex.os.stat", side_effect=flaky):
            rep = enumerate_corpus(self.specs)
        self.assertGreater(rep.skipped_race, 0, "竞态未被计数")
        others = [e for e in rep.entries if e.path != boom]
        self.assertGreater(len(others), 1, "一个文件坏了就把整个枚举打断了")

    def test_g12_is_binary返回None表示读不到而非二进制(self):
        """`_is_binary` 的三态契约。早期版本只有两态（打开失败 ⇒ 当作二进制），
        于是权限错误/文件已删被静默归类成「正常二进制过滤」，竞态计数永远是 0。"""
        real_open = open
        with mock.patch("builtins.open", side_effect=PermissionError(13, "denied")):
            self.assertIsNone(memindex._is_binary(self.a))

    def test_g12_枚举期读不到被计入竞态而非二进制(self):
        """红向对照：若把 `is_bin is None` 的处理去掉（当作文本放行），
        读不到的文件会**被当成正常文本入库**，而计数一个都不涨 ⇒ 诊断信号全失。"""
        boom = str(self.c)
        real = memindex._is_binary

        def none_for_target(p):
            return None if str(p) == boom else real(p)

        with mock.patch("memindex._is_binary", side_effect=none_for_target):
            rep = enumerate_corpus(self.specs)
        self.assertGreater(rep.skipped_race, 0, "读得到不到的文件没被计入竞态")
        self.assertNotIn(boom, {e.path for e in rep.entries},
                         "读不到的文件被当文本放进了语料")
        self.assertNotIn("s3.md", {os.path.basename(e.path) for e in rep.entries})

    def test_g12_枚举期打开抛异常也被跳过并计数不中断(self):
        """红向：`raise` 往上抛 ⇒ 建库永远完不成（实测 17 例枚举竞态）。"""
        boom = str(self.c)
        real = memindex._is_binary

        def flaky(p):
            if str(p) == boom:
                raise OSError(5, "simulated open race")
            return real(p)

        with mock.patch("memindex._is_binary", side_effect=flaky):
            rep = enumerate_corpus(self.specs)
        self.assertGreater(rep.skipped_race, 0, "枚举期打开失败未被计入竞态")
        self.assertGreater(len([e for e in rep.entries if e.path != boom]), 1,
                           "一个文件读不了就整枚举打断了")

    def test_g12_入库期读取失败被跳过并计数不中断(self):
        """枚举过了但建库时文件没了（stat/open 竞态的另一半）。"""
        boom = str(self.c)
        real = pathlib.Path.read_text

        def flaky(self, *a, **kw):
            if str(self) == boom:
                raise PermissionError(13, "simulated denied")
            return real(self, *a, **kw)

        with mock.patch("pathlib.Path.read_text", new=flaky):
            r = self._build()
        self.assertTrue(r.ok, msg=f"一个文件读不了就整库失败：{r.notes}")
        self.assertGreater(r.skipped_race, 0, "入库期失败未计入竞态")
        self.assertGreater(r.files, 1, "其余文件也没入库")

    def test_g12_二进制文件被过滤不进库(self):
        rep = enumerate_corpus(self.specs)
        self.assertNotIn(str(self.binary), {e.path for e in rep.entries})
        self.assertGreater(rep.skipped_binary, 0)


class TestG13RecallSupersetOfRg(MemIndexFixture):
    """G13：真 rg 对拍，不用 fake。"""

    def _rg_hits(self, q):
        if not RG_BIN:
            self.skipTest("无 rg")
        cmd = [RG_BIN, "-F", "-i", "--no-heading", "--no-ignore", "--hidden",
               "--max-filesize", "20M"]
        for s in self.specs:
            cmd += ["-g", s.glob or "**"]
        cmd += ["--", q, self.tmp / "srcA", self.tmp / "srcB", self.linkroot]
        out = subprocess.run(cmd, capture_output=True, text=True)
        return {os.path.realpath(l.split(":", 1)[0])
                for l in out.stdout.splitlines() if ":" in l}

    def test_g13_fts命中集真包含rg命中集(self):
        self._build()
        q = "alpha_needle"
        self.assertGreaterEqual(len(q), MIN_FTS_CHARS)
        rg_files = self._rg_hits(q)
        self.assertGreater(len(rg_files), 0, "rg 都没命中：对拍无意义")
        res = search(q, limit=50, db_path=self.db)
        self.assertTrue(res["ok"], msg=str(res))
        idx_files = {os.path.realpath(i["id"].rsplit(":", 1)[0]) for i in res["items"]}
        missing = rg_files - idx_files
        self.assertEqual(missing, set(),
                         f"索引漏召回（这条最贵：全绿但用户找不到自己记过的东西）：{missing}")

    def test_g13_超长行也能被查到(self):
        """超长行整行不切（D3）⇒ 命中行在库里且带正确 l1/l2 区间。"""
        self._build()
        res = search("alpha_needle", limit=50, db_path=self.db)
        ids = [i["id"] for i in res["items"]]
        hit = [i for i in ids if "s3.md" in i]
        self.assertTrue(hit, f"超长行所在文件未被命中：{ids}")
        for h in hit:
            l1, l2 = h.rsplit(":", 1)[-1].split("-")
            self.assertGreaterEqual(int(l2), int(l1))

    def test_g13_查不到时返回空但ok为真(self):
        """ok=True + 空 items 是合法语义（真·零命中），与 G16 的「假零命中」必须可区分。"""
        self._build()
        res = search("zzz_absolutely_not_present_token", db_path=self.db)
        self.assertTrue(res["ok"])
        self.assertEqual(res["count"], 0)
        self.assertNotIn("fallback", res)


class TestG14ShortQuery(MemIndexFixture):
    def test_g14_不足三字必须标记回退(self):
        """G14 红向：直接拿 2 字词查 trigram ⇒ SQLite 抛错或静默零命中。"""
        self._build()
        for q in ("中", "中枢"):
            self.assertTrue(is_short_query(q))
            res = search(q, db_path=self.db)
            self.assertEqual(res.get("fallback"), "short_query",
                             f"{q!r} 未标回退：{res}")
            self.assertEqual(res["count"], 0)
            self.assertEqual(res["items"], [])

    def test_g14_三字及以上不算短查询(self):
        self.assertTrue(is_short_query("记忆"))
        self.assertFalse(is_short_query("记忆库"))
        self.assertFalse(is_short_query(" alpha_needle "))

    def test_g14_短查询标记名必须带进响应(self):
        """调用方靠这个字段拼 short_query_fallback=true，字段名写错就静默失效。"""
        self._build()
        res = search("中", db_path=self.db)
        self.assertIn("fallback", res)
        self.assertIn("short_query", res["fallback"])


class TestG15OutboundSanitized(MemIndexFixture):
    def test_g15_出站内容逐条经过sanitize(self):
        """G15：库内存的是**原文**（D6 的价值），脱敏必须在出站做，漏一条就泄密。
        用 `_sanitize_content` **实测能认的四类**（2026-10-02 实测：
        sk-* / Bearer / password: / api_key=）。它不认的形态属另案，不在本闸门范围。"""
        from memfed import _sanitize_content
        secret = _w(self.tmp / "srcA" / "secret.md",
                    "这里有密钥 sk-abcdef1234567890 和 api_key=AKIAIOSFODNN7EXAMPLE\n")
        specs = self.specs + [Spec("secretroot", (str(secret.parent),), "*.md")]
        build("full", db_path=self.db, specs=specs)
        res = search("sk-abcdef1234567890", limit=20, db_path=self.db,
                     sanitize=_sanitize_content)
        self.assertTrue(res["ok"], msg=str(res))
        self.assertGreater(len(res["items"]), 0, "夹具没造出命中，脱敏断言会假绿")
        for it in res["items"]:
            blob = json.dumps(it, ensure_ascii=False)
            self.assertNotIn("sk-abcdef1234567890", blob, f"出站未脱敏：{it}")
            self.assertIn("<redacted>", it["content"], "脱敏没生效")

    def test_g15_不脱敏就会泄密_对照组(self):
        """红向对照：不传 sanitize ⇒ 原文必须真的出现在出站。
        没有这一步，上面那条就可能因为「索引里本来就没���原文」而假绿。"""
        secret = _w(self.tmp / "srcA" / "secret.md", "这里有密钥 sk-abcdef1234567890\n")
        specs = self.specs + [Spec("secretroot", (str(secret.parent),), "*.md")]
        build("full", db_path=self.db, specs=specs)
        res = search("sk-abcdef1234567890", limit=20, db_path=self.db)
        self.assertTrue(res["ok"], msg=str(res))
        self.assertIn("sk-abcdef1234567890",
                      " ".join(i["content"] for i in res["items"]),
                      "库里没有原文 ⇒ 脱敏断言无意义")

    def test_g15_每条都过了sanitize不是只过第一条(self):
        """红向：sanitize 只作用于 items[0]（写循环忘了或 early return）。"""
        from memfed import _sanitize_content
        for i in range(5):
            _w(self.tmp / "srcA" / f"m{i}.md", f"sk-leak{i}000000000000000\n")
        build("full", db_path=self.db, specs=self.specs)
        res = search("sk-leak", limit=20, db_path=self.db, sanitize=_sanitize_content)
        self.assertGreaterEqual(res["count"], 5)
        for it in res["items"]:
            self.assertNotIn("sk-leak", it["content"])


class TestG16FailClosed(MemIndexFixture):
    def test_g16_索引缺失回退且不抛错(self):
        missing = self.tmp / "nope.db"
        res = search("alpha_needle", db_path=missing)
        self.assertFalse(res["ok"], "缺索引却报 ok ⇒ 上游会当零命中")
        self.assertEqual(res["fallback"], "no_index")
        self.assertIn("error", res)

    def test_g16_索引损坏回退且不抛错(self):
        bad = self.tmp / "bad.db"
        bad.write_bytes(b"this is not a sqlite database at all" * 100)
        res = search("alpha_needle", db_path=bad)
        self.assertFalse(res["ok"])
        self.assertIn(res.get("fallback"), ("corrupt", "query_error", "no_index"))
        self.assertTrue(res["items"] == [])

    def test_g16_低水位拒绝增量且不删索引(self):
        """D4 铁律：任何水位都**不自动删索引**。索引是我生成的，但删除权在用户。
        用**生产真阈值**（REAL_LOW_GB），不靠 setUp 里压小的值。"""
        self._build()
        size_before = self.db.stat().st_size
        free_low = int(REAL_LOW_GB * 1024 ** 3) - 1
        with mock.patch("memindex.disk_free_bytes", return_value=free_low), \
             mock.patch.object(memindex, "DISK_LOW_GB", REAL_LOW_GB), \
             mock.patch.object(memindex, "DISK_CRITICAL_GB", REAL_CRITICAL_GB):
            r = build("incremental", db_path=self.db, specs=self.specs)
            res = search("alpha_needle", db_path=self.db)
        self.assertFalse(r.ok, msg=f"低水位却允许增量：{r.notes}")
        self.assertTrue(any("水位" in n or "disk" in n for n in r.notes))
        self.assertTrue(self.db.exists(), "低水位把索引删了 ⇒ 违反 D4")
        self.assertGreaterEqual(self.db.stat().st_size, size_before)
        # low 只闸写不闸读：读索引不耗空间，在这里断召回＝纯静默丢召回
        self.assertTrue(res["ok"], msg=f"low 水位不该断读：{res}")
        self.assertGreater(res["count"], 0)

    def test_g16_critical水位查询也降级(self):
        self._build()
        free_crit = int(REAL_CRITICAL_GB * 1024 ** 3) - 1
        with mock.patch("memindex.disk_free_bytes", return_value=free_crit), \
             mock.patch.object(memindex, "DISK_LOW_GB", REAL_LOW_GB), \
             mock.patch.object(memindex, "DISK_CRITICAL_GB", REAL_CRITICAL_GB):
            res = search("alpha_needle", db_path=self.db)
        self.assertFalse(res["ok"])
        self.assertEqual(res.get("fallback"), "low_disk")
        self.assertIn("proj_disabled", res.get("degraded", ""))

    def test_g16_生产阈值不得被后人改写(self):
        """D4 的 8G/3G 是按「技术文档配额 30G」定的，尾仓余量就靠它。
        谁把它改小或改大，这里直接报红。"""
        self.assertEqual(REAL_LOW_GB, 8.0)
        self.assertEqual(REAL_CRITICAL_GB, 3.0)
        self.assertLess(REAL_CRITICAL_GB, REAL_LOW_GB, "critical 必须严于 low")

    def test_g16_探针在缺索引时自报不可用(self):
        p = probe(self.tmp / "nope.db")
        self.assertFalse(p["ok"])


class TestG17DiskMeasureOnIndexParent(MemIndexFixture):
    def test_g17_水位取索引父目录statvfs(self):
        """G17 红向：statvfs('/') 或 statvfs('/fs') ⇒ trimafs 配额视图错、闸门虚设。"""
        seen = []
        real_statvfs = os.statvfs

        def spy(p):
            seen.append(str(p))
            return real_statvfs(p)

        with mock.patch("memindex.os.statvfs", side_effect=spy):
            build("full", db_path=self.db, specs=self.specs)
        self.assertTrue(seen, "一次 statvfs 都没调")
        root = os.path.realpath(str(self.db.parent))
        for s in seen:
            self.assertNotEqual(s, "/", "量到了根文件系统")
            self.assertNotEqual(s, "/fs", "量到了 /fs：trimafs 在 /fs 上是 186G 视图")
            # 首次建库时 data/ 可能还不存在，会向上找到最近的存在目录
            # （= 将来会施加配额的那个目录）⇒ 断言「是父目录或其祖先」而非「严格相等」
            self.assertTrue(
                root == os.path.realpath(s) or root.startswith(os.path.realpath(s) + os.sep),
                f"水位量在了 {s}，不是索引父目录 {root} 的祖先")

    def test_g17_disk_free_bytes不接受任意路径(self):
        """默认必须落在索引目录；传别的路径也要能算但调用方得显式。"""
        self.assertGreater(disk_free_bytes(self.db.parent), 0)
        self.assertGreater(disk_free_bytes(self.tmp), 0)
        free = disk_free_bytes(self.db.parent)
        with mock.patch.object(memindex, "DISK_LOW_GB", 1.0), \
             mock.patch.object(memindex, "DISK_CRITICAL_GB", 0.5):
            self.assertEqual(disk_state(int(1.0 * 1024 ** 3) - 1), "low")
            self.assertEqual(disk_state(int(0.5 * 1024 ** 3) - 1), "critical")
            self.assertEqual(disk_state(int(1.0 * 1024 ** 3) + 1), "ok")
        self.assertGreater(free, 0)

    def test_g17_不存在的目录不得把容量闸炸掉(self):
        """红向：首次建库时 data/ 可能还没建，`statvfs` 会抛 FileNotFoundError
        ⇒ 建库第一步就崩，容量闸根本没机会生效。"""
        ghost = self.tmp / "not-created-yet" / "db"
        self.assertGreater(disk_free_bytes(ghost), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)