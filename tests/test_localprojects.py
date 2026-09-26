#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L0 hermetic：本机项目清单（src/localprojects.py + 前端页面四件套）。

夹具：tmp 根目录里手工造 .git 目录（普通仓库 / git worktree 的 .git 文件 /
深度超限 / node_modules 内 / 点目录内），cloudcli.AUTH_DB 换到 tmp 夹具 db。
零宿主依赖：不起服务、不 import src.main、不 spawn git。

为什么这些用例必须存在：
1. **扫描剪枝**：node_modules/venv/.隐藏目录里的 .git 不是用户项目——全收会把
   依赖山当项目列表刷屏（每仓一条 ⇒ 数百条噪声）。
2. **缺根不静默**：根不存在必须进 errors（「查不了」≠「没有」四态口径，
   与 cloudcli._list_projects 同型）。
3. **双源合并**：git 与 cloudcli 各有半张图（cloudcli 有精确名/会话数，git 覆盖
   从没在 cloudcli 开过会话的目录）——去重 + cloudcli 精确名胜出是本页的名称口径。
4. **降级解耦**：cloudcli 库读不到 ⇒ git 部分照常 + errors 点名，不许整页塌掉。
5. **前端四件套**：section / 侧栏按钮（必须位于 #navTree 之上——用户点名
   「在 agents 菜单上面」）/ PAGE_LABELS / go() 懒加载钩子，缺一即静默退回总览
   或页面空白（test_kb_frontend_pages 同型判据）。
