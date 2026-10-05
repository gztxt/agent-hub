"""会话导出的批量取消息（2026-10-05，v0.13.79）—— 把 N+1 收敛成一次 IN 查询。

【病根：实测，不是推断】
`/api/sessions/export` 旧实现在两处各写了一次「每会话一条查询」：

    main.py（CSV）   for r in rows: for m in db.query("... WHERE session_id=?", (r["id"],))
    main.py（JSON）  同上

而 `main.py:1004` 把 `limit` 上限放到 **5000** ⇒ 最坏 **5001 次查询**，且每次都过
`db.py` 的全局 `threading.Lock`（`db.query` 全程持锁，含 `dict(r)` 转换）⇒
**不只是导出慢，还会把所有其他 DB 使用者（vitals / cronjobs / term / memindex）
一起串行化**。

【为什么单独成文件而不是就地改 `main.py`】
L0 hermetic **禁 `import src.main`**（一 import 就跑 lifespan：开真库、起后台探针、
绑端口 —— `tests/README.md:9`）。所以就地改的话，判据无法被单测覆盖，只能靠
L2 探针端到端验；而端到端验不出「查询次数」这个判据。
这是 `src/staticguard.py` 已经用过的范式：把路由里的判据抽成纯函数，路由只调它。
本模块**不 import fastapi、不 import db**，只吃一个可注入的 `execute(sql, params)`。

【分块而不是一个大 IN】
本机 SQLite 3.40.1 实测 5000 个占位符可用（见 CHANGELOG 实测记录），但不同构建的
`SQLITE_MAX_VARIABLE_NUMBER` 可能是 999（老版本默认值）。按 900 分块 ⇒ 任何构建下
都不会撞上限，而 5000/900 = 6 次查询仍远好于 5001 次。
"""
from typing import Any, Callable, Dict, Iterable, List, Sequence

#: 单个 IN 块的占位符上限。900 < 999（SQLite 老默认 SQLITE_MAX_VARIABLE_NUMBER）
#: ⇒ 任何构建下都安全。
CHUNK = 900

#: 单块查询仍返回空时用它收尾（空 session 列表时不能空转）
SELECT_MESSAGES = ("SELECT session_id, role, content, created_at "
                   "FROM chat_messages WHERE session_id IN (%s) ORDER BY id ASC")


def fetch_messages(execute: Callable[[str, Sequence[Any]], List[Dict[str, Any]]],
                   session_ids: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    """一次（或少数几次）查完所有会话的消息，按 session_id 分组返回。

    `execute` 需与 `db.query` 同签名 `(sql, params) -> list[dict]`，但由调用方注入 ——
    这让本函数可以在 L0 里用一个计数 stub 测「发了几次查询」，不需要真库。

    返回值是 `session_id -> [消息行, …]`，**保持 `ORDER BY id ASC` 的原始次序**
    （导出格式的 `MESSAGE_COLUMNS` 断言依赖它）。
    """
    ids = [sid for sid in dict.fromkeys(session_ids) if sid]   # 去重 + 保序 + 去空
    out: Dict[str, List[Dict[str, Any]]] = {sid: [] for sid in ids}
    for i in range(0, len(ids), CHUNK):
        block = ids[i:i + CHUNK]
        ph = ",".join("?" * len(block))
        for m in execute(SELECT_MESSAGES % ph, tuple(block)):
            out.setdefault(str(m.get("session_id")), []).append(m)
    return out


def query_count(n_sessions: int, chunk: int = CHUNK) -> int:
    """给定会话数需要几次消息查询 —— 供闸门断言用（不实际执行）。"""
    return max(1, (n_sessions + chunk - 1) // chunk) if n_sessions else 0
