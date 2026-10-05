"""外部探测的 TTL 缓存 + 「async 里不许裸调同步探测」闸门。

【为什么加缓存：实测数字，不是推断】
`/api/agents` 实测 57ms（主会话 2026-10-05，生产 :3102），其中
`docker ps -a` 独占 **16.5ms**、`systemctl list-units` 独占 **5.1ms** —— 合计 21.6ms，
占 38%。前端每 30s 调一次 loadAgents、每 60s 又调一次 updateBadges ⇒ 每分钟白烧 3 次。
而 `/status` 已有 60s TTL 范式（`skill.py:212`），`list_processes` 已有 3s（`profiles.py:37`）
—— **是「该缓存没缓存」，不是「算法慢」**。

【为什么 kill 路径必须绕过缓存：本文件最要紧的一条】
`resources.kill_resource` 用 `running_systemd_units()` 的结果把关 SIGTERM/SIGKILL
（`resources.py:336-338`）。若吃到最多 3s 的陈旧值：
  · 单元刚停 → 缓存说 running → **误杀一个已经不在的进程组**
  · 单元刚起 → 缓存说 stopped → **拒杀一个真在跑的**（409）
⇒ `force_fresh` 是**仅关键字参数、默认 False**：读路径零改动，写路径显式声明要新鲜值。
`test_kill_path_bypasses_cache` 是本文件的核心断言。

【为什么用 AST 而不是正则判「async 里不许裸调」】
`resources.py:99-109` 自己写了整段 P0-5 规范（「绝不阻塞事件循环」），而
`:180 _collect_agent_resources` 与 `:307 kill_resource` 恰好违反它。
正则会被注释骗过、也会被跨行的 `asyncio.to_thread(...)` 骗过；
AST 看的是**调用节点在不在 async 函数体内**，注释进不来。
（范式同 `tests/test_staticguard.py`。）
"""
import ast
import subprocess
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

from src import profiles  # noqa: E402

RESOURCES_PY = _REPO / "src" / "resources.py"

#: **只含会起外部子进程的两个探测**。
#:
#: ⚠ 为什么 `list_processes` 不在此列（第一版把它算进来，闸门误报，改法就是删掉它）：
#: 它读 `/proc` 是纯文件 IO，没有 `timeout=8/10` 那种「一台 docker 卡住就等 10 秒」的
#: 尾部风险，且已有 3s TTL 缓存（`profiles.py:37`）。把它与 subprocess 探测同等对待，
#: 就是**逼人把正确代码改成 to_thread 求绿** —— 本仓明文禁止假红闸门
#: （`tests/tiers.py:107-114`：「假红的闸门比没有闸门更坏」）。
#: 精准修改 > 顺手扩大范围：这里只禁真正的阻塞源。
PROBE_FUNCS = ("running_systemd_units", "docker_states")


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _async_funcs(tree: ast.Module):
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef)}


class _CountingRun:
    """替换 `subprocess.run`，数调用次数并返回可判定的假结果。

    为什么能替换成功：`profiles.py` 的两个探测把 `import subprocess` 写在
    **函数内部**（L0 可安全 import 的代价），所以它们取的是 `subprocess` 模块属性，
    打个 patch 就能生效 —— 不必真去跑 systemctl/docker。
    """

    def __init__(self, stdout: str = ""):
        self.calls = []
        self.stdout = stdout

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, self.stdout, "")


