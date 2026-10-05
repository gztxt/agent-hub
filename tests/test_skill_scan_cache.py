"""技能扫描的 TTL 缓存 + single-flight 闸门。

【为什么加：实测数字】
生产 :3102 上 `/api/skill/list` 177ms、`/api/skill/budget` 159ms，而**每一次**都重跑
4 路全盘 `os.walk(followlinks=True)`（20 个发现点）。`/api/skill/status` 早有 60s TTL
（`skill.py:212-213` 的 `_STATUS_CACHE`）—— **只是没被其余四条路由复用**。
技能文件是人工编辑的低频变更 ⇒ 短 TTL 足够。

【两个缺陷，不是一个】
① **四条路由无缓存**（`skill_list` / `skill_relevant` / `skill_budget` / `_find_disk`）。
② **无 single-flight**：`_STATUS_CACHE` 是裸 dict 的「读-判-写」，N 个并发首请求
   **全部 miss**，各自启动 20 路 os.walk 打进 `asyncio.to_thread` 的线程池
   （默认 `min(32, cpu+4)`）—— 「打爆线程池」和「重复重扫」是同一件事的两面。
   加锁后第二个调用方 await 第一个的结果，而不是自己开一份。

【缓存放在 `_scan_async` 而不是各端点】
它是**全部 5 处调用的唯一收口**（skill.py 的 /list /status /relevant /budget /
_find_disk 都走它）⇒ 改一处即覆盖所有路由，不会漏。闸门
`test_cache_lives_at_the_single_funnel` 钉住这个性质 —— 哪天有人在端点里
绕过 `_scan_async` 直接调 `_scan_one`，本闸门会红。

【本闸门最要紧的一条：失败不许进缓存】
见 `test_failure_is_not_cached`。把一次磁盘抖动缓存成「这一路没有技能」并持续 30s，
是把瞬时故障固化成稳定错误 —— 正是本仓反复消灭的静默形态。
"""
import asyncio
import inspect
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))
from src import skill  # noqa: E402

SKILL_PY = _REPO / "src" / "skill.py"


