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


# ── 工作区溯源（补 is_stale 的半个盲区）────────────────────────────
# is_stale 比的是 commit 粒度的 sha。两边相等只能证明「启动之后没人提交过」，
# 证明不了「跑的就是 HEAD 的内容」—— 最危险的情形恰恰是**工作区里有未提交的
# 代码在生产跑**，此时它照样报 code_stale=False（09-23 另一会话实测报出此缺陷）。
# 本机还有条硬约束：改前必备份 ⇒ src/ 下常年躺着一堆 *.bak-*，未跟踪文件并不进
# 运行时，所以只按「已跟踪且被改」+「src 下新增未跟踪 .py（可能被 import）」计。
_DIRTY_TTL = 30.0          # 后台循环节拍参考值（main.py 里跟 vitals 同拍刷）
_dirty_cache: dict = {"at": 0.0, "counts": None}


def _is_runtime_path(path: str) -> bool:
    """这个路径的内容会不会真的进入运行时（被 import / 被服务 / 被渲染 / 被读版本）。"""
    if path == "VERSION":
        return True
    if path.startswith("src/"):
        return path.endswith(".py")
    return path.startswith(("static/", "templates/"))


def _interpret_status(lines) -> dict:
    """把 `git status --porcelain` 的行归成三类计数（纯函数，不碰盘不起子进程）。

    未跟踪项只认「真会进运行时的」：src/ 下的 .py（可能被 import）、static/ 与
    templates/ 下的任何文件（会被直接服务 / 被渲染）。
    但 *.bak-* 排除：一是不进 import，二是 P1-8 静态闸门已让它们 404，
    不排的话本机「改前必备份」的常态会把计数永久撑爆，闸门叫成灰狼。
    """
    runtime = other = untracked_code = 0
    for ln in lines or []:
        if not ln.strip():
            continue
        xy, path = ln[:2], ln[3:].strip()
        if " -> " in path:                      # 重命名：取新名
            path = path.split(" -> ", 1)[1]
        if xy == "??":
            if _is_runtime_path(path) and ".bak-" not in path:
                untracked_code += 1
            continue                            # 其余未跟踪（含 *.bak-*）不进运行时
        if _is_runtime_path(path):
            runtime += 1
        else:
            other += 1
    return {"runtime_dirty_files": runtime, "other_dirty_files": other,
            "untracked_code_files": untracked_code}


def matches_head(boot_sha: str, now_sha: str, counts) -> bool:
    """能不能自称「跑的就是 HEAD」。取不到证据一律 False（不可虚报）。

    与 is_stale 方一向相反：is_stale 宁可少报（没证据不说你旧），
    本函数宁可少应（没证据不自称新），因为这是一个**肯定式声称**。
    """
    if not boot_sha or not now_sha or boot_sha != now_sha:
        return False
    if not counts:
        return False
    return counts["runtime_dirty_files"] == 0 and counts["untracked_code_files"] == 0


def _run_status(base):
    """跑一次 git status。失败/超时 ⇒ None（调用方按 fail-closed 处理）。"""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(base), "status", "--porcelain",
                            "--untracked-files=normal"],
                           capture_output=True, text=True, timeout=3)
    except Exception:  # noqa: BLE001  git 不在/超时/权限：宁可报不可证
        return None
    if r.returncode != 0:
        return None
    return r.stdout.splitlines()


def refresh(base: Optional[Path] = None) -> Optional[dict]:
    """跑一次 git status 并刷进缓存。**只能在启动路径或后台循环里调**，
       不得进 /health 的请求路径：/health 是生命线端点，不能为了一个
       溯源字段去等一个最多 3s 的子进程（那会把 WS 帧一并卡住）。
    """
    root = Path(base) if base else _repo
    import time as _t
    if not root:
        _dirty_cache.update(at=_t.monotonic(), counts=None)
        return None
    lines = _run_status(root)
    counts = None if lines is None else _interpret_status(lines)
    _dirty_cache.update(at=_t.monotonic(), counts=counts)
    return counts


def cached_counts():
    """/health 只读这个（零子进程）。None = 从来没测到，不得肯定声称。"""
    return _dirty_cache["counts"]


def dirty_age_s() -> Optional[int]:
    """缓存多久没刷了 —— 让「陈旧证据」本身可见，而不是伪装成现测值。"""
    if _dirty_cache["counts"] is None and _dirty_cache["at"] == 0.0:
        return None
    import time as _t
    return int(_t.monotonic() - _dirty_cache["at"])



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
    refresh()          # 启动时刷一次：不开接口也不能一上来就自称「跑的是 HEAD」而无证据
    return snapshot()


def snapshot(probe_dirty: bool = True) -> dict:
    """给 /health 用的自证字段（**纯读缓存，不起子进程、不抛**）。

    probe_dirty=False ：连溯源维度都不给（code_matches_head=None）。
    工作区计数只读 refresh() 刷过的缓存；从没刷过 ⇒ counts=None ⇒
    code_matches_head=False（fail-closed：拿不到证据就不肯定声称）。
    """
    now = head_sha()
    import time as _t
    uptime = int(_t.monotonic() - _boot_mono) if _boot_wall else 0
    out = {
        "git_sha_boot": short(_boot_sha),
        "git_sha_now": short(now),
        "code_stale": is_stale(_boot_sha, now),
        "boot_at": _boot_wall.astimezone(timezone.utc).isoformat(timespec="seconds") if _boot_wall else "",
        "uptime_s": uptime,
        "pid": _pid,
        "git_repo": bool(_repo and _git_dir(Path(_repo))),
    }
    if not probe_dirty:
        out["code_matches_head"] = None
        return out
    counts = cached_counts()
    out.update(counts or {"runtime_dirty_files": None, "other_dirty_files": None,
                          "untracked_code_files": None})
    out["dirty_age_s"] = dirty_age_s()
    out["code_matches_head"] = matches_head(_boot_sha, now, counts)
    return out