"""
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

import cloudcli                     # noqa: E402
import localprojects as lp          # noqa: E402

os.environ.setdefault("TERM_TOKEN", "unit-test-token-not-production")


def _mkrepo(root: pathlib.Path, name: str, as_file: bool = False) -> pathlib.Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    g = d / ".git"
    if as_file:
        g.write_text("gitdir: /somewhere/else\n", encoding="utf-8")   # git worktree 形态
    else:
        g.mkdir()
        (g / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return d


def _mk_cc_db(path: pathlib.Path, rows) -> None:
    """最小 cloudcli auth.db（字段与真实库实测 schema 对齐，test_cloudcli 同型）。"""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE projects (project_id TEXT PRIMARY KEY, project_path TEXT,"
                " custom_project_name TEXT, isStarred INTEGER, isArchived INTEGER)")
    con.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, provider TEXT,"
                " project_path TEXT, isArchived INTEGER, created_at TEXT, updated_at TEXT)")
    for pid, ppath, cname, star in rows:
        con.execute("INSERT INTO projects VALUES (?,?,?,?,0)", (pid, ppath, cname, star))
    con.commit()
    con.close()


class TestScan(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="l0lp-"))
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_plain_repo_collected_with_basename(self):
        d = _mkrepo(self.root, "projA")
        projects, errors = lp._scan_roots([str(self.root)])
        self.assertEqual(errors, [])
        hits = [p for p in projects if p["path"] == str(d)]
        self.assertEqual(len(hits), 1, "普通 git 仓必须被收录")
        self.assertEqual(hits[0]["name"], "projA")
        self.assertTrue(hits[0]["git"])

    def test_worktree_git_file_counts(self):
        d = _mkrepo(self.root, "wtB", as_file=True)
        projects, _ = lp._scan_roots([str(self.root)])
        self.assertIn(str(d), [p["path"] for p in projects], "worktree 的 .git 是文件也必须算")

    def test_hidden_and_dep_dirs_pruned(self):
        _mkrepo(self.root, ".hermes/internal")          # 点开头目录：工具内部仓，不是项目
        _mkrepo(self.root, "web/node_modules/pkg")      # 依赖目录
        _mkrepo(self.root, "web/venv/x")                # venv
        _mkrepo(self.root, "web/projC")                 # 正常两深项目
        projects, _ = lp._scan_roots([str(self.root)])
        paths = [p["path"] for p in projects]
        self.assertIn(str(self.root / "web" / "projC"), paths)
        self.assertNotIn(str(self.root / ".hermes" / "internal"), paths, "点目录必须剪掉")
        self.assertNotIn(str(self.root / "web" / "node_modules" / "pkg"), paths, "node_modules 必须剪掉")
        self.assertNotIn(str(self.root / "web" / "venv" / "x"), paths, "venv 必须剪掉")

    def test_depth_beyond_max_not_collected(self):
        # 语义：根本身算第 0 层；深度 ≤ MAX_DEPTH 的仓库收录，更深不剪不收。
        # a/b/proj = 深度 3（收）；a/b/c/deep = 深度 4（不收）。
        _mkrepo(self.root, "a/b/c/deep")                # 深度 4 > MAX_DEPTH
        _mkrepo(self.root, "a/b/proj")                  # 深度 3：应收录
        projects, _ = lp._scan_roots([str(self.root)])
        paths = [p["path"] for p in projects]
        self.assertIn(str(self.root / "a" / "b" / "proj"), paths)
        self.assertNotIn(str(self.root / "a" / "b" / "c" / "deep"), paths,
                         f"深度 > {lp.MAX_DEPTH} 必须剪掉")

    def test_missing_root_reported_not_silent(self):
        projects, errors = lp._scan_roots([str(self.tmp / "ghost")])
        self.assertEqual(projects, [])
        self.assertTrue(errors and "不存在" in errors[0], "缺根必须点名，不许静默")


class TestMerge(unittest.TestCase):
    """git + cloudcli 合并：去重、精确名胜出、sessions/last_activity 透传、降级。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="l0lpm-"))
        self.root = self.tmp / "root"
        self.root.mkdir()
        _mkrepo(self.root, "shared")                    # 双源命中
        _mkrepo(self.root, "gitonly")
        self._saved_db = cloudcli.AUTH_DB
        cc_db = self.tmp / "auth.db"
        _mk_cc_db(cc_db, [
            ("p1", str(self.root / "shared"), "共享项目自定义名", 0),
            ("p2", str(self.root / "cconly"), None, 1),
        ])
        cloudcli.AUTH_DB = cc_db
        self.addCleanup(self._restore)

    def _restore(self):
        cloudcli.AUTH_DB = self._saved_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _merged(self):
        git_projects, _ = lp._scan_roots([str(self.root)])
        errors: list = []
        return lp._merge_cloudcli(git_projects, errors), errors

    def test_merge_dedup_and_precise_name_wins(self):
        merged, errors = self._merged()
        self.assertEqual(errors, [])
        by_path = {p["path"]: p for p in merged}
        shared = by_path[str(self.root / "shared")]
        self.assertEqual(shared["name"], "共享项目自定义名", "cloudcli 自定义名必须胜出")
        self.assertTrue(shared["git"] and shared["cloudcli"])
        self.assertIn(str(self.root / "gitonly"), by_path)
        self.assertIn(str(self.root / "cconly"), by_path, "cloudcli 独有项目必须并入")
        self.assertTrue(by_path[str(self.root / "cconly")]["cloudcli"])
        self.assertFalse(by_path[str(self.root / "cconly")]["git"])

    def test_cc_sessions_and_activity_passthrough(self):
        con = sqlite3.connect(cloudcli.AUTH_DB)
        con.execute("INSERT INTO sessions VALUES ('s1','claude',?,0,?,?)",
                    (str(self.root / "shared"), "2026-09-26T10:00:00", "2026-09-26T10:00:00"))
        con.commit()
        con.close()
        merged, _ = self._merged()
        shared = next(p for p in merged if p["path"] == str(self.root / "shared"))
        self.assertEqual(shared["sessions"], 1)
        self.assertEqual(shared["last_activity"], "2026-09-26T10:00:00")

    def test_cc_down_git_survives_with_named_error(self):
        cloudcli.AUTH_DB = self.tmp / "ghost.db"        # 读不到
        merged, errors = self._merged()
        self.assertIn(str(self.root / "gitonly"), [p["path"] for p in merged],
                      "cloudcli 挂了 git 部分必须照常")
        self.assertTrue(any("CloudCLI" in e for e in errors), "降级必须点名，不许静默")


class TestEnvelope(unittest.TestCase):
    def test_limits_are_sane_constants(self):
        self.assertGreaterEqual(lp.LIMIT, 64)
        self.assertGreaterEqual(lp.MAX_DEPTH, 2)
        self.assertIn("node_modules", lp.EXCLUDE_DIR_NAMES)

    def test_list_projects_shape(self):
        d = lp._list_projects()
        for k in ("ok", "count", "projects", "roots", "errors", "took_ms"):
            self.assertIn(k, d)
        for p in d["projects"][:5]:
            for k in ("name", "path", "git", "cloudcli", "sessions", "last_activity"):
                self.assertIn(k, p)


