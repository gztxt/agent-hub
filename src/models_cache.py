"""模型列表的成功路径缓存（2026-10-05，v0.13.79）。

【病根】
`/api/models` 每次都新建 `aiohttp.ClientSession` 去上游拉 `/v1/models`，
`ClientTimeout(total=8)` 且**无缓存** ⇒ 上游（CCR）一挂，**模型下拉每次卡 8 秒**。
而模型清单是**低频变更**的数据（网关侧增删模型是人工动作）⇒ 短 TTL 绰绰有余。

【★ 只缓存成功路径 —— 本模块最要紧的一条】
失败/超时**不写缓存**。否则一次上游抖动会被缓存成「没有模型」并持续 TTL，
把瞬时故障固化成稳定错误 —— 这正是本仓反复消灭的
「全指标绿而功能层已死」同族的静默形态（`tests/verify_memory_federation.py:3-19`
有完整案例：宣告能力 ≠ 实际能力）。

【为什么抽成独立模块】
L0 hermetic 禁 `import src.main`（一 import 跑 lifespan 开真库绑端口）。
本模块只吃一个「可 await 的 fetch」与一个「单调时钟」，不 import fastapi / aiohttp，
所以 L0 能用假 fetch 测「命中几次 / 失败会不会污染缓存」—— 这些判据端到端验不了。

【`force` 参数沿用既有约定，不另造第二套】
`skill_status(force: bool = Query(default=False))`（`skill.py:857`）已是本仓惯例，
这里照抄同一个形状。
"""
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

#: 成功结果的缓存秒数。模型清单人工变更 ⇒ 60s 足够；`/status` 的 600s 是给索引的，
#: 那类数据变更频率更低，不宜照搬。
DEFAULT_TTL_S = 60.0


class TTLCache:
    """极小的 TTL 缓存（只存成功结果）。

    刻意不抽公共缓存类：全仓有四种缓存形制（skill / vitals / gwprobe / _gz_cache），
    此刻统一它们属于「延后批」的重构范围；先让这一处按自己的需要最小实现。
    """

    def __init__(self, ttl_s: float = DEFAULT_TTL_S):
        self.ttl_s = ttl_s
        self._entry: Optional[Tuple[float, Any]] = None

    def get(self, now: float) -> Tuple[bool, Any]:
        """返回 `(命中?, 值)`。未命中时值是 None —— 调用方必须自己再取。"""
        if self._entry is None:
            return False, None
        ts, val = self._entry
        if (now - ts) < self.ttl_s:
            return True, val
        return False, None

    def put(self, val: Any, now: float) -> None:
        self._entry = (now, val)

    def clear(self) -> None:
        self._entry = None


async def cached_models(cache: TTLCache,
                        fetch: Callable[[], Awaitable[Dict[str, Any]]],
                        *,
                        force: bool = False,
                        clock: Callable[[], float] = time.monotonic
                        ) -> Tuple[Dict[str, Any], bool]:
    """取模型清单，命中则复用；**只有 fetch「真的成功」才写缓存**。

    返回 `(payload, cached)`：
      · `cached=True`  ⇒ payload 里的 `cached: True`，调用方可透给前端做诊断显示
      · fetch 抛异常 ⇒ **不写缓存**，异常上抛由调用方按原有语义处理

    ⚠⚠ **「成功」的判据是 `payload["error"]` 为空，不是「fetch 没抛异常」**
    （2026-10-05 实测踩到，记在这里因为它极易被重新踩）：
    `main.py` 的 `_fetch` 里 `async with s.get(...)` **在连接失败时不抛异常** ——
    它只是没拿到 200，于是 `out` 保持空、`_shape_models` 返回一个**空清单**，
    被本函数当成「成功」缓存 60s。

    后果正是本函数 docstring 里警告的那件事：用户点了模型下拉，看到「没有模型」，
    而真实原因是上游连不上；且这个假象要持续 60s 才消失。
    实测（影子指死端口 59999）：第 1 次 3.01s 返回 `{"models":[],"error":…}`，
    第 2~4 次 0.003s —— **快是快了，但缓存的是失败**。
    ⇒ 判据必须是「有 error 就不进缓存」。

    为什么 payload 里带 `cached` 标记：前端面板要能看出「这次没打上游」。
    默默复用而不标记，就是让运维**无法判断**缓存是否在起作用 ——
    而「看不出它有没有生效」正是本仓最贵的几类故障之一。
    """
    now = clock()
    if not force:
        hit, val = cache.get(now)
        if hit:
            return {**val, "cached": True}, True

    payload = await fetch()

    # ★ 唯一判据：payload 自己说它有没有错。见上面那条实测记录。
    # 抛异常的情形在上面已自然处理（不进 catch，直接上抛给调用方）。
    if payload.get("error"):
        return payload, False

    cache.put(payload, clock())
    return payload, False
