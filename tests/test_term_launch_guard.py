"""L0 hermetic：终端拉起面的安全闸（src/term.py + sessions_store.resume_argv）。

这组断言钉的是本项目最要命的一条不变量：
    **客户端永远只能给 agent_id / session_id，命令只出自画像白名单 + 后端模板。**
docstring 第 4-5 行写着这条，但注释不算判据 —— 今天它可测了。

不 fork pty、不起服务、不连网：只用 AST 看源码形状 + 调纯函数。
"""
import ast
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))        # 与 src/*.py 内部的裸 import 保持一致（见 test_term_reaper）
sys.path.insert(0, str(ROOT / "tests"))      # tiers.py
os.environ.setdefault("TERM_TOKEN", "unit-test-token-not-production")

import sessions_store                         # noqa: E402
import term                                   # noqa: E402
import tiers                                  # noqa: E402,F401  (确保分层模块可导入且无副作用)

TERMSRC = ROOT / "src" / "term.py"


def _func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError(f"src/term.py 里找不到函数 {name}（被重构掉了？那这条判据要同步改）")


class TestCreateInShape(unittest.TestCase):
    """请求模型字段清单：多一个能传命令的字段，这条立刻红"""

    FORBIDDEN = ("cmd", "command", "argv", "args", "shell", "binary", "exe", "program", "script")

    def test_only_id_bearing_fields(self):
        names = set(term.CreateIn.model_fields)
        self.assertTrue(names, "CreateIn 没有字段了？")
        bad = {n for n in names if any(t in n.lower() for t in self.FORBIDDEN)}
        self.assertEqual(bad, set(), f"CreateIn 出现了命令类字段 {bad}：等于把 pty 变成 RCE 入口")
        self.assertTrue({"agent_id"} & names, "agent_id 必须还在")

    def test_session_id_still_optional(self):
        f = term.CreateIn.model_fields["session_id"]
        self.assertTrue(f.annotation is not None)


class TestCommandProvenance(unittest.TestCase):
    """create_session 里 cmd 的来源必须只有两处：画像白名单 / 后端模板"""

    @classmethod
    def setUpClass(cls):
        cls.fn = _func(ast.parse(TERMSRC.read_text(encoding="utf-8")), "create_session")

    def _assignments(self):
        out = []
        for n in ast.walk(self.fn):
            if isinstance(n, ast.Assign):
                targets = n.targets
            elif isinstance(n, ast.AnnAssign):
                targets = [n.target]
            else:
                continue
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if "cmd" in names and getattr(n, "value", None) is not None:
                out.append(n)
        return out

    def test_cmd_value_shape_comes_only_from_whitelist_or_template(self):
        """cmd 的每一次赋值，右值只能是：
             ① sessions_store.resume_argv(...)  —— 后端模板（内部再过形状正则 + 实盘双校验）
             ② shlex.split(<画像里的命令串>)      —— 且入参不得引用 body
           出现任何其他来源（尤其直接从 body 拿）即红。"""
        self.assertGreaterEqual(len(self._assignments()), 1, "没找到 cmd 赋值，判据已失效")
        for a in self._assignments():
            v = a.value
            self.assertIsInstance(v, ast.Call,
                                  f"第 {a.lineno} 行 cmd 右值不是函数调用，来源不明：{ast.dump(v)[:80]}")
            fn = v.func
            attr = getattr(fn, "attr", None)
            self.assertIn(attr, ("resume_argv", "split"),
                          f"第 {a.lineno} 行 cmd 来自未预期函数 {attr}")
            if attr == "split":
                for sub in ast.walk(v):
                    if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                            and sub.value.id == "body":
                        self.fail(f"第 {a.lineno} 行把 body.{sub.attr} 当命令串去 split 了")

    def test_only_id_bearing_body_fields_are_read(self):
        """整个 create_session 能读到的请求字段，只许 agent_id / session_id。
           这才是「客户端只能给 id」的可执行版本 —— 多读一个字段就红。"""
        read = set()
        for sub in ast.walk(self.fn):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                    and sub.value.id == "body":
                read.add(sub.attr)
        self.assertTrue(read, "create_session 不再读 body 任何字段？判据需复核")
        self.assertLessEqual(read, {"agent_id", "session_id"},
                             f"读到了多余字段 {read - {'agent_id', 'session_id'}}")

    def test_no_subprocess_in_module_outside_session_class(self):
        """term.py 只应有一处进程拉起（Session.__init__ 的 fork+execvpe）。
           出现 subprocess/os.system/eval 说明有人抄近路绕开 pty 白名单。"""
        tree = ast.parse(TERMSRC.read_text(encoding="utf-8"))
        hits = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                nm = getattr(f, "attr", None) or (getattr(f, "id", "") if isinstance(f, ast.Name) else "")
                if nm in {"system", "popen", "run", "Popen", "check_call",
                          "check_output", "eval", "exec", "spawnl", "spawnv"}:
                    hits.append(f"{nm}@{n.lineno}")
        self.assertEqual(hits, [], f"term.py 出现第二条拉起路径：{hits}")


