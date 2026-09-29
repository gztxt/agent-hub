"""资源监控（v0.13.31）：列出本机所有 Agent 的进程资源占用，支持一键结束。

背景：用户要求在系统菜单添加「资源」子菜单，列出所有 Agent 的运行资源情况，
并在 Agent 名称后添加 kill 按钮，可直接结束占用资源的进程以节省本机资源。

设计：
- GET /api/resources：聚合 /proc + systemd + docker 三路发现，按 Agent 画像归类，
  返回每个 Agent 的进程列表（PID、CPU%、RSS、命令行），并计算汇总（总 CPU、总内存）。
- POST /api/resources/kill {agent_id, pid?, signal?}：结束指定 Agent 的进程。
  - 仅限 kind=agent/gateway/service/tool/memory 的画像（不含 shell 等工具卡）。
  - signal 默认 SIGTERM，可选 SIGKILL（强制）。
  - 仅允许杀当前用户拥有的进程（os.getuid() 校验），防止越权。
  - 记审计日志（hublog）。
- 复用 profiles.list_processes / running_systemd_units / docker_states 与 detect_status，
  保证与 /api/agents 的「running」判定一致。
- 无需鉴权（与 /api/agents 同口径：列表只读，写动作才要 token）；kill 由前端确认后调用。
"""

import asyncio
import os
import signal
import subprocess
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException

import profiles
import hublog

router = APIRouter(tags=["resources"])

# 进程信息采样间隔（秒）：/proc 统计 CPU% 需要两次采样
SAMPLE_INTERVAL = 0.15


def _read_all_proc_stats(pids: List[int]) -> Dict[int, dict]:
    """批量读取多个 PID 的 /proc/<pid>/stat，返回 {pid: {utime, stime, rss_pages}}。
    
    正确解析 stat 文件：comm 字段在括号内，可能包含空格和括号，不能简单 split。
    格式：pid (comm) state ... 字段索引从 1 开始，comm 后面的字段索引需减 2。
    """
    result = {}
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat", "r") as f:
                line = f.read()
            # 找到 comm 字段结束的右括号
            rparen = line.rfind(')')
            if rparen == -1:
                continue
            # comm 后的字段从 rparen+2 开始
            fields = line[rparen+2:].split()
            # 原始字段索引：14=utime, 15=stime, 24=rss -> 去掉 pid 和 (comm) 两个字段，索引 -2
            # 即：fields[11]=utime, fields[12]=stime, fields[21]=rss_pages
            if len(fields) >= 22:
                utime = int(fields[11])
                stime = int(fields[12])
                rss_pages = int(fields[21])
                result[pid] = {"utime": utime, "stime": stime, "rss_pages": rss_pages}
        except (OSError, IndexError, ValueError):
            pass
    return result


def _read_all_cmdlines(pids: List[int]) -> Dict[int, str]:
    """批量读取多个 PID 的 cmdline。"""
    result = {}
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode(errors="ignore")[:300]
            result[pid] = cmdline
        except OSError:
            result[pid] = ""
    return result


def _get_cpu_total() -> int:
    """读取 /proc/stat 第一行的总 CPU 时间。"""
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
        parts = line.split()[1:]
        return sum(int(x) for x in parts[:10])
    except (OSError, IndexError, ValueError):
        return 0


async def _run_cli(argv: List[str], timeout: float = 5.0):
    """在**线程池**里跑一条外部命令（P0-5：绝不阻塞事件循环）。

    `subprocess.run` 会同步等子进程退出，最长到 timeout 秒。调用方里有三处在
    async 函数体内直接调它（systemctl show ×N、docker inspect ×N），而
    `subprocess` 内部偶尔会等在 event loop 上；一台 docker 卡住就是整个 hub 的
    WebSocket 帧停摆 5 秒 —— 表现为「我什么都没点，终端突然卡了一下」。
    统一收口到这里：asyncio.to_thread 把阻塞搬进工作线程，事件循环全程可调度。
    返回 CompletedProcess；抛出的异常由调用方原有的 try/except 处置（语义不变）。
    """
    return await asyncio.to_thread(
        subprocess.run, argv, capture_output=True, text=True, timeout=timeout)


