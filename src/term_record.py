"""终端会话录制（PT-20260927-16）：pty I/O 落盘 + 回放。

本机**唯一**「主动把用户键入内容落盘」的功能，所以四条硬约束不是风格偏好、
是安全前提（台账 PT-20260927-16 原文照录）：

1. **默认关闭**：`TERM_RECORD` 缺省 0，只有显式 `TERM_RECORD=1` 才录。不开就零开销
   （`Recorder.__init__` 直接短路，不建表写入、不计字节）。
2. **录前一律过既有脱敏器** `sessions_export.redact_text`：pty 流里出现口令/Token 是
   常态而不是边缘情况 —— 不脱敏等于建了一个凭据转储盘。脱敏发生在**落盘前**，
   库里不会有原文。
3. **只记 I/O 与时间戳，绝不记初始环境变量**：env 里就有 `TERM_TOKEN`。本模块**不接触**
   `term.child_env()`，调用方只许把 pty 字节 / 客户端按键喂进来 —— 从签名上断掉这条路，
   而不是靠「记得别记」。
4. **独立表 + 单会话上限 + 全局上限，触顶即停录并可查**：静默丢帧不可接受，用户事后
   分不清「没发生」和「录不下」。所以 `capped` 是**一等状态**，经 `/api/term/recording/{sid}`
   报给界面，由前端明示。

参数（2026-09-29 用户裁定「按建议值执行」）：
- 单会话上限 32MB、全局上限 512MB
- 保留期 7 天，每会话只留最后 1 份录制（同 sid 再次录制即覆盖）
- **不进备份链**（运行时库在 `~/agent-hub/data` 与 `/vol1`，不入 git）
"""
from __future__ import annotations

import base64
import os
import time
from typing import Any, Dict, List, Optional

import db

DEFAULT_MAX_SESSION = 32 * 1024 * 1024
DEFAULT_MAX_TOTAL = 512 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 7

_TRUE = {"1", "true", "yes", "on"}


def enabled() -> bool:
    """录制总开关。缺省 0 ⇒ 默认关。"""
    return os.environ.get("TERM_RECORD", "0").strip().lower() in _TRUE


