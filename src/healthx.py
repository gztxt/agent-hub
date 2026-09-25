"""/health 的**纯函数**辅助层：把"画像最近检测时间"这类派生量从 main.py 里抽出来。

为什么单独一个模块（而不是直接写在 /health 里）：
  项目分层铁律 —— L0 hermetic **不 import `src.main`**（见 tests/README.md）。
  /health 里的字段一旦写在 main.py，就只能靠 L2 live 探针去验，而 live 探针需要
  服务在跑、会产生副作用。抽成 stdlib-only 纯函数后，"三轮没扫 ⇒ stale"这种判据
  可以在空 HOME 下红绿对照，不必碰生产。

口径与 `code_stale` / `memory_backend` 一致：**只兑情报，不改 `status`**。
心跳停了不等于 hub 坏了（可能是 VITALS_SWEEP_SEC 被调大、也可能是刚启动还没到首轮）。
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, Optional

# 几轮没扫就算"心跳停了"。3 轮是容忍度与灵敏度的折中：
# 1 轮会把"扫描本身慢于周期"误报成红（本机实测一轮 sweep 墙钟 avg 18.23s、
# 抖动期可达 1276s/70 轮），2 轮仍可能被一次长扫吞掉。
STALE_ROUNDS = 3


def profiles_last_check(snap: Optional[Dict[str, Any]],
                        last_sweep: Optional[float],
                        sweep_every: Optional[float],
                        now: Optional[float] = None) -> Dict[str, Any]:
    """把 vitals 的内存快照折成"最近检测时间"情报。

    参数全部由调用方注入（不在这里 import vitals）⇒ 本函数无 I/O、无全局态、可断言。

    返回字段的用意：
      · `last_sweep_age_s` —— 上一轮**开始/结束**距今多久；None = 从未扫过。
      · `checked` / `agents` —— 有 `checked_at` 的家数 vs 在册家数；两者长期不等
        说明有人在册却从没被扫到（正是 09-22 "在册却静默不可用 21 天" 的形态）。
      · `oldest_check_age_s` —— **最陈旧那一家**的年龄。只看 last_sweep 会被
        "新一轮扫了 6 家、漏了第 7 家"骗过去，所以必须给最坏值。
      · `stale` —— 心跳判据：last_sweep 超过 STALE_ROUNDS 轮，或从未扫过。
    """
    now_ts = time.time() if now is None else float(now)
    snap = snap or {}
    every = float(sweep_every or 0) or None
    ages = []
    for _aid, ev in snap.items():
        c = (ev or {}).get("checked_at") if isinstance(ev, dict) else None
        if not c:
            continue
        try:
            a = now_ts - float(c)
        except (TypeError, ValueError):
            continue
        if math.isfinite(a):
            ages.append(a)

    ls: Optional[float] = None
    if last_sweep:
        try:
            v = float(last_sweep)
            # NaN/Inf 必须当"没有值"处理：它们会被 json 序列化成裸 `NaN`/`Infinity`，
            # 那不是合法 JSON ⇒ 前端 JSON.parse(/health) 会整页炸掉（而 /health 是自证端点，
            # 它炸了等于所有判据一起失明）。float("NaN") 不抛异常，所以光靠 try 拦不住。
            ls = v if math.isfinite(v) else None
        except (TypeError, ValueError):
            ls = None

    out: Dict[str, Any] = {
        "agents": len(snap),
        "checked": len(ages),
        "unchecked": max(0, len(snap) - len(ages)),
        "sweep_every_sec": every,
        "last_sweep_age_s": round(now_ts - ls, 1) if ls else None,
        "oldest_check_age_s": round(max(ages), 1) if ages else None,
        "newest_check_age_s": round(min(ages), 1) if ages else None,
        "stale_rounds": STALE_ROUNDS,
    }
    if not ls:
        out["state"] = "never_swept"
        out["stale"] = True
    else:
        age = now_ts - ls
        out["stale"] = bool(every and age > STALE_ROUNDS * every)
        out["state"] = "stale" if out["stale"] else "ok"
    return out