async def _sample_processes(pids: List[int]) -> Dict[int, dict]:
    """批量采样多个 PID，返回 {pid: {cpu_percent, rss_mb, cmdline}}。

    async 而非 def（P0-5）：内部有一次必须的采样间隔 sleep，走 await 才能让出事件循环。
    """
    if not pids:
        return {}
    # 第一次采样
    stats1 = _read_all_proc_stats(pids)
    if not stats1:
        return {}
    cmdlines = _read_all_cmdlines(list(stats1.keys()))
    total1 = _get_cpu_total()
    # P0-5：这里是唯一必须**真睡**的地方（CPU 占用率按两次采样差值算），
    # 但调用链 list_resources() 是 async —— 早先直接 time.sleep 会把整个事件循环
    # 按住 150ms：同期所有 WebSocket 帧停摆、/health 不回、其它终端看起来「卡住」。
    # 改成 await asyncio.sleep(...)：同样睡 150ms，但让出控制权给别人。
    # 这条 sleep 与 SAMPLE_INTERVAL 必须保持同一口径，否则 cpu_pct 会系统性偏。
    await asyncio.sleep(SAMPLE_INTERVAL)
    # 第二次采样
    stats2 = _read_all_proc_stats(list(stats1.keys()))
    total2 = _get_cpu_total()
    total_delta = total2 - total1

    result = {}
    ncpu = os.cpu_count() or 1
    for pid, s1 in stats1.items():
        s2 = stats2.get(pid)
        if not s2:
            continue
        proc_delta = (s2["utime"] + s2["stime"]) - (s1["utime"] + s1["stime"])
        cpu_pct = 0.0
        if total_delta > 0:
            cpu_pct = (proc_delta / total_delta) * 100 * ncpu
        rss_mb = (s2["rss_pages"] * 4096) / (1024 * 1024)
        result[pid] = {
            "cpu_percent": round(cpu_pct, 1),
            "rss_mb": round(rss_mb, 1),
            "cmdline": cmdlines.get(pid, "")
        }
    return result


def _kill_pid(pid: int, sig: int = signal.SIGTERM) -> bool:
    """尝试杀掉进程，返回是否成功。仅允许杀当前用户自己的进程。
    如果进程已不存在，视为成功（目标状态已达成）。"""
    try:
        # 先检查进程归属（/proc/<pid>/status 中的 Uid）
        with open(f"/proc/{pid}/status", "r") as f:
            for line in f:
                if line.startswith("Uid:"):
                    parts = line.split()
                    # Uid: real effective saved filesystem
                    ruid = int(parts[1])
                    if ruid != os.getuid():
                        return False
                    break
        os.kill(pid, sig)
        return True
    except (ProcessLookupError, FileNotFoundError):
        # 进程已不存在（kill 时或读取 /proc 时），目标已达成
        return True
    except (OSError, PermissionError):
        # 其他错误（如无权限、进程归属不匹配已在上面处理）
        return False


async def _collect_agent_resources() -> List[dict]:
    """聚合所有画像的进程资源信息。"""
    procs = profiles.list_processes()
    units = profiles.running_systemd_units()
    dockers = profiles.docker_states()

    # 先按画像匹配 PID，再批量采样（避免对 400+ 进程逐个 sleep）
    all_profiles = profiles.all_profiles(include_blocked=True)
    profile_pids: Dict[str, List[int]] = {}
    for p in all_profiles:
        if p["kind"] not in ("agent", "gateway", "service", "tool", "memory"):
            continue
        status = profiles.detect_status(p, procs, units, dockers)
        if status != "running":
            continue
        matched_pids: List[int] = []
        det = p.get("detect", {})
        proc_patterns = det.get("proc", [])
        systemd_units = det.get("systemd", [])
        docker_names = det.get("docker", [])

        # 1. /proc 正则匹配
        for pr in procs:
            pid = pr["pid"]
            ok = False
            for rx in proc_patterns:
                pat = __import__("re").compile(rx)
                if pat.search(pr["comm"]) or pat.search(pr["cmdline"]):
                    # MainThread 这类通用 comm 需 cmdline 佐证（与 profiles.detect_status 同口径）
                    if rx == "MainThread":
                        if ".ccr" not in pr["cmdline"] and "claude-code-router" not in pr["cmdline"]:
                            continue
                    ok = True
                    break
            if ok:
                matched_pids.append(pid)

        # 2. systemd 单元：通过 systemctl show 获取 MainPID
        for unit in systemd_units:
            if unit in units:
                try:
                    r = await _run_cli(
                        ["systemctl", "--user", "show", unit, "--property=MainPID", "--value"])
                    mp = int(r.stdout.strip())
                    if mp > 0:
                        matched_pids.append(mp)
                except Exception:
                    pass

        # 3. docker 容器：通过 docker inspect 获取 PID
        for dname in docker_names:
            if dockers.get(dname) == "running":
                try:
                    r = await _run_cli(
                        ["docker", "inspect", dname, "--format", "{{.State.Pid}}"])
                    dp = int(r.stdout.strip())
                    if dp > 0:
                        matched_pids.append(dp)
                except Exception:
                    pass

        if matched_pids:
            # 去重
            profile_pids[p["id"]] = list(set(matched_pids))

    # 批量采样所有需要的 PID
    all_pids = []
    for pids in profile_pids.values():
        all_pids.extend(pids)
    all_pids = list(set(all_pids))
    sampled = await _sample_processes(all_pids)

    # 构建结果
    results = []
    for p in all_profiles:
        if p["kind"] not in ("agent", "gateway", "service", "tool", "memory"):
            continue
        if p["id"] not in profile_pids:
            continue
        matched = []
        for pid in profile_pids[p["id"]]:
            if pid in sampled:
                # 找到原始 proc 信息
                base = next((pr for pr in procs if pr["pid"] == pid), {"pid": pid, "comm": "", "cmdline": sampled[pid]["cmdline"]})
                matched.append({**base, **sampled[pid]})

        if matched:
            total_cpu = sum(m["cpu_percent"] for m in matched)
            total_rss = sum(m["rss_mb"] for m in matched)
            results.append({
                "agent_id": p["id"],
                "agent_name": p["name"],
                "kind": p["kind"],
                "status": "running",
                "processes": matched,
                "summary": {
                    "cpu_percent": round(total_cpu, 1),
                    "rss_mb": round(total_rss, 1),
                    "count": len(matched)
                }
            })
    # 按总内存降序
    results.sort(key=lambda x: -x["summary"]["rss_mb"])
    return results