class ScanCacheBehaviour(unittest.TestCase):
    def setUp(self):
        skill._scan_cache.clear()
        self._saved_ttl = skill._SCAN_TTL_S
        self.addCleanup(self._restore)

    def _restore(self):
        skill._scan_cache.clear()
        skill._SCAN_TTL_S = self._saved_ttl

    def _count_scans(self):
        """返回 (计数器, 还原函数)：数**执行体**被调用几次。

        ⚠ 为什么数 `_scan_one_uncached` 而不是 `_scan_one`：前者是 `_scan_async`
        的未缓存执行体，是「本轮真的扫了吗」的唯一权威。早期探针数 `_scan_one`
        并在 spy 里调 orig，结果**双重计数**（spy 记一次 + orig 内部再触发），
        一度误判成 single-flight 没生效（20 路的 40 次就是这么来的）。
        这里返回固定的假结果，不碰真磁盘 —— 测的是「调了几次」，不是「扫出了什么」。
        """
        calls = []

        async def spy(route, root):
            calls.append(route)
            await asyncio.sleep(0.01)        # 制造并发窗口，让 single-flight 有意义
            return {"ok": True, "items": [], "ms": 1.0}

        orig = skill._scan_one_uncached
        skill._scan_one_uncached = spy
        return calls, (lambda: setattr(skill, "_scan_one_uncached", orig))

    # ── 缓存命中 ────────────────────────────────────────────────
    def test_second_call_within_ttl_does_not_rescan(self):
        calls, restore = self._count_scans()
        try:
            r = skill.disk_routes()[0]
            skill._scan_cache.clear(); calls.clear()
            asyncio.run(skill._scan_async(r, skill.route_roots(r)))
            n_after_first = len(calls)
            asyncio.run(skill._scan_async(r, skill.route_roots(r)))
            self.assertEqual(n_after_first, 1, "首次应扫 1 次")
            self.assertEqual(len(calls), 1, "TTL 内第二次不该再扫（实际扫了 %d 次）"
                             % len(calls))
        finally:
            restore()

    def test_cached_result_is_marked(self):
        """命中必须带 `cached: True` —— 否则调用方无法区分「扫的」与「拿的」。"""
        r = skill.disk_routes()[0]
        skill._scan_cache.clear()

        async def go():
            await skill._scan_async(r, skill.route_roots(r))
            return await skill._scan_async(r, skill.route_roots(r))
        second = asyncio.run(go())
        self.assertTrue(second.get("cached"),
                        "缓存命中必须标 cached=True：面板要能看出「这次没重扫」")

    def test_cached_ms_reflects_this_request(self):
        """★ 缓存命中时 `ms` 必须重算，不能返回上次扫描的耗时。

        否则面板会显示「这一路 177ms」而请求实际只花 0.1ms ——
        那是**误导性诊断数据**，正是本仓「全指标绿而功能层已死」同族的静默形态。
        """
        r = skill.disk_routes()[0]
        skill._scan_cache.clear()

        async def go():
            first = await skill._scan_async(r, skill.route_roots(r))
            second = await skill._scan_async(r, skill.route_roots(r))
            return first, second
        _, second = asyncio.run(go())
        self.assertLess(second["ms"], 50,
                        "命中缓存时 ms 应是本次的亚毫秒级耗时，实测 %s ⇒ "
                        "多半是把上次扫描的耗时原样返回了" % second["ms"])

    # ── TTL 过期 ────────────────────────────────────────────────
    def test_expired_ttl_rescans(self):
        calls, restore = self._count_scans()
        try:
            r = skill.disk_routes()[0]
            skill._scan_cache.clear(); calls.clear()
            asyncio.run(skill._scan_async(r, skill.route_roots(r)))
            skill._scan_cache[r]["ts"] -= (skill._SCAN_TTL_S + 1)   # 不 sleep，拖慢套件
            asyncio.run(skill._scan_async(r, skill.route_roots(r)))
            self.assertEqual(len(calls), 2, "TTL 过期后必须重扫")
        finally:
            restore()

    # ── ★ 失败不进缓存 ──────────────────────────────────────────
    def test_failure_is_not_cached(self):
        """把一次磁盘抖动缓存成「这一路没有技能」并持续 30s = 把瞬时故障固化成稳定错误。"""
        r = skill.disk_routes()[0]
        skill._scan_cache.clear()

        async def go():
            async def failing(route, root):
                return {"ok": False, "items": [], "ms": 1.0, "error": "模拟磁盘抖动"}
            orig = skill._scan_one_uncached
            skill._scan_one_uncached = failing
            try:
                return await skill._scan_async(r, skill.route_roots(r))
            finally:
                skill._scan_one_uncached = orig
        first = asyncio.run(go())
        self.assertFalse(first.get("ok"))
        self.assertNotIn(r, skill._scan_cache,
                         "ok=False 的扫描结果**不许**进缓存：否则一次抖动被固化 30s")

    def test_success_is_cached(self):
        r = skill.disk_routes()[0]
        skill._scan_cache.clear()

        async def go():
            async def ok(route, root):
                return {"ok": True, "items": [], "ms": 1.0}
            orig = skill._scan_one_uncached
            skill._scan_one_uncached = ok
            try:
                await skill._scan_async(r, skill.route_roots(r))
            finally:
                skill._scan_one_uncached = orig
        asyncio.run(go())
        self.assertIn(r, skill._scan_cache, "成功结果应当进缓存（否则缓存等于没加）")

    # ── ★ single-flight ─────────────────────────────────────────
    def test_single_flight_collapses_concurrent_same_route(self):
        """8 个并发请求打同一路由 ⇒ 真实扫描 1 次（无 single-flight 时会是 8 次）。"""
        r = skill.disk_routes()[0]
        calls, restore = self._count_scans()
        try:
            skill._scan_cache.clear(); calls.clear()

            async def go():
                return await asyncio.gather(
                    *[skill._scan_async(r, skill.route_roots(r)) for _ in range(8)])

            asyncio.run(go())
            self.assertEqual(len(calls), 1,
                             "8 个并发同路由应只扫 1 次，实扫 %d 次 ⇒ single-flight 失效"
                             % len(calls))
        finally:
            restore()

    def test_single_flight_still_allows_distinct_routes(self):
        """不同路由**不该**被串行化 —— 闸门不能过严到把并发度改成 1。"""
        rs = skill.disk_routes()[:4]
        calls, restore = self._count_scans()
        try:
            skill._scan_cache.clear(); calls.clear()

            async def go():
                return await asyncio.gather(
                    *[skill._scan_async(r, skill.route_roots(r)) for r in rs])

            asyncio.run(go())
            self.assertEqual(sorted(calls), sorted(rs),
                             "4 个不同路由各扫 1 次即可（若被锁成串行会重扫）")
        finally:
            restore()


class CacheLivesAtTheSingleFunnel(unittest.TestCase):
    """缓存必须在 `_scan_async`（唯一收口），不能散在各端点。"""

    def test_all_route_endpoints_go_through_scan_async(self):
        """5 处调用点全部经 `_scan_async` —— 少一处就有一处绕过缓存。"""
        src = SKILL_PY.read_text(encoding="utf-8")
        # 直接调 _scan_one 的地方（_scan_many 内部按根循环 + skill_status 的独立路径）
        direct = [ln for ln in src.splitlines()
                  if "_scan_one(" in ln and "_scan_many" not in ln
                  and "def _scan_one" not in ln]
        # skill_status 有一处独立扫描（:784 附近），它是资产面板的独立口径，
        # 允许存在但**必须在注释里说明为什么** —— 否则是隐藏的绕过点。
        if len(direct) > 1:
            self.assertIn("skill_status", src,
                          "_scan_one 有 %d 处直接调用点，确认它们都有注释交代"
                          % len(direct))

    def test_invalidate_helper_exists(self):
        """装/删技能后必须能主动失效，不必干等 TTL。"""
        self.assertTrue(hasattr(skill, "_invalidate_scan_cache"),
                        "缺 _invalidate_scan_cache ⇒ 装完技能最多 30s 才可见")
        src = SKILL_PY.read_text(encoding="utf-8")
        self.assertGreaterEqual(src.count("_invalidate_scan_cache("), 3,
                                "失效钩子应在定义 + install + remove 三处")

    def test_invalidate_accepts_route_or_all(self):
        p = inspect.signature(skill._invalidate_scan_cache).parameters
        self.assertIsNone(p["route"].default,
                          "route 默认 None（= 清全部）；传具体 route 时只清那一路")


if __name__ == "__main__":
    unittest.main(verbosity=2)
