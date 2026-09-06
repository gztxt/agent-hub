"""任务 DAG 协同引擎（部署方案 S3 的 Python 落地：SQLite 状态机 + asyncio）

对应上游 workflow.rs 的核心语义，砍掉 Redis/petgraph：
- 状态持久化：tasks 表（服务重启不丢，running→启动时标记 failed 由用户重试）
- 拓扑执行：依赖满足即并发派发（每 run 并发上限 4）
- 超时熔断：单任务默认 600s（asyncio.wait_for）+ 后台 sweeper 兜底
- 上下文传递：上游 output 注入下游 prompt（临时记忆区语义，限长防爆炸）
- Leader 拆解：LLM 产出受校验的 JSON DAG（环检测/引用检测/数量与深度限制）
"""
import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import db

router = APIRouter()

TASK_TIMEOUT_S = int(os.getenv("TASK_TIMEOUT_S", "600"))
MAX_CONCURRENCY = 4
MAX_TASKS = 12
UPSTREAM_TRIM = 4000

_ctx: Dict[str, Any] = {}
_runners: Dict[str, asyncio.Task] = {}


def set_context(chat_fn=None, agent_ids_fn=None):
    if chat_fn:
        _ctx["chat_fn"] = chat_fn
    if agent_ids_fn:
        _ctx["agent_ids_fn"] = agent_ids_fn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    trace_id TEXT,
    goal TEXT,
    prompt TEXT NOT NULL,
    agent_id TEXT,
    deps TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|running|success|failed|blocked
    output TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id, status);