class ProbeCacheBehaviour(unittest.TestCase):
    """缓存行为：命中、过期、force_fresh 绕过。"""

    def setUp(self):
        # 每个用例独立计数，避免用例间互相污染缓存状态
        self._saved = (profiles._unitsds_cache.copy(), profiles._docker_cache.copy())
        profiles._unitsds_cache.update(ts=0.0, units=None)
        profiles._docker_cache.update(ts=0.0, states=None)
        self.addCleanup(self._restore)

    def _restore(self):
        profiles._unitsds_cache.update(self._saved[0])
        profiles._docker_cache.update(self._saved[1])

    # ── 读路径：TTL 内只跑一次 subprocess ──────────────────────────
    def test_systemd_units_cached_within_ttl(self):
        fake = _CountingRun("claude.service\nfoo.service\n")
        import subprocess as sp
        orig = sp.run
        sp.run = fake
        try:
            first = profiles.running_systemd_units()
            second = profiles.running_systemd_units()
        finally:
            sp.run = orig
        self.assertEqual(first, {"claude", "foo"})
        self.assertEqual(second, first)
        self.assertEqual(len(fake.calls), 1,
                         "TTL 内第二次调用不该再起 systemctl（实际起了 %d 次）" % len(fake.calls))

    def test_docker_states_cached_within_ttl(self):
        import json as _json
        line = _json.dumps({"Names": "tdai", "State": "running"})
        fake = _CountingRun(line + "\n")
        import subprocess as sp
        orig = sp.run
        sp.run = fake
        try:
            first = profiles.docker_states()
            second = profiles.docker_states()
        finally:
            sp.run = orig
        self.assertEqual(first, {"tdai": "running"})
        self.assertEqual(second, first)
        self.assertEqual(len(fake.calls), 1,
                         "TTL 内第二次调用不该再起 docker ps（实际起了 %d 次）" % len(fake.calls))

    # ── TTL 过期后必须重跑 ───────────────────────────────────────
    def test_expired_ttl_refetches(self):
        fake = _CountingRun("a.service\n")
        import subprocess as sp
        orig = sp.run
        sp.run = fake
        try:
            profiles.running_systemd_units()
            # 手动把时间戳推到 TTL 之外（不 sleep，避免拖慢套件）
            profiles._unitsds_cache["ts"] -= (profiles._UNITSD_TTL_S + 1)
            profiles.running_systemd_units()
        finally:
            sp.run = orig
        self.assertEqual(len(fake.calls), 2, "TTL 过期后必须重跑")

    # ── ★ 核心：kill 写路径必须绕过缓存 ──────────────────────────
    def test_force_fresh_bypasses_cache(self):
        """`force_fresh=True` 在 TTL 内也必须真跑 —— 这条是误杀/拒杀的防线。"""
        fake = _CountingRun("a.service\n")
        import subprocess as sp
        orig = sp.run
        sp.run = fake
        try:
            profiles.running_systemd_units()            # 填缓存
            profiles.running_systemd_units()            # 吃缓存（不跑）
            self.assertEqual(len(fake.calls), 1)
            profiles.running_systemd_units(force_fresh=True)   # 必须跑
        finally:
            sp.run = orig
        self.assertEqual(len(fake.calls), 2,
                         "force_fresh=True 仍吃了缓存 ⇒ kill 路径可能误杀/拒杀")

    def test_force_fresh_is_keyword_only(self):
        """仅关键字：防止有人写 `running_systemd_units(True)` 顺手吃到写语义。"""
        import inspect
        for fn in (profiles.running_systemd_units, profiles.docker_states):
            with self.subTest(fn=fn.__name__):
                p = inspect.signature(fn).parameters["force_fresh"]
                self.assertEqual(p.kind, inspect.Parameter.KEYWORD_ONLY,
                                 "force_fresh 必须是仅关键字参数")

    def test_default_is_not_fresh(self):
        """读路径默认吃缓存 —— 否则缓存等于没加。"""
        import inspect
        for fn in (profiles.running_systemd_units, profiles.docker_states):
            with self.subTest(fn=fn.__name__):
                self.assertFalse(inspect.signature(fn)
                                 .parameters["force_fresh"].default)


class KillPathBypassesCache(unittest.TestCase):
    """**本文件的核心**：kill 写路径必须显式要新鲜值。

    这条比「缓存有没有生效」重要一个数量级 —— 缓存不生效只是慢，
    写路径吃陈旧值是**误杀进程**。
    """

    def setUp(self):
        self.src = RESOURCES_PY.read_text(encoding="utf-8")
        self.body = _async_funcs(_tree(RESOURCES_PY)).get("kill_resource")
        self.assertIsNotNone(self.body, "找不到 kill_resource")

    def _to_thread_kwargs_for(self, probe_name):
        """取 `asyncio.to_thread(profiles.<probe>, force_fresh=True)` 里的关键字。

        判据为什么这么写（第一版写错过两次，坑记在这里）：
          ① `force_fresh=True` 是 **`to_thread` 的**关键字参数，不是 `profiles.<probe>()` 的
             —— 所以要在**外层 to_thread 调用**上找关键字，不能在被探测函数的 call 上找；
          ② `to_thread(profiles.running_systemd_units, force_fresh=True)` 的第一个实参
             是 **`ast.Attribute`**（函数引用，尚未调用），**不是 `ast.Call`**。
             写成 `isinstance(arg, ast.Call)` 会永远匹配不到 —— 这就是第一版全红的成因。
        """
        out = []
        for node in ast.walk(self.body):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "to_thread"):
                continue
            for arg in node.args:
                # 实参是函数引用 profiles.<probe>（Attribute），也可能写成
                # profiles.<probe>()（Call）—— 两种都收
                target = arg.func if isinstance(arg, ast.Call) else arg
                if (isinstance(target, ast.Attribute)
                        and target.attr == probe_name):
                    out.append(node)
        return out

    def test_kill_path_requests_fresh_units(self):
        wrapped = self._to_thread_kwargs_for("running_systemd_units")
        self.assertTrue(wrapped,
                        "kill_resource 里没找到 to_thread(profiles.running_systemd_units, …)")
        fresh = [n for n in wrapped
                 if any(k.arg == "force_fresh"
                        and isinstance(k.value, ast.Constant) and k.value.value is True
                        for k in n.keywords)]
        self.assertTrue(fresh,
                        "kill_resource 必须显式传 force_fresh=True："
                        "status 判定直接把关 SIGTERM/SIGKILL，陈旧值会导致误杀或拒杀")

    def test_kill_path_requests_fresh_docker(self):
        wrapped = self._to_thread_kwargs_for("docker_states")
        self.assertTrue(wrapped,
                        "kill_resource 里没找到 to_thread(profiles.docker_states, …)")
        fresh = [n for n in wrapped
                 if any(k.arg == "force_fresh"
                        and isinstance(k.value, ast.Constant) and k.value.value is True
                        for k in n.keywords)]
        self.assertTrue(fresh, "kill_resource 的 docker_states 同样要新鲜值")


