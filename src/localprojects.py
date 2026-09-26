"""本机项目清单（v0.13.30）：多根 git 仓库扫描 + 合并 CloudCLI 项目。

背景：用户要在 hub 里「自动检索和加载显示本机所有项目（不限目录）」，点项目名称、
选 agent、新建会话直达终端。仓库里此前唯一的项目清单是 v0.13.29 的
`/api/cloudcli/projects`（只读 cloudcli 自己的 auth.db）——覆盖不到没在 cloudcli
开过会话的目录（如 agent-router、WorkBuddy）。本模块补上**文件系统侧**的发现：
`GET /api/localprojects` 在若干根目录下找 git 仓库，再与 cloudcli 项目合并去重。

设计：
- 纯 os.walk 手动剪枝（不 spawn git 子进程——term.py 的"全仓唯一拉起路径"纪律
  同样适用于这里，且 walk 比 git ls-projects 免 per-repo fork）；
- 跳过点开头目录（.hermes/.claude/.nvm 等工具内部仓不是"项目"）与
  node_modules/venv 一类依赖目录；深度上限 3（实测三根全量 find maxdepth 3 仅
  93ms，剪枝后同量级）；
- 三个根默认 /home/gztxt、/fs/1000/ftp/技术文档、/vol1/@apphome，
  env LOCALPROJECT_ROOTS 可覆盖（逗号分隔）；
- 根目录不存在/不可读 ⇒ 记入 errors（「查不了」不能糊成「没有」的四态口径，
  与 cloudcli._list_projects 同型）；cloudcli 库读不到时该项目源整体降级，
  git 部分照常。

安全模型：
- GET 只读目录名/路径/时间戳，不读文件内容；不鉴权（与 /api/agents、
  /api/cloudcli/projects 同口径——列表是控制平面入口，写动作才要 token）；
- 不埋 runlog（同 /api/agents 防噪声口径）。
"""
from __future__ import annotations

import datetime
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter

import cloudcli

router = APIRouter(tags=["localprojects"])

#: 扫描根（逗号分隔，env 可覆盖）。默认三根覆盖本机绝大多数项目落点。
ROOTS: List[str] = [r.strip() for r in os.getenv(
    "LOCALPROJECT_ROOTS", "/home/gztxt,/fs/1000/ftp/技术文档,/vol1/@apphome").split(",") if r.strip()]

#: 深度上限：根本身算第 0 层，3 层足够覆盖 <root>/<分组>/<项目> 形态且不扫穿 node_modules 山脉
MAX_DEPTH = 3

#: 依赖/构建产物目录名：下钻即浪费（里面不会有用户项目）
EXCLUDE_DIR_NAMES = {"node_modules", "venv", ".venv", "__pycache__", "target", "dist", ".cache"}

#: 返回条目上限（防异常目录树拖垮响应；实测 git 仓约 48 + cloudcli 29 ≈ 60）
LIMIT = 256


def _is_git_dir(d: Path) -> bool:
    """普通仓库 .git 是目录；git worktree 的 .git 是一页文本文件——都算。"""
    g = d / ".git"
    return g.is_dir() or g.is_file()


def _scan_roots(roots: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """在根列表下找 git 仓库。返回 (projects, errors)；缺根/不可读进 errors 不静默。"""
    projects: List[Dict[str, Any]] = []
    errors: List[str] = []
    for raw in roots:
        root = Path(raw).expanduser()
        if not root.is_dir():
            errors.append(f"根目录不存在: {root}")
            continue
        try:
            it = os.walk(str(root), followlinks=False)
            for dirpath, dirnames, filenames in it:
                cur = Path(dirpath)
                if _is_git_dir(cur):
                    rel = cur.relative_to(root)
                    if len(rel.parts) > 0:          # 根自身是仓库时由去重层决定归属
                        g = cur / ".git"
                        try:
                            ts = os.path.getmtime(g)
                        except OSError:
                            ts = 0.0
                        projects.append({
                            "name": cur.name,
                            "path": str(cur),
                            "git": True,
                            "last_ts": ts,
                        })
                # 剪枝：点目录（.git 内部、工具隐藏仓）、依赖目录、超深
                depth = len(cur.relative_to(root).parts)
                dirnames[:] = [
                    d for d in dirnames
                    if not d.startswith(".") and d not in EXCLUDE_DIR_NAMES
                ] if depth < MAX_DEPTH else []
        except OSError as e:
            errors.append(f"根目录不可读: {root}（{type(e).__name__}）")
    return projects, errors


def _merge_cloudcli(git_projects: List[Dict[str, Any]],
                    errors: List[str]) -> List[Dict[str, Any]]:
    """并入 cloudcli 项目（精确名/会话数/最近活动），按 normpath 去重。
    cloudcli 库读不到（ok:false）⇒ 整源降级，errors 点名，git 部分照常。"""
    cc = cloudcli._list_projects()
    if not cc.get("ok"):
        errors.append("CloudCLI 项目源不可用：" + str(cc.get("error") or "?"))
        cc_projects: List[Dict[str, Any]] = []
    else:
        cc_projects = cc.get("projects") or []

    by_path: Dict[str, Dict[str, Any]] = {}
    for p in git_projects:
        key = os.path.normpath(p["path"])
        by_path[key] = dict(p, git=True, cloudcli=False, sessions=0,
                            last_activity=None)

    for p in cc_projects:
        key = os.path.normpath(p.get("path") or "")
        if not key:
            continue
        la = p.get("last_activity")
        la_ts = 0.0
        if la:
            try:
                la_ts = datetime.datetime.fromisoformat(la).timestamp()
            except ValueError:
                la_ts = 0.0
        cur = by_path.get(key)
        if cur:                       # 双源命中：cloudcli 的精确名与活跃度胜出
            cur["name"] = p.get("name") or cur["name"]
            cur["cloudcli"] = True
            cur["sessions"] = int(p.get("sessions") or 0)
            cur["last_activity"] = la
            cur["last_ts"] = max(cur.get("last_ts") or 0.0, la_ts)
        else:
            by_path[key] = {
                "name": p.get("name") or os.path.basename(key),
                "path": p.get("path") or key,
                "git": _is_git_dir(Path(key)),
                "cloudcli": True,
                "sessions": int(p.get("sessions") or 0),
                "last_activity": la,
                "last_ts": la_ts,
            }
    return list(by_path.values())


def _list_projects() -> Dict[str, Any]:
    """git 扫描 + cloudcli 合并去重，按最近活动倒序。"""
    t0 = time.monotonic()
    errors: List[str] = []
    git_projects, errors = _scan_roots(ROOTS)
    merged = _merge_cloudcli(git_projects, errors)
    merged.sort(key=lambda p: (-(p.get("last_ts") or 0.0), str(p.get("name") or "")))
    out = merged[:LIMIT]
    return {
        "ok": True,
        "count": len(out),
        "projects": [{k: p.get(k) for k in
                      ("name", "path", "git", "cloudcli", "sessions", "last_activity")}
                     for p in out],
        "roots": list(ROOTS),
        "errors": errors,
        "took_ms": round((time.monotonic() - t0) * 1000, 1),
    }


@router.get("/api/localprojects")
async def local_projects():
    """本机项目清单（git 扫描 + cloudcli 合并；直读，不鉴权不埋点）。
    单资源端点：路径就是 /api/localprojects，前端 api('/api/localprojects') 一步到位。"""
    return _list_projects()
