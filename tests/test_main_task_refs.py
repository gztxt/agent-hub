"""L0 hermetic：main.py 的「后台任务符号必须存在」+「函数体不得有不可达尾巴」。

起因（2026-09-23 实测，本会话自己造的）：给 src/main.py 插 prov_loop 时，
误把 `async def vitals_loop():` 这一行函数头当锚点吃掉了。结果：
  - vitals 的整个函数体被并进 prov_loop 的 `while True:` **之后** ⇒ 永不执行的死代码；
  - startup() 里 `asyncio.create_task(vitals_loop())` 引用的符号从此不存在。
`python -m py_compile` 过得去，全套单测也全绿（单测故意不 import src.main，
因为它有开库/起探针的 lifespan 副作用），只有真把服务起起来才炸：
  NameError: name 'vitals_loop' is not defined
也就是「语法合法 + 单测全绿 + 服务起不来」。本例把那一步判据前移到测试层。

两条判据都不看语法是否合法，只看语义：
  ① create_task(X()) 里的裸名 X 必须在本模块有定义（或本模块 import 进来）；
  ② `while True:` 之后同层不得跟着语句 —— 除非循环体里真有 break/return/raise
     （有出口就不算不可达，这一条不做就会误报，是刻意留的豁免）。
"""
import ast
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))
import tiers                                   # noqa: E402


def _has_exit(node) -> bool:
    """循环体（含嵌套）里是否存在能让控制流离开 while 的出口。"""
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Break, ast.Return, ast.Raise)):
            return True
        if isinstance(sub, ast.While) and sub is not node:
            # 嵌套 while 里的 break 只出内层，不算本层出口 —— 保守起见仍当作有出口，
            # 因为这里的目标是"绝不误报"，宁可漏报也不要把人从真信号前赶走
            return True
    return False


def _bare_call_name(fn) -> str:
    if isinstance(fn, ast.Name):
        return fn.id
    return ""          # Attribute（如 tasks_mod.sweep_stale_tasks）交给模块自身保证


class TestTaskSymbols(unittest.TestCase):
    """判据①：create_task 的裸名必须有定义"""

    def _mod(self, rel):
        src = (REPO / rel).read_text(encoding="utf-8")
        return ast.parse(src), src

    def _check(self, rel):
        tree, _ = self._mod(rel)
        defined = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(n.name)
            elif isinstance(n, ast.ClassDef):
                defined.add(n.name)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    defined.add(a.asname or a.name.split(".")[0])
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        defined.add(t.id)
        missing = []
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            nm = f.attr if isinstance(f, ast.Attribute) else _bare_call_name(f)
            if nm != "create_task" or not n.args:
                continue
            arg = n.args[0]
            if isinstance(arg, ast.Call):
                target = _bare_call_name(arg.func)
                if target and target not in defined:
                    missing.append((getattr(arg, "lineno", 0), target))
        return missing

    def test_main_create_task_targets_exist(self):
        miss = self._check("src/main.py")
        self.assertEqual(miss, [], f"create_task 引用了不存在的函数（服务起不来）：{miss}")

    def test_other_modules_too(self):
        for rel in ("src/term.py", "src/tasks.py", "src/vitals.py"):
            if not (REPO / rel).exists():
                continue
            with self.subTest(rel=rel):
                self.assertEqual(self._check(rel), [])


class TestUnreachableTail(unittest.TestCase):
    """判据②：while True 之后不得挂死代码（本次事故的直接形态）"""

    def _check(self, rel):
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        bad = []
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            body = list(fn.body)
            for i, st in enumerate(body):
                if isinstance(st, ast.While) and isinstance(st.test, ast.Constant) \
                        and st.test.value is True and not _has_exit(st):
                    tail = body[i + 1:]
                    if tail:
                        bad.append((fn.name, tail[0].lineno,
                                    type(tail[0]).__name__, len(tail)))
        return bad

    def test_main_has_no_unreachable_tail(self):
        bad = self._check("src/main.py")
        self.assertEqual(bad, [], f"while True 之后有不可达语句（多半是函数头被吃掉）：{bad}")

    def test_guard_itself_bites(self):
        """本判据必须是**能变红的**：现场造一份"函数头被吃掉"的等价体。"""
        import ast as _a
        probe = (
            "async def a():\n"
            "    while True:\n"
            "        await x()\n"
            "    'orphan statement that used to be a docstring'\n"
            "    await y()\n"
        )
        tree = _a.parse(probe)
        fn = [n for n in ast.walk(tree) if isinstance(n, _a.AsyncFunctionDef)][0]
        st = fn.body[0]
        self.assertTrue(isinstance(st, _a.While) and isinstance(st.test, _a.Constant)
                        and st.test.value is True)
        self.assertFalse(_has_exit(st), "无出口的 while True 必须判为不可达尾巴的起因")
        self.assertTrue(len(fn.body) > 1, "样本本身就是 while True 后跟语句")


if __name__ == "__main__":
    unittest.main(verbosity=2)