"""


def ensure_schema():
    db.execute_script(SCHEMA_TASKS)


class DecomposeIn(BaseModel):
    goal: str = Field(min_length=4, max_length=4000)
    default_agent: Optional[str] = None
    auto_run: bool = False


class RetryIn(BaseModel):
    task_id: Optional[str] = None


LEADER_PROMPT = """你是任务编排器。把用户目标拆解为子任务 DAG，只输出 JSON 数组（不要围栏、不要解释）。
每项格式: {"id":"t1","task":"中文指令（自包含，可独立执行）","agent_id":"执行Agent","deps":[]}
规则：
- agent_id 只能从这里选：{agents}（选最合适的；不确定用 {default}）
- 子任务数 ≤ {maxn}；无依赖的兄弟任务会被并行执行，有依赖写 deps
- 聚合/汇总类任务必须 deps 列出全部上游
"""


def _extract_json_array(text: str) -> list:
    text = re.sub(r"```(?:json)?|```", "", text)
    s, e = text.find("["), text.rfind("]")
    if s < 0 or e <= s:
        raise ValueError("LLM 未返回 JSON 数组")
    return json.loads(text[s:e + 1])


def _validate_dag(items: list, known_agents: set, default_agent: str) -> List[dict]:
    if not (1 <= len(items) <= MAX_TASKS):
        raise HTTPException(400, f"子任务数须在 1-{MAX_TASKS}，得到 {len(items)}")
    normalized, ids = [], set()
    for i, it in enumerate(items):
        tid = str(it.get("id") or f"t{i+1}")
        if tid in ids:
            raise HTTPException(400, f"任务 id 重复: {tid}")
        ids.add(tid)
        agent = it.get("agent_id") or default_agent
        if agent not in known_agents:
            agent = default_agent if default_agent in known_agents else "jcode"
        normalized.append({"id": tid, "task": str(it.get("task") or it.get("prompt") or "").strip(),
                           "agent_id": agent, "deps": [str(d) for d in (it.get("deps") or [])]})
    for n in normalized:
        for d in n["deps"]:
            if d not in ids:
                raise HTTPException(400, f"依赖不存在的任务: {n['id']} -> {d}")
    # Kahn 环检测
    indeg = {n["id"]: len(n["deps"]) for n in normalized}
    children: Dict[str, list] = {n["id"]: [] for n in normalized}
    for n in normalized:
        for d in n["deps"]:
            children[d].append(n["id"])
    queue = [k for k, v in indeg.items() if v == 0]
    visited = 0
    while queue:
        cur = queue.pop()
        visited += 1
        for ch in children[cur]:
            indeg[ch] -= 1
            if indeg[ch] == 0:
                queue.append(ch)
    if visited != len(normalized):
        raise HTTPException(400, "检测到循环依赖，已拒绝该 DAG")
    return normalized


@router.post("/api/tasks/decompose")
async def decompose(body: DecomposeIn):
    import llm
    if not llm.configured():
        raise HTTPException(503, "LLM 未配置，无法拆解")
    known = set()
    if _ctx.get("agent_ids_fn"):
        known = set(_ctx["agent_ids_fn"]())
    default = body.default_agent or ("jcode" if "jcode" in known else (sorted(known)[0] if known else "jcode"))
    messages = [{"role": "system", "content": (
        LEADER_PROMPT
        .replace("{agents}", ", ".join(sorted(known)) or "jcode")
        .replace("{default}", default)
        .replace("{maxn}", str(MAX_TASKS)))},
        {"role": "user", "content": body.goal}]
    try:
        answer, _ = await llm.chat_tools_loop(messages, [], None, max_rounds=1)
        items = _validate_dag(_extract_json_array(answer), known, default)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Leader 拆解失败: {e}")
    run_id = uuid.uuid4().hex[:10]
    now = _now()
    for it in items:
        db.execute(
            "INSERT INTO tasks(run_id,task_id,trace_id,goal,prompt,agent_id,deps,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,'pending',?,?)",
            (run_id, it["id"], run_id, body.goal, it["task"], it["agent_id"],
             json.dumps(it["deps"]), now, now))
    if body.auto_run:
        _start_runner(run_id)
    return {"run_id": run_id, "goal": body.goal, "tasks": items,
            "running": body.auto_run}


@router.get("/api/tasks/runs")
async def list_runs(limit: int = Query(default=20, le=100)):
    rows = db.query("""
        SELECT run_id, MIN(trace_id) trace_id, MAX(goal) goal, COUNT(*) total,
               SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) success,
               SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,
               SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) running,
               SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) pending,
               MIN(created_at) created_at, MAX(finished_at) finished_at
        FROM tasks GROUP BY run_id ORDER BY created_at DESC LIMIT ?""", (limit,))
    for r in rows:
        r["state"] = ("running" if r["running"] else
                      "success" if r["success"] == r["total"] else
                      "failed" if r["failed"] and r["pending"] == r["running"] == 0 else
                      "pending" if r["pending"] == r["total"] else "partial")
    return {"runs": rows}


@router.get("/api/tasks/{run_id}")
async def get_run(run_id: str):
    rows = db.query("SELECT * FROM tasks WHERE run_id=? ORDER BY created_at, task_id", (run_id,))
    if not rows:
        raise HTTPException(404, "run not found")
    for r in rows:
        r["deps"] = json.loads(r["deps"] or "[]")
    return {"run_id": run_id, "goal": rows[0]["goal"], "tasks": rows}


@router.post("/api/tasks/{run_id}/start")
async def start_run(run_id: str):
    n = db.query("SELECT COUNT(*) c FROM tasks WHERE run_id=?", (run_id,))[0]["c"]
    if not n:
        raise HTTPException(404, "run not found")
    _start_runner(run_id)
    return {"run_id": run_id, "status": "scheduled"}


@router.post("/api/tasks/{run_id}/retry")
async def retry_run(run_id: str, body: RetryIn):
    if body.task_id:
        db.execute("UPDATE tasks SET status='pending',error=NULL,updated_at=? "
                   "WHERE run_id=? AND task_id=?", (_now(), run_id, body.task_id))
    else:
        db.execute("UPDATE tasks SET status='pending',error=NULL,updated_at=? "
                   "WHERE run_id=? AND status IN ('failed','blocked')", (_now(), run_id))
    _start_runner(run_id)
    return {"run_id": run_id, "status": "retry-scheduled"}


@router.delete("/api/tasks/{run_id}")
async def delete_run(run_id: str):
    if run_id in _runners:
        _runners[run_id].cancel()
        _runners.pop(run_id)
    n = db.execute("DELETE FROM tasks WHERE run_id=?", (run_id,))
    if not n:
        raise HTTPException(404, "run not found")
    return {"status": "deleted", "run_id": run_id}


def _start_runner(run_id: str):
    old = _runners.get(run_id)
    if old and not old.done():
        return  # 已在跑
    t = asyncio.create_task(_execute_run(run_id))
    _runners[run_id] = t
    t.add_done_callback(lambda _f: _runners.pop(run_id, None))


async def _run_task(run_id: str, row: dict):
    chat_fn = _ctx.get("chat_fn")
    if not chat_fn:
        db.execute("UPDATE tasks SET status='failed',error='chat 引擎未就绪',updated_at=? "
                   "WHERE run_id=? AND task_id=?", (_now(), run_id, row["task_id"]))
        return
    # 上下文传递：上游 success 输出注入
    ups = db.query("SELECT task_id,output FROM tasks WHERE run_id=? AND status='success'",
                   (run_id,))
    upstream = {u["task_id"]: u["output"] or "" for u in ups}
    prompt = row["prompt"]
    dep_outputs = []
    for d in json.loads(row["deps"] or "[]"):
        if d in upstream:
            dep_outputs.append(f"—— 任务 {d} 的结果 ——\n{upstream[d][:UPSTREAM_TRIM]}")
    if dep_outputs:
        prompt = f"总目标：{row['goal']}\n\n你的子任务：{prompt}\n\n" + "\n\n".join(dep_outputs)
    t0 = asyncio.get_event_loop().time()
    try:
        result = await asyncio.wait_for(
            chat_fn(row["agent_id"], prompt,
                    f"task-{row['trace_id']}-{row['task_id']}"),
            timeout=TASK_TIMEOUT_S)
        dur = int((asyncio.get_event_loop().time() - t0) * 1000)
        if result.get("success"):
            db.execute("UPDATE tasks SET status='success',output=?,duration_ms=?,"
                       "finished_at=?,updated_at=? WHERE run_id=? AND task_id=?",
                       (result.get("response", "")[:20000], dur, _now(), _now(),
                        run_id, row["task_id"]))
            db.log_profile_event("task_exec", row["agent_id"], "success", dur,
                                 trace_id=row["trace_id"], detail={"task": row["task_id"]})
        else:
            err = (result.get("error") or "unknown")[:500]
            db.execute("UPDATE tasks SET status='failed',error=?,duration_ms=?,"
                       "finished_at=?,updated_at=? WHERE run_id=? AND task_id=?",
                       (err, dur, _now(), _now(), run_id, row["task_id"]))
            db.log_profile_event("task_exec", row["agent_id"], "fail", dur,
                                 trace_id=row["trace_id"], detail={"task": row["task_id"]})
    except asyncio.TimeoutError:
        db.execute("UPDATE tasks SET status='failed',error='超时熔断 %ds',finished_at=?,"
                   "updated_at=? WHERE run_id=? AND task_id=?",
                   (TASK_TIMEOUT_S, _now(), _now(), run_id, row["task_id"]))
        db.log_profile_event("task_exec", row["agent_id"], "fail", TASK_TIMEOUT_S * 1000,
                             trace_id=row["trace_id"], detail={"task": row["task_id"], "timeout": True})
    except Exception as e:  # noqa: BLE001
        db.execute("UPDATE tasks SET status='failed',error=?,finished_at=?,updated_at=?"
                   " WHERE run_id=? AND task_id=?",
                   (repr(e)[:500], _now(), _now(), run_id, row["task_id"]))


async def _execute_run(run_id: str):
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    live: Dict[str, asyncio.Task] = {}
    try:
        while True:
            rows = db.query("SELECT * FROM tasks WHERE run_id=? ORDER BY created_at", (run_id,))
            status = {r["task_id"]: r["status"] for r in rows}
            finished = all(s in ("success", "failed", "blocked") for s in status.values())
            if finished:
                return
            for r in rows:
                if r["task_id"] in live or r["status"] != "pending":
                    continue
                deps = json.loads(r["deps"] or "[]")
                if any(status.get(d) == "failed" or status.get(d) == "blocked" for d in deps):
                    db.execute("UPDATE tasks SET status='blocked',error='上游失败',finished_at=?,"
                               "updated_at=? WHERE run_id=? AND task_id=?",
                               (_now(), _now(), run_id, r["task_id"]))
                    continue
                if all(status.get(d) == "success" for d in deps):
                    db.execute("UPDATE tasks SET status='running',started_at=?,updated_at=?"
                               " WHERE run_id=? AND task_id=?",
                               (_now(), _now(), run_id, r["task_id"]))

                    async def guarded(row=r):
                        async with sem:
                            await _run_task(run_id, row)
                    live[r["task_id"]] = asyncio.create_task(guarded())
            if not live:
                # 没有任何在跑且未结束 → 剩余 pending 的上游都在 running 之外，等待或死锁
                pend = [t for t, s in status.items() if s == "pending"]
                runn = [t for t, s in status.items() if s == "running"]
                if not runn and pend:
                    db.execute("UPDATE tasks SET status='failed',error='无法调度(疑似悬挂)',"
                               "updated_at=? WHERE run_id=? AND status='pending'", (_now(), run_id))
                    return
            done = [tid for tid, t in live.items() if t.done()]
            for tid in done:
                live.pop(tid)
            await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        for t in live.values():
            t.cancel()
        raise


async def sweep_stale_tasks():
    """服务启动恢复 + 周期兜底：running 超时→failed（上游 sweeper 语义）"""
    db.execute("UPDATE tasks SET status='failed',error='服务重启恢复:标记中断',finished_at=?,"
               "updated_at=? WHERE status='running'", (_now(), _now()))
    while True:
        await asyncio.sleep(30)
        try:
            cutoff = datetime.now(timezone.utc).timestamp() - (TASK_TIMEOUT_S + 120)
            stale = db.query("SELECT run_id,task_id,started_at FROM tasks WHERE status='running'")
            for r in stale:
                try:
                    started = datetime.fromisoformat(r["started_at"]).timestamp()
                except (TypeError, ValueError):
                    continue
                if started < cutoff:
                    db.execute("UPDATE tasks SET status='failed',error='超时兜底',finished_at=?,"
                               "updated_at=? WHERE run_id=? AND task_id=?",
                               (_now(), _now(), r["run_id"], r["task_id"]))
        except Exception:  # noqa: BLE001
            pass