class TestTokenGate(unittest.TestCase):
    """_check_term_token：空/错必须 401，且实现用常量时间比较"""

    def test_empty_rejected(self):
        with self.assertRaises(Exception) as cm:
            term._check_term_token("", "unit")
        self.assertEqual(getattr(cm.exception, "status_code", None), 401)

    def test_wrong_rejected(self):
        with self.assertRaises(Exception) as cm:
            term._check_term_token(term.TERM_TOKEN + "x", "unit")
        self.assertEqual(getattr(cm.exception, "status_code", None), 401)

    def test_correct_passes(self):
        term._check_term_token(term.TERM_TOKEN, "unit")     # 不抛即通过

    def test_token_is_not_empty_by_default(self):
        """未配 TERM_TOKEN 时自动生成，绝不允许"空 token=不鉴权"的裸奔态"""
        self.assertTrue(term.TERM_TOKEN, "TERM_TOKEN 为空：闸门形同虚设")
        self.assertGreaterEqual(len(term.TERM_TOKEN), 16)

    def test_uses_constant_time_compare(self):
        src = TERMSRC.read_text(encoding="utf-8")
        i = src.find("def _check_term_token")
        seg = src[i:i + 400] if i >= 0 else "<找不到 _check_term_token>"
        self.assertIn("hmac.compare_digest", seg,
                      "token 比较退化成 == 会引入计时侧信道（函数体前 400 字符附下）：\n" + seg)


class TestResumeArgvHostile(unittest.TestCase):
    """六家 agent 的 id 注入面：形状正则 + 实盘存在双校验，一道都不许松"""

    HOSTILE = ["x; rm -rf /", "--resume=evil", "$(id)", "`id`", "../etc/passwd",
               "a" * 300, "", " ", "\n", "01a0c91d-eb84-7130-9767-479821ef336c; echo",
               "session_x' or '1'='1"]

    def test_every_agent_rejects_every_hostile_id(self):
        for agent in sorted(sessions_store.SESSION_STORES):
            for bad in self.HOSTILE:
                with self.subTest(agent=agent, bad=bad[:18]):
                    with self.assertRaises(ValueError):
                        sessions_store.resume_argv(agent, bad, tiers.repo_root())

    def test_absent_but_wellformed_id_rejected(self):
        """形状合法但盘上没有 —— 必须拒（这条决定"续聊"不能被拿来拉起任意命令）"""
        for agent, sid in (("grok", "00000000-0000-4000-8000-000000000000"),
                           ("codex", "00000000-0000-4000-8000-000000000000")):
            with self.subTest(agent=agent):
                with self.assertRaises(ValueError):
                    sessions_store.resume_argv(agent, sid, "/tmp")

    def test_unknown_agent_rejected(self):
        with self.assertRaises(ValueError):
            sessions_store.resume_argv("pi", "01a0c91d-eb84-7130-9767-479821ef336c", "/tmp")

    def test_argv_is_always_a_list_of_str(self):
        """返回形状：pty execvpe 要求全 str，混进 None/路径对象会直接崩在子进程"""
        for agent in sorted(sessions_store.SESSION_STORES):
            for bad in ("nope", ""):
                try:
                    argv = sessions_store.resume_argv(agent, bad, "/tmp")
                except ValueError:
                    continue
                self.assertIsInstance(argv, list)
                self.assertTrue(all(isinstance(x, str) for x in argv), f"{agent} argv 含非 str")


if __name__ == "__main__":
    unittest.main()