class NoBareProbeInAsync(unittest.TestCase):
    """AST 闸门：`async def` 体内不得裸调同步探测（resources.py:99-109 的 P0-5）。

    为什么这条要有：`resources.py:99-109` 用整段注释立了规范，而
    `_collect_agent_resources` 与 `kill_resource` 恰好违反它 ——
    **同一文件内的自相矛盾**，正是本仓反复吃亏的形状。
    一台 docker 卡住（timeout=10）就是整个 hub 的 WebSocket 帧停摆 10 秒。
    """

    #: 读路径豁免：这些函数本身就是同步的（无 async 可言）
    ALLOWED_SYNC = set()

    def _violations(self, fn: ast.AsyncFunctionDef):
        bad = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if name not in PROBE_FUNCS:
                continue
            # 已经裹进 to_thread / gather 的不算（to_thread 内部仍是同步的，
            # 但调用发生在工作线程上，事件循环不被占 —— 这正是 P0-5 要的形状）
            if self._inside_to_thread(node, fn):
                continue
            bad.append("%s:%d %s" % (RESOURCES_PY.name, node.lineno, name))
        return bad

    def _inside_to_thread(self, call: ast.Call, fn: ast.AsyncFunctionDef) -> bool:
        """这个裸 call 是否**已经**被裹进 `asyncio.to_thread(...)`。

        判据按**实参身份**判（`arg is call`），不按父节点链 ——
        因为 `await asyncio.gather(to_thread(a), to_thread(b))` 里，
        两个裸探测的父节点都是 gather 的实参列表，按父节点判会两边都判错。

        ⚠ `to_thread` 的实参可能是函数引用（`profiles.docker_states`，`ast.Attribute`）
        也可能是调用（`profiles.docker_states()`，`ast.Call`）。两种都算「已包裹」。
        """
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "to_thread"):
                continue
            for arg in node.args:
                if arg is call:
                    return True
                # 实参本身是 to_thread 的调用（嵌套写法），也放行
                if isinstance(arg, ast.Call) and arg is call:
                    return True
        return False

    def test_no_bare_probe_calls_in_async_functions(self):
        fns = _async_funcs(_tree(RESOURCES_PY))
        offenders = []
        for name, fn in fns.items():
            offenders += self._violations(fn)
        self.assertEqual([], offenders,
                         "async def 里裸调同步探测（违反本文件 :99-109 的 P0-5）：%s"
                         % offenders)

    def test_the_two_known_collectors_are_actually_async(self):
        """反向自证：确认上面那条判据不是「因为找不到 async 函数而空跑」。

        这就是 `tests/test_tier_collector_parity.py:22-25` 说的「元闸门必须自证」——
        不这么写的元闸门，绿得没有意义。
        """
        fns = _async_funcs(_tree(RESOURCES_PY))
        self.assertIn("_collect_agent_resources", fns,
                      "_collect_agent_resources 不再是 async？那 P0-5 判据的覆盖面变了，请复核")
        self.assertIn("kill_resource", fns,
                      "kill_resource 不再是 async？同上")

    def test_detector_bites_on_synthetic_sample(self):
        """红臂（同一产物内）：造一个违规样本，确认判据真的会报。

        照 `tests/test_tier_collector_parity.py` 的做法 —— 元闸门不自证，
        绿得没有意义。
        """
        sample = ast.parse(
            "async def bad():\n"
            "    units = profiles.running_systemd_units()\n"
            "    return units\n")
        fn = _async_funcs(sample)["bad"]
        self.assertTrue(self._violations(fn),
                        "判据对明显违规的样本都没报 ⇒ 判据本身失效")

    def test_detector_quiet_on_wrapped_sample(self):
        """反向：裹了 to_thread 的样本不该被报。"""
        sample = ast.parse(
            "import asyncio\n"
            "async def good():\n"
            "    units = await asyncio.to_thread(profiles.docker_states)\n"
            "    return units\n")
        fn = _async_funcs(sample)["good"]
        self.assertEqual([], self._violations(fn),
                         "裹了 to_thread 仍被判违规 ⇒ 判据过严，会逼人改对代码求绿")


if __name__ == "__main__":
    unittest.main(verbosity=2)