@router.get("/api/resources")
async def list_resources():
    """返回所有运行中 Agent 的资源占用列表。"""
    t0 = time.monotonic()
    data = await _collect_agent_resources()
    return {
        "ok": True,
        "agents": data,
        "total_agents": len(data),
        "total_cpu": round(sum(a["summary"]["cpu_percent"] for a in data), 1),
        "total_rss_mb": round(sum(a["summary"]["rss_mb"] for a in data), 1),
        "took_ms": round((time.monotonic() - t0) * 1000, 1)
    }


@router.post("/api/resources/kill")
async def kill_resource(
    agent_id: str = Body(...),
    pid: Optional[int] = Body(None),
    signal_name: str = Body("SIGTERM")
):
    """结束指定 Agent 的进程。
    - agent_id: 画像 id
    - pid: 可选，指定杀某个 PID；不传则杀该 Agent 下所有进程
    - signal_name: SIGTERM(默认) 或 SIGKILL
    """
    # 校验 agent_id 存在且 kind 允许
    prof = profiles.get_profile(agent_id)
    if not prof or prof["kind"] not in ("agent", "gateway", "service", "tool", "memory"):
        raise HTTPException(404, "Agent not found or not killable")

    sig = signal.SIGTERM
    if signal_name.upper() == "SIGKILL":
        sig = signal.SIGKILL

    # 收集目标进程
    procs = profiles.list_processes()
    units = profiles.running_systemd_units()
    dockers = profiles.docker_states()
    status = profiles.detect_status(prof, procs, units, dockers)
    if status != "running":
        raise HTTPException(409, "Agent not running")

    det = prof.get("detect", {})
    proc_patterns = det.get("proc", [])
    systemd_units = det.get("systemd", [])
    docker_names = det.get("docker", [])

    target_pids: List[int] = []

    # /proc 匹配 —— 与 _collect_agent_resources() 的采集侧**逐字同口径**（P0-4）。
    # 旧实现在 kill 侧少了 MainThread 的 cmdline 佐证，而采集侧有 ⇒ 同一进程在
    # 「列资源」时被正确排除，在「点结束」时却被匹配上。后果是**误杀**：CCR 画像里
    # 带 MainThread 模式时，任何 comm=MainThread 的进程（实测含 WorkBuddy 桌面端
    # 等 Electron 应用）只要 cmdline 里出现 ccr 字样就中枪。资源页越点越危险。
    for pr in procs:
        ok = False
        for rx in proc_patterns:
            pat = __import__("re").compile(rx)
            if pat.search(pr["comm"]) or pat.search(pr["cmdline"]):
                # MainThread 这类通用 comm 需 cmdline 佐证（与采集侧、profiles.detect_status 同口径）
                if rx == "MainThread":
                    if ".ccr" not in pr["cmdline"] and "claude-code-router" not in pr["cmdline"]:
                        continue
                ok = True
                break
        if ok:
            target_pids.append(pr["pid"])

    # systemd
    for unit in systemd_units:
        if unit in units:
            try:
                r = await _run_cli(
                    ["systemctl", "--user", "show", unit, "--property=MainPID", "--value"])
                mp = int(r.stdout.strip())
                if mp > 0:
                    target_pids.append(mp)
            except Exception:
                pass

    # docker
    for dname in docker_names:
        if dockers.get(dname) == "running":
            try:
                r = await _run_cli(
                    ["docker", "inspect", dname, "--format", "{{.State.Pid}}"])
                dp = int(r.stdout.strip())
                if dp > 0:
                    target_pids.append(dp)
            except Exception:
                pass

    # 去重
    target_pids = list(set(target_pids))

    # 如果指定了 pid，过滤
    if pid is not None:
        if pid not in target_pids:
            raise HTTPException(404, "PID not belong to this agent")
        target_pids = [pid]

    if not target_pids:
        raise HTTPException(409, "No processes to kill")

    killed = []
    failed = []
    for tp in target_pids:
        if _kill_pid(tp, sig):
            killed.append(tp)
            hublog.log("resource_kill", f"killed pid={tp} agent={agent_id} signal={signal_name}")
        else:
            failed.append(tp)
            hublog.log("resource_kill_failed", f"failed pid={tp} agent={agent_id} signal={signal_name}")

    return {
        "ok": len(failed) == 0,
        "agent_id": agent_id,
        "signal": signal_name,
        "killed": killed,
        "failed": failed
    }