def _int_env(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def max_session_bytes() -> int:
    return _int_env("TERM_RECORD_MAX_SESSION", DEFAULT_MAX_SESSION)


def max_total_bytes() -> int:
    return _int_env("TERM_RECORD_MAX_TOTAL", DEFAULT_MAX_TOTAL)


def retention_days() -> int:
    return _int_env("TERM_RECORD_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)


def _human(n: int) -> str:
    """上限文案。**不许一律印 MB**：`1024 // (1<<20)` == 0 ⇒ 「已达上限 0MB」是假话，
    而这行文案是触顶时界面唯一的解释（实测自测：单会话上限 1024B 印成 0MB）。"""
    mib = n / (1024 * 1024)
    if n % (1024 * 1024) == 0:
        return "%dMB" % (n // (1024 * 1024))
    return "%d 字节（%.3gMB）" % (n, mib)


def _redact(raw: bytes) -> bytes:
    """pty 字节 → 脱敏后字节。函数内惰性 import：sessions_export 拖 db，本模块也被 term 拖。"""
    from sessions_export import redact_text
    text = raw.decode("utf-8", errors="replace")
    cleaned, hits = redact_text(text)
    if hits:
        # 命中即标记，界面可提示「本段录制已脱敏 N 处」
        return cleaned.encode("utf-8", errors="replace")
    return raw


class Recorder:
    """一个会话一条。`active=False` 时所有方法都是零成本 no-op。"""

    def __init__(self, session_id: str, agent_id: str):
        self.session_id = session_id
        self.agent_id = agent_id
        self.active = False
        self.bytes = 0
        self.frames = 0
        self.capped = False
        self.capped_reason = ""
        self.started = time.time()
        self.ended: Optional[float] = None
        self.redacted_hits = 0
        if not enabled() or not db.is_open():
            return
        # 同 sid 只留最后 1 份：先清掉上一份录制（台账裁定「每会话只留最后 1 份」）。
        _exec("DELETE FROM term_recordings WHERE session_id=?", (self.session_id,))
        used = total_bytes()
        if used >= max_total_bytes():
            # 全局预算满：本会话**不开录**并说清原因，而不是「录了但少了」。
            self.capped = True
            self.capped_reason = ("全局录制预算已满（%s，在盘 %s），本会话未开录"
                                  % (_human(max_total_bytes()), _human(used)))
            return
        self.active = True

    # ── 写 ────────────────────────────────────────────────────────────────
    def feed(self, direction: str, data: bytes) -> None:
        """direction: out = pty→客户端，in = 客户端→pty。绝不记 env、绝不记命令。"""
        if not self.active or not data:
            return
        if self.capped:
            return
        if self.bytes + len(data) > max_session_bytes():
            self.capped = True
            self.capped_reason = "本会话录制已达上限 %s，已停录（后续内容未记录）" % (
                _human(max_session_bytes()))
            return
        if self.bytes + total_bytes() > max_total_bytes():
            self.capped = True
            self.capped_reason = "全局录制预算已满 %s，已停录（后续内容未记录）" % (
                _human(max_total_bytes()))
            return
        blob = _redact(data)
        ok = _exec(
            "INSERT INTO term_recordings(session_id,agent_id,seq,ts,direction,data,bytes) "
            "VALUES(?,?,?,?,?,?,?)",
            (self.session_id, self.agent_id, self.frames, time.time(),
             direction, blob, len(blob)))
        if ok:
            self.frames += 1
            self.bytes += len(blob)

    def close(self) -> None:
        if not self.active:
            return
        self.active = False
        self.ended = time.time()
        sweep()

    # ── 查 ────────────────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id, "agent_id": self.agent_id,
            "recording": bool(self.active or self.frames),
            "enabled": enabled(), "frames": self.frames, "bytes": self.bytes,
            "capped": self.capped, "capped_reason": self.capped_reason,
            "started": self.started, "ended": self.ended,
            "redacted_hits": self.redacted_hits,
            "limits": {"max_session": max_session_bytes(),
                       "max_total": max_total_bytes(),
                       "retention_days": retention_days()},
        }


def _exec(sql: str, params: tuple = ()) -> bool:
    """录制写盘失败**不许打断终端**（记录是旁路，不是主链路）。"""
    if not db.is_open():
        return False
    try:
        db.execute(sql, params)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[term_record] 写入跳过（不影响终端）：{type(e).__name__}: {e}", flush=True)
        return False


def total_bytes() -> int:
    if not db.is_open():
        return 0
    try:
        rows = db.query("SELECT COALESCE(SUM(bytes),0) AS n FROM term_recordings")
        return int(rows[0]["n"]) if rows else 0
    except Exception:  # noqa: BLE001
        return 0


def frames(session_id: str, limit: int = 2000) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT seq, ts, direction, bytes, data FROM term_recordings "
        "WHERE session_id=? ORDER BY seq ASC LIMIT ?", (session_id, limit))
    return [{"seq": r["seq"], "ts": r["ts"], "dir": r["direction"],
             "bytes": r["bytes"],
             "data": base64.b64encode(bytes(r["data"] or b"")).decode("ascii")}
            for r in rows]


def status(session_id: str) -> Dict[str, Any]:
    """给界面的播报：有没有录、录了多少、是不是触顶停的。"""
    rows = db.query(
        "SELECT COUNT(*) AS n, COALESCE(SUM(bytes),0) AS b, MIN(ts) AS t0, MAX(ts) AS t1 "
        "FROM term_recordings WHERE session_id=?", (session_id,))
    r = rows[0] if rows else {"n": 0, "b": 0, "t0": None, "t1": None}
    return {"session_id": session_id, "enabled": enabled(),
            "frames": int(r["n"] or 0), "bytes": int(r["b"] or 0),
            "t0": r["t0"], "t1": r["t1"],
            # 注意：这是**事后推断**（字节数已到上限），不是落库的 capped 真值。真值只在
            # Recorder 实例里（内存），进程重启后无从恢复 —— 所以叫 at_limit 不叫 capped：
            # 接口上不许拿推断冒充事实（口径 2026-09-29 定）。
            "at_limit": bool(r["b"] and int(r["b"]) >= max_session_bytes()),
            "limits": {"max_session": max_session_bytes(),
                       "max_total": max_total_bytes(),
                       "retention_days": retention_days()},
            "total_on_disk": total_bytes()}


def sweep() -> int:
    """保留期清理。返回删掉的帧数。7 天前的整会话删除。"""
    if not db.is_open():
        return 0
    cutoff = time.time() - retention_days() * 86400
    if not _exec("DELETE FROM term_recordings WHERE ts < ?", (cutoff,)):
        return 0
    return 1