class TestFrontendQuartet(unittest.TestCase):
    """静态断言：section / 侧栏按钮在 #navTree 之上 / PAGE_LABELS / 懒加载钩子成对。"""

    def setUp(self):
        self.html = (_REPO / "templates" / "index.html").read_text(encoding="utf-8")
        self.js = (_REPO / "static" / "hub.js").read_text(encoding="utf-8")

    def test_section_exists(self):
        self.assertIn('id="page-localprojects"', self.html,
                      "page-localprojects section 缺失 ⇒ go() 会静默退回总览")

    def test_sidebar_button_above_navtree(self):
        """用户点名「在 agents 菜单上面」——按钮必须在 #navTree 之前、带 data-sys。"""
        btn = self.html.find('data-sys="localprojects"')
        navtree = self.html.find('id="navTree"')
        home = self.html.find('data-sys="classroom"')
        self.assertGreater(btn, 0, "侧栏「本机项目」按钮缺失")
        self.assertGreater(navtree, 0)
        self.assertLess(btn, navtree, "「本机项目」必须位于 AGENTS 手风琴（#navTree）之上")
        self.assertGreater(btn, home, "顺序：总览 → 本机项目 → AGENTS")

    def test_page_label(self):
        self.assertIn("localprojects: '本机项目'", self.js)

    def test_lazy_hook_paired(self):
        self.assertIn("page === 'localprojects'", self.js)

    def test_loader_functions_defined(self):
        for fn in ("loadLocalProjects", "lpRenderList", "lpSelect", "lpStart",
                   "lpRenderAgents", "lpFilter" if "function lpFilter" in self.js else "lpTime"):
            self.assertIn(f"function {fn}(", self.js, f"{fn} 缺失 ⇒ 面板点了没反应")

    def test_dom_ids_used_by_js_exist_in_html(self):
        for dom_id in ("lpQ", "lpHint", "lpList", "lpSelName", "lpAgent",
                       "lpStartBtn", "lpMeta"):
            self.assertIn(f'id="{dom_id}"', self.html,
                          f"#{dom_id} 在 JS 里被引用但 HTML 缺失 ⇒ null 崩溃")

    def test_inline_handlers_reference_real_functions(self):
        import re
        for m in re.finditer(r'onclick="(?!if\()([a-zA-Z_$][\w$]*)\(', self.html):
            fn = m.group(1)
            if fn in ("chatSend",):     # 既有页面的函数不归本测管（test_kb_frontend_pages 已覆盖）
                continue
            self.assertIn(f"function {fn}(", self.js,
                          f"HTML 引用 {fn}() 但 hub.js 未定义（点击即报错）")

    def test_start_posts_cwd(self):
        """lpStart 必须把选中项目路径作为 cwd 传给 /api/term/sessions——本页的存在意义。"""
        i = self.js.find("async function lpStart(")
        self.assertGreater(i, 0)
        body = self.js[i:i + 800]
        self.assertIn("/api/term/sessions", body)
        self.assertIn("cwd: lpSel", body)


class TestTermCwd(unittest.TestCase):
    """term.py 的 _cwd_or_none（创建端点的 cwd 校验）。"""

    def setUp(self):
        sys.path.insert(0, str(_REPO / "tests"))
        import term                    # noqa: E402
        self.term = term
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="l0tc-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_blank_is_none(self):
        for blank in (None, "", "   "):
            self.assertIsNone(self.term._cwd_or_none(blank))

    def test_valid_abs_dir_returned(self):
        self.assertEqual(self.term._cwd_or_none(str(self.tmp)), str(self.tmp))

    def test_missing_dir_rejected(self):
        with self.assertRaises(ValueError):
            self.term._cwd_or_none(str(self.tmp / "nope"))

    def test_relative_rejected(self):
        with self.assertRaises(ValueError):
            self.term._cwd_or_none("relative/path")

    def test_suspicious_chars_rejected(self):
        for bad in ("/a;b", "/a|b", "/a&b", "/a$b", "/a`b", "/a\nb", "/a\x00b"):
            with self.assertRaises(ValueError):
                self.term._cwd_or_none(bad)

    def test_createin_has_cwd_field(self):
        self.assertIn("cwd", self.term.CreateIn.model_fields)


if __name__ == "__main__":
    unittest.main(verbosity=2)
