"""sessions_store 单测：真磁盘只读断言（无网络、无进程、无 mock）。
   跑法：cd ~/agent-hub && venv/bin/python -m unittest tests.test_sessions_store -v"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import sessions_store as ss      # noqa: E402

CWD = "/fs/1000/ftp/技术文档"
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
    def test_only_six_agents(self):
        self.assertEqual(set(ss.SESSION_STORES), {"grok", "claude", "qoder", "jcode", "hermes", "codex"})

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

    def test_qoder_empty_state_has_note(self):
        d = ss.list_history("qoder", CWD, 3)
        self.assertEqual(d["items"], [], "qoder 该目录实测 0 条可续")
        self.assertTrue(d["note"], "空态必须给中文说明，不得留白")

    def test_missing_dir_degrades_not_raises(self):
        d = ss.list_history("grok", "/no/such/dir", 3)
        self.assertEqual(d["items"], [])
        self.assertTrue(d["note"])

    def test_unknown_agent_degrades(self):
        self.assertEqual(ss.list_history("pi", CWD, 3)["items"], [])

    def test_sqlite_readonly_mtime_unchanged(self):
        for agent, p in (("hermes", Path.home() / ".hermes/state.db"),
                         ("codex", Path.home() / ".codex/state_5.sqlite")):
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


class TestJcodeWindow(unittest.TestCase):
    """09-22 实测缺陷回归：jcode 仓库是平铺目录、混着所有 cwd。早期实现只扫「最新 20 个
       文件」再按 cwd 过滤 —— 当日 10 条探针（cwd=/tmp、/home/gztxt/agent-hub）把窗口占满，
       技术文档目录下实有 89 条却只剩 1 条可见，前端看着像漏读。"""

    def test_limit_is_actually_filled(self):
        d = ss.list_history("jcode", CWD, 3)
        self.assertEqual(len(d["items"]), 3, "limit=3 必须填满（该目录实测 89 条）")
        d8 = ss.list_history("jcode", CWD, 8)
        self.assertGreaterEqual(len(d8["items"]), 8, "放宽 limit 应真拿到更多，而不是被窗口卡住")
        ids = [i["id"] for i in d8["items"]]
        self.assertEqual(len(ids), len(set(ids)), "不得出现重复条目")

    def test_fast_wd_equals_json_load(self):
        """_fast_wd() 是过滤的唯一依据，抠出来的 cwd 必须与整份解析逐字一致"""
        root = Path.home() / ".jcode" / "sessions"
        n = 0
        for p in sorted(root.glob("session_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:25]:
            try:
                d = json.load(open(p, errors="ignore"))
            except Exception:  # noqa: BLE001
                continue
            self.assertEqual(ss._fast_wd(p), d.get("working_dir"), p.name)
            n += 1
        self.assertGreater(n, 10, "样本太少，断言无意义")


class TestResumeExists(unittest.TestCase):
    """续聊存在性校验必须按仓库结构直查，不能走被 limit 截断的展示清单"""

    def test_old_session_beyond_top20_resumable(self):
        root = Path.home() / ".jcode" / "sessions"
        cands = [p for p in sorted(root.glob("session_*.json"),
                                   key=lambda x: x.stat().st_mtime, reverse=True)
                 if ss._fast_wd(p) == CWD]
        if len(cands) <= 25:
            self.skipTest(f"该目录仅 {len(cands)} 条，样本不足")
        old = cands[-5].stem
        self.assertNotIn(old, ss.known_ids("jcode", CWD), "前提：它确实不在 20 条展示清单里")
        self.assertEqual(ss.resume_argv("jcode", old, CWD), ["jcode", "--resume", old])

    def test_absent_id_still_rejected(self):
        with self.assertRaises(ValueError):
            ss.resume_argv("jcode", "session_zzzz_1790000000000_0000000000000000", CWD)
        with self.assertRaises(ValueError):
            ss.resume_argv("grok", "00000000-0000-4000-8000-000000000000", CWD)
        with self.assertRaises(ValueError):
            ss.resume_argv("codex", "00000000-0000-4000-8000-000000000000", CWD)
