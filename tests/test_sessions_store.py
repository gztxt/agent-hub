"""sessions_store 单测 —— 已分层（口径见 tests/tiers.py 与 tests/README.md）：
   L0 hermetic = 只依赖纯函数/tempfile，干净机器（含 CI runner）上结论必须一致；
   L1 host     = 断了本机真实仓库形态（~/.grok ~/.claude ~/.jcode ~/.qoder ~/.hermes
                 ~/.codex 与 /fs 真目录），换机显式 SKIP + 理由，绝不静默通过。
   跑法：
     bash scripts/run_tests.sh hermetic   # 只跑 L0，以零跳过为闸
     bash scripts/run_tests.sh all        # L0 + L1
     venv/bin/python -m unittest tests.test_sessions_store -v
   历史：本文件原口径是「真磁盘只读断言（无网络、无进程、无 mock）」—— 口径不变，
   只是把离不开本机的那部分显式标出来，不让它们冒充全绿。"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tiers.py
import tiers                                   # noqa: E402
from src import sessions_store as ss      # noqa: E402

CWD = "/fs/1000/ftp/技术文档"


def _first_live(items):
    """取第一条「记录里的目录现在还在盘上」的历史条目，没有则 None。

    本机历史里会混进指向临时目录的会话：子代理在 /tmp 影子树上起过真 pty，
    那些 claude/grok/qoder/jcode 会话被各自 app 落进了自己的 sessions 目录，
    而影子树随后被清理掉。此时 session_cwd() 的**正确**行为是回退 fallback，
    而不是等于记录里那个已死的目录 —— 原先三个用例无条件比等于，与同文件
    test_missing_recorded_dir_falls_back 断言的回退分支直接矛盾（09-23 实测
    被一次正常清理踩爆 6 例）。
    """
    return next((i for i in items if i.get("cwd") and Path(i["cwd"]).is_dir()), None)
# 【偏差 D3】计划这一例写错了 cwd：7491a32f… 实测只存在于
# ~/.qoder/projects/-home-gztxt-agent-hub/7491a32f-….jsonl（cwd=/home/gztxt/agent-hub），
# 而 CWD 目录下 qoder 实测 0 条 *.jsonl（仅 4 个无正文骨架）⇒ known_ids("qoder", CWD) 为空，
# 与同文件的 test_qoder_empty_state_has_note 在计划里直接互斥（双校验必拒 CWD 那份）。
# 处理：按真实 cwd 断言，不放宽 resume_argv「id_re + 实盘存在」这道安全闸。
QODER_CWD = "/home/gztxt/agent-hub"


class TestMask(unittest.TestCase):
    def test_kv_secret_masked(self):
        out = ss.mask_title("入口 ?ccr_web_token=ccr-web-fixed-token-gztxt-2026 打不开")
        self.assertIn("<masked>", out)
        self.assertNotIn("ccr-web-fixed-token-gztxt-2026", out)

    def test_uuid_title_not_mangled(self):
        t = "排查 session 01a0c91d-eb84-7130-9767-479821ef336c 无响应"
        self.assertNotIn("<masked>", ss.mask_title(t))      # 含 '-' 的 UUID 刻意不糊（避免误伤）

    def test_plain_chinese_untouched(self):
        t = "agent hub 宽屏 左侧菜单 1.不要有滚动条"
        self.assertEqual(ss.mask_title(t), t)

    def test_title_length_capped(self):
        self.assertLessEqual(len(ss.mask_title("长" * 400)), 120)


class TestTable(unittest.TestCase):
    def test_only_seven_agents(self):
        self.assertEqual(set(ss.SESSION_STORES), {"grok", "claude", "qoder", "jcode", "hermes", "codex", "opencode"})

    def test_hist_agents_front_back_same_set(self):
        """hub.js 的 TERM_HIST_AGENTS 与后端 SESSION_STORES 必须同集合——
           注释里写着"同集合"却无人核对，opencode 就是漏这刀漏出来的。"""
        hub = (tiers.repo_root() / "static" / "hub.js").read_text(encoding="utf-8")
        m = re.search(r"const TERM_HIST_AGENTS = \[([^\]]*)\]", hub)
        self.assertIsNotNone(m, "hub.js 里找不到 TERM_HIST_AGENTS")
        front = {x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()}
        self.assertEqual(front, set(ss.SESSION_STORES), "前后端历史白名单漂移")

    def test_supports_negative(self):
        for a in ("pi", "shell", "qwenpaw", "ccr"):
            self.assertFalse(ss.supports(a))

    def test_id_regexes(self):
        st = ss.SESSION_STORES
        self.assertTrue(st["jcode"]["id_re"].match("session_seedling_1790072132294_70768ccd3fd9f542"))
        self.assertFalse(st["jcode"]["id_re"].match("sheep"))            # 动物名不唯一，禁用
        self.assertTrue(st["hermes"]["id_re"].match("20260921_221812_fa76f1"))
        self.assertTrue(st["grok"]["id_re"].match("01a0c91d-eb84-7130-9767-479821ef336c"))
        self.assertFalse(st["grok"]["id_re"].match("01a0c91d-eb84-7130-9767-479821ef336c; rm -rf /"))


class TestResumeArgv(unittest.TestCase):
    @tiers.host_only          # 需要盘上真 id 才能断言（L1）
    def test_good_ids(self):
        self.assertEqual(ss.resume_argv("grok", "01a0c91d-eb84-7130-9767-479821ef336c", CWD),
                         ["grok", "--resume", "01a0c91d-eb84-7130-9767-479821ef336c"])
        self.assertEqual(ss.resume_argv("qoder", "7491a32f-ccfb-4602-bd84-22c521fd45ee", QODER_CWD),
                         ["qodercli", "-w", QODER_CWD, "-r", "7491a32f-ccfb-4602-bd84-22c521fd45ee"])
        self.assertEqual(ss.resume_argv("codex", "01a0bffd-7df4-7692-953d-210230d73610", CWD),
                         ["codex", "resume", "01a0bffd-7df4-7692-953d-210230d73610"])

    def test_injection_rejected(self):
        for bad in ["x; rm -rf /", "--resume=evil", "$(id)", "../etc/passwd", "a" * 300, ""]:
            with self.assertRaises(ValueError):
                ss.resume_argv("grok", bad, CWD)

    def test_unsupported_agent(self):
        with self.assertRaises(ValueError):
            ss.resume_argv("pi", "01a0c91d-eb84-7130-9767-479821ef336c", CWD)


@tiers.host_only              # 断言本机六家仓库的真实形态 ⇒ 换机不可判定（L1）
class TestRealStores(unittest.TestCase):
    """把 spec §2 取证矩阵的数字当断言：磁盘形态变了就 FAIL，这正是我们要的信号。"""

    def test_grok_history_has_cn_title(self):
        d = ss.list_history("grok", CWD, 3)
        self.assertTrue(d["items"], "grok 在 技术文档 实测 88 条，不应为空")
        it = d["items"][0]
        self.assertLessEqual(len(d["items"]), 3)
        self.assertTrue(re.search(r"[\u4e00-\u9fff]", it["title"]), f"标题应含中文：{it}")
        self.assertNotRegex(it["title"], r"\A[0-9a-f]{4}", "标题不得是字母编号")
        self.assertIsInstance(it["ts"], int)
        self.assertTrue(it["id"])

    def test_claude_jcode_hermes_nonempty(self):
        for agent, cwd in (("claude", CWD), ("jcode", CWD), ("hermes", "/home/gztxt")):
            with self.subTest(agent=agent):
                self.assertTrue(ss.list_history(agent, cwd, 3)["items"])

    def test_sorted_desc_and_capped(self):
        items = ss.list_history("grok", CWD, 3)["items"]
        ts = [i["ts"] for i in items]
        self.assertEqual(ts, sorted(ts, reverse=True))

    def test_codex_cli_only(self):
        d = ss.list_history("codex", CWD, 3)
        self.assertLessEqual(len(d["items"]), 3)            # 实测 1 条；0 也合法但须给 note
        if not d["items"]:
            self.assertTrue(d["note"])

    def test_hermes_note_declares_scope(self):
        d = ss.list_history("hermes", "/home/gztxt", 3)
        self.assertTrue(d["note"], "hermes 不按 cwd 过滤，note 必须写明口径")

    def test_opencode_history_from_sqlite(self):
        d = ss.list_history("opencode", CWD, 3)
        self.assertTrue(d["items"], "opencode 实测有可续会话（1.18.32 opencode.db），不应为空")
        it = d["items"][0]
        self.assertRegex(it["id"], r"\Ases_")
        self.assertNotRegex(it["title"], r"\ANew session - ", "零消息占位会话不该进历史")
        argv = ss.resume_argv("opencode", it["id"], CWD)
        self.assertEqual(argv, ["opencode", "--session", it["id"]])
        self.assertTrue(ss.title_for("opencode", it["id"]), "title_for 必须能按 id 直查")

    def test_items_carry_their_own_cwd(self):
        """跳目录之后，条目若不带自己的 cwd，前端就无从标出「这条来自哪个工程」"""
        seen_other = False
        for a in ("grok", "claude", "jcode", "qoder", "hermes", "codex"):
            for i in ss.list_history(a, CWD, 5)["items"]:
                with self.subTest(agent=a, sid=i["id"][:10]):
                    self.assertIsInstance(i["cwd"], str)
                if i["cwd"] and i["cwd"] != CWD:
                    seen_other = True
        self.assertTrue(seen_other, "跨目录必须真命中别的目录的条目，否则本次裁定没落地")

    def test_missing_dir_degrades_not_raises(self):
        # 跳目录口径（用户 09-22 裁定）之后，"画像目录不存在"不再等于"无历史"：
        # 全局最近条目照给，传入的 cwd 只用于画像目录标注。此例断言的是「不许抛」。
        d = ss.list_history("grok", "/no/such/dir", 3)
        self.assertIsInstance(d["items"], list)
        for i in d["items"]:
            self.assertEqual({"agent", "id", "title", "ts", "cwd"} - set(i), set(), "条目字段不齐")

    def test_unknown_agent_degrades(self):
        self.assertEqual(ss.list_history("pi", CWD, 3)["items"], [])

    def test_sqlite_readonly_mtime_unchanged(self):
        for agent, p in (("hermes", Path.home() / ".hermes/state.db"),
                         ("codex", Path.home() / ".codex/state_5.sqlite"),
                         ("opencode", Path.home() / ".local/share/opencode/opencode.db")):
            if not p.exists():
                self.skipTest(f"{p} 不存在")
            before = p.stat().st_mtime_ns
            ss.list_history(agent, CWD, 3)
            self.assertEqual(before, p.stat().st_mtime_ns)

    def test_known_ids_covers_history(self):
        ids = ss.known_ids("grok", CWD)
        for it in ss.list_history("grok", CWD, 3)["items"]:
            self.assertIn(it["id"], ids)

    def test_cache_returns_same_object(self):
        self.assertIs(ss.list_history("grok", CWD, 3), ss.list_history("grok", CWD, 3))


class TestLiveTitles(unittest.TestCase):
    def test_shape(self):
        for agent in ("grok", "claude", "hermes", "jcode", "codex", "qoder", "pi"):
            with self.subTest(agent=agent):
                m = ss.live_titles(agent)
                self.assertIsInstance(m, dict)
                for k, v in m.items():
                    self.assertIsInstance(k, int)
                    self.assertIsInstance(v, str)


@tiers.host_only              # 三个用例里两个要真盘样本 ⇒ 整类标 L1，宁可少跑不谎报
class TestTitleFor(unittest.TestCase):
    """续聊会话的顶栏标题兜底：live_titles() 靠 pid 反查，实测 jcode 只在退出时写 last_pid、
       codex/qoder 压根没有 pid→会话 映射 ⇒ 只靠它会回退成 sid 前缀（正是本次要消除的东西）。"""

    def test_all_six_resolve_by_id(self):
        for agent, cwd in (("grok", CWD), ("claude", CWD), ("jcode", CWD),
                           ("hermes", str(Path.home())), ("codex", CWD), ("qoder", QODER_CWD)):
            with self.subTest(agent=agent):
                items = ss.list_history(agent, cwd, 1)["items"]
                if not items:
                    self.skipTest(f"{agent} 该目录无历史")
                t = ss.title_for(agent, items[0]["id"], cwd)
                self.assertTrue(t, f"{agent} title_for 返回空")
                self.assertTrue(re.search(r"[\u4e00-\u9fffA-Za-z]", t), f"{agent} 标题无实义：{t!r}")
                self.assertNotEqual(t, items[0]["id"][:8], "标题退化成 sid 前缀")

    def test_bad_shape_id_rejected(self):
        for bad in ("../../etc/passwd", "a;rm -rf", "", "x" * 300):
            with self.subTest(bad=bad):
                self.assertEqual(ss.title_for("grok", bad), "")

    def test_unknown_agent_returns_empty(self):
        self.assertEqual(ss.title_for("pi", "01a0c914-8f95-78e2-8b0f-123456789abc"), "")


@tiers.host_only              # 依赖本机 jcode 平铺目录的真实条数（L1）
class TestJcodeWindow(unittest.TestCase):
    """09-22 实测缺陷回归：jcode 仓库是平铺目录、混着所有 cwd。早期实现只扫「最新 20 个
       文件」再按 cwd 过滤 —— 当日 10 条探针（cwd=/tmp、/home/gztxt/agent-hub）把窗口占满，
       技术文档目录下实有 89 条却只剩 1 条可见，前端看着像漏读。"""

    def test_limit_is_actually_filled(self):
        """产品默认 5 条（HIST_LIMIT）：不得再出现"只要 5 条却给 1 条"的静默缺量"""
        d = ss.list_history("jcode", CWD, 5)
        self.assertEqual(len(d["items"]), 5, "limit=5 必须填满")
        d8 = ss.list_history("jcode", CWD, 8)
        self.assertGreaterEqual(len(d8["items"]), 8, "放宽 limit 应真拿到更多，而不是被窗口卡住")
        ids = [i["id"] for i in d8["items"]]
        self.assertEqual(len(ids), len(set(ids)), "不得出现重复条目")
        ts = [i["ts"] for i in d8["items"]]
        self.assertEqual(ts, sorted(ts, reverse=True), "必须按时间倒序（跨目录之后仍要单调）")

    def test_session_cwd_matches_record(self):
        """session_cwd() 是 pty 起目录与 qoder -w 的唯一来源，必须与条目自带 cwd 一致，
           且绝不返回一个不存在的目录（否则 pty 直接起不来）"""
        for a in ("grok", "claude", "jcode", "qoder"):
            items = ss.list_history(a, CWD, 5)["items"]
            if not items:
                continue
            i = _first_live(items) or items[0]
            with self.subTest(agent=a):
                c = ss.session_cwd(a, i["id"], "FALLBACK")
                if Path(i["cwd"]).is_dir():
                    self.assertEqual(c, i["cwd"], "反查与会话记录不一致")
                    self.assertTrue(Path(c).is_dir(), f"返回了不存在的目录：{c}")
                else:
                    self.assertEqual(c, "FALLBACK",
                                     f"记录目录 {i['cwd']} 已消失时必须回退，不得吐死路径")
                    # 回退分支的"绝不返回不存在目录"必须拿**真存在的 fallback** 验，
                    # 拿字面量 "FALLBACK" 去 is_dir() 是在断一个无意义的命题
                    real = ss.session_cwd(a, i["id"], CWD)
                    self.assertEqual(real, CWD, "回退时必须给调用方给的 fallback")
                    self.assertTrue(Path(real).is_dir(), f"回退结果不在盘上：{real}")


@tiers.host_only              # 依赖真盘；同一条「形状合法但盘上没有必须拒」在
                              # tests/test_term_launch_guard.py::TestResumeArgvHostile 已有 L0 版
class TestResumeExists(unittest.TestCase):
    """续聊存在性校验必须按仓库结构直查，不能走被 limit 截断的展示清单"""

    def test_old_session_beyond_top20_resumable(self):
        root = Path.home() / ".jcode" / "sessions"

        def wd(p):
            try:
                return json.load(open(p, errors="ignore")).get("working_dir")
            except Exception:  # noqa: BLE001
                return None
        cands = [p for p in sorted(root.glob("session_*.json"),
                                   key=lambda x: x.stat().st_mtime, reverse=True) if wd(p) == CWD]
        if len(cands) <= 25:
            self.skipTest(f"该目录仅 {len(cands)} 条，样本不足")
        old = cands[-5].stem
        self.assertNotIn(old, ss.known_ids("jcode", CWD), "前提：它确实不在 20 条展示清单里")
        self.assertEqual(ss.resume_argv("jcode", old, CWD), ["jcode", "--resume", old])

    def test_qoder_argv_uses_session_cwd(self):
        """qoder 是唯一把目录写进 argv 的（-w）：跳目录后必须给会话自己的目录，
           拿画像 cwd 硬套等于「在技术文档里打开 agent-hub 的工程」"""
        items = ss.list_history("qoder", CWD, 5)["items"]
        if not items:
            self.skipTest("qoder 无可续条目")
        it = _first_live(items) or items[0]
        argv = ss.resume_argv("qoder", it["id"], CWD)
        self.assertEqual(argv[0], "qodercli")
        self.assertEqual(argv[argv.index("-w") + 1],
                         it["cwd"] if Path(it["cwd"]).is_dir() else CWD,
                         "-w 必须是会话自己的目录（其目录已消失时给 fallback，即调用方 CWD）")
        self.assertEqual(argv[-2:], ["-r", it["id"]])

    def test_missing_recorded_dir_falls_back(self):
        """记录目录已被删时必须退回 fallback——否则 pty 起在不存在的目录里直接死，
           而这条分支在真仓库里没有自然样本（探针只能 SKIP），故用 mock 钉住。"""
        from unittest import mock
        items = ss.list_history("jcode", CWD, 5)["items"]
        if not items:
            self.skipTest("jcode 无可续条目")
        live = _first_live(items)
        it = live or items[0]
        sid, want = it["id"], it["cwd"]
        self.assertTrue(want, "样本需自带目录")
        with mock.patch.object(Path, "is_dir", return_value=False):
            self.assertEqual(ss.session_cwd("jcode", sid, "FALLBACK"), "FALLBACK")
        if live:
            self.assertEqual(ss.session_cwd("jcode", sid, "FALLBACK"), want, "未打桩时必须给真目录")
        else:
            # 盘上没有活目录样本时，本例仍钉住硬约束：绝不返回不存在的路径
            self.assertEqual(ss.session_cwd("jcode", sid, "FALLBACK"), "FALLBACK",
                             f"记录目录 {want} 已消失时必须回退")

    def test_absent_id_still_rejected(self):
        with self.assertRaises(ValueError):
            ss.resume_argv("jcode", "session_zzzz_1790000000000_0000000000000000", CWD)
        with self.assertRaises(ValueError):
            ss.resume_argv("grok", "00000000-0000-4000-8000-000000000000", CWD)
        with self.assertRaises(ValueError):
            ss.resume_argv("codex", "00000000-0000-4000-8000-000000000000", CWD)
