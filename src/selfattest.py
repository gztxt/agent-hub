"""自证层（P0-2）：让 /health 能回答「此刻跑的是哪一份代码」。

为什么单独一个模块（不进 main.py）：main.py 导入期就有 app/中间件/静态路由等副作用，
单元测试不该为取一个 sha 解析函数而把它拉起来。本模块**零依赖、零副作用**，
只读 .git 文件，不起子进程、不发网络、不碰数据库。

要治的病（实测，2026-09-23）：
  进程 06:50:02 启动 ⇒ /health 恒报 version=0.13.2，而 HEAD 已是 v0.13.3(1bd58e7)。
  旧 /health 只有 {status,service,version,port}，**没有任何字段**能区分
  「代码已改但服务没重启」与「服务在跑最新代码」⇒ 版本漂移完全不可见。
  这正是本项目踩过的「全绿但功能层已死」形态（同 P0-1：/health 200 而对话端点必 500）。

口径：
  code_stale = 当前 HEAD 与启动时记下的 HEAD 不一致 ⇒ 有代码改了没重启。
  取不到 sha（非 git 环境/拷出来的树）一律 **不** 报 stale —— 宁可少报，不可虚报。
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# 仓库根：src/selfattest.py 的上上级
_DEFAULT_REPO = Path(__file__).resolve().parents[1]

_repo: Optional[Path] = _DEFAULT_REPO
_boot_sha: str = ""
_boot_mono: float = 0.0
_boot_wall: Optional[datetime] = None
_pid: int = 0


def set_repo(base: Optional[Path]) -> None:
    """改仓库根（单测用）。传 None 表示「不在 git 环境里」。"""
    global _repo
    _repo = base


def _git_dir(base: Path) -> Optional[Path]:
    """.git 既可能是目录，也可能是 worktree 的指针文件（`gitdir: ...`）。"""
    dot = base / ".git"
    if dot.is_dir():
        return dot
    if dot.is_file():
        try:
            txt = dot.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if txt.startswith("gitdir:"):
            target = Path(txt.split(":", 1)[1].strip())
            return target if target.is_absolute() else (base / target).resolve()
    return None


def head_sha(base: Optional[Path] = None) -> str:
    """解析当前 HEAD 的完整 sha；拿不到就返回 ""（绝不抛）。

    覆盖三种形态：分支松散引用 / gc 后的 packed-refs / detached HEAD。
    """
    root = Path(base) if base else _repo
    if not root:
        return ""
    gitd = _git_dir(Path(root))
    if gitd is None:
        return ""
    try:
        head = (gitd / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not head:
        return ""
    if head.startswith("ref:"):
        ref = head[4:].strip()
        if not ref:
            return ""
        loose = gitd / ref
        try:
            if loose.is_file():
                sha = loose.read_text(encoding="utf-8").strip()
                if _looks_like_sha(sha):
                    return sha
        except OSError:
            return ""
        return _from_packed(gitd, ref)
    return head if _looks_like_sha(head) else ""


def _looks_like_sha(sha: str) -> bool:
    return len(sha) == 40 and all(c in "0123456789abcdef" for c in sha.lower())


def _from_packed(gitd: Path, ref: str) -> str:
    """packed-refs 行形如 `<sha> refs/heads/master`。"""
    try:
        for line in (gitd / "packed-refs").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "^")):
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1].strip() == ref and _looks_like_sha(parts[0]):
                return parts[0]
    except OSError:
        pass
    return ""


def short(sha: str, n: int = 8) -> str:
    return sha[:n] if sha else ""


def is_stale(boot_sha: str, now_sha: str) -> bool:
    """只有两边都取到了、且不相等，才算 stale。"""
    return bool(boot_sha) and bool(now_sha) and boot_sha != now_sha


def boot(base: Optional[Path] = None) -> dict:
    """服务启动时调用：记下启动那一刻的 sha 与时钟。"""
    global _boot_sha, _boot_mono, _boot_wall, _pid
    import time as _t
    if base is not None:
        set_repo(Path(base))
    _boot_sha = head_sha()
    _boot_mono = _t.monotonic()
    _boot_wall = datetime.now(timezone.utc)
    _pid = __import__("os").getpid()
    return snapshot()


def snapshot() -> dict:
    """给 /health 用的自证字段（纯读，不抛）。"""
    now = head_sha()
    import time as _t
    uptime = int(_t.monotonic() - _boot_mono) if _boot_wall else 0
    return {
        "git_sha_boot": short(_boot_sha),
        "git_sha_now": short(now),
        "code_stale": is_stale(_boot_sha, now),
        "boot_at": _boot_wall.astimezone(timezone.utc).isoformat(timespec="seconds") if _boot_wall else "",
        "uptime_s": uptime,
        "pid": _pid,
        "git_repo": bool(_repo and _git_dir(Path(_repo))),
    }
