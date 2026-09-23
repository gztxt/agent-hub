"""自证层（P0-2）：让 /health 能回答「此刻跑的是哪一份代码」。

为什么单独一个模块（不进 main.py）：main.py 导入期就有 app/中间件/静态路由等副作用，
单元测试不该为取一个 sha 解析函数而把它拉起来。本模块**零依赖、零副作用**，
只读 .git 文件，不起子进程、不发网络、不碰数据库。

要治的病（实测，2026-09-23）：
  进程 06:50:02 启动 ⇒ /health 恒报 version=0.13.2，而 HEAD 已是 v0.13.3(1bd58e7)。
  旧 /health 只有 {status,service,version,port}，**没有任何字段**能区分
  「代码已改但服务没重启」与「服务在跑最新代码」⇒ 版本漂移完全不可见。
  这正是本项目踩过的「全绿但功能层已死」形态（同 P0-1：/health 200 而对话端点必 500）。

口径（**09-24 改语义**：比内容，不比 commit 指针）：
  needs_restart        = 启动那一刻的工作区内容指纹 != 当前工作区内容指纹  ⇒ 真需要重启。
  running_matches_head = 启动那一刻的内容指纹 == HEAD 里同批文件的内容指纹 ⇒ 跑的就是 HEAD 的内容。
  code_stale           = needs_restart 的**兼容别名**（老消费方字段名不变）。
  git_sha_boot/git_sha_now 保留，但只作溯源信息，**不再参与任何判定**。

为什么必须改（实测事故，09-23→09-24）：旧语义比的是 commit 指针，而本机工序是
「带未提交改动重启 → 实弹验证 → 通过才提交」⇒ 每次提交后 boot 指针必然落后一格，
`code_stale` 长期假红（09-23 就为此产生 3 例 verify_prod_smoke 假 FAIL，实为记账口径）。

**为什么 static/templates 不进指纹**：这两类每次请求从磁盘直读，改完不必重启即生效
（实测：进程 boot 在更早的 commit，而服务出参 hub.js 的 md5 与磁盘逐字节相等）。
算进去就等于永久假红。

取不到证据一律不肯定声称：boot 指纹为空（进程启动于本机制之前、非 git 环境、拷出来的树）
⇒ `needs_restart`/`running_matches_head` 给 **None** + `code_stale_reason`，绝不给 False 蒙人。
"""
import hashlib   # 09-24 内容指纹层需要；本模块原先没有（第一次跑闸门就炸出 NameError）
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
_boot_fp: str = ""   # 启动那一刻运行时文件的内容指纹（09-24 新增）


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


_fp_cache = {"at": 0.0, "wt": None, "head": None}   # 内容指纹缓存（与 _dirty_cache 同一节奏刷）


def runtime_paths(base: Path) -> list:
    """进指纹的文件清单：只有 `src/**.py`（会被 import 进进程、改了必须重启的那些）。

    刻意**不含** static/ 与 templates/ —— 它们每次请求从磁盘直读，不需要重启。
    """
    out = []
    for p in sorted((base / "src").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        out.append(p.relative_to(base).as_posix())
    return out


def git_blob_sha(data: bytes) -> str:
    """按 git 的 hash-object 语义算 blob sha ⇒ 与 `git ls-tree` 给的 objectname 同一坐标系。"""
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def worktree_blobs(base: Path) -> dict:
    """当前工作区每个运行时文件的 blob sha。读不动（权限/竞态删文件）⇒ 该路径记 "unreadable"，
    宁可让指纹变化可见，也不静默当成"没改"。"""
    out = {}
    for rel in runtime_paths(base):
        try:
            out[rel] = git_blob_sha((base / rel).read_bytes())
        except Exception:  # noqa: BLE001
            out[rel] = "unreadable"
    return out


def head_blobs(base: Path) -> Optional[dict]:
    """HEAD 里同批文件的 blob sha（一条 ls-tree 拿全，**不** cat-file 读历史对象）。
       非 git 环境 / git 不可用 / 超时 ⇒ None（调用方按 fail-closed 处理）。"""
    if not base or not _git_dir(Path(base)):
        return None
    import subprocess
    try:
        # ★不用 `--format`：git 的 --format 不解释 	（实测输出成字面 "src/a.py\tb917..."），
        #   按真 tab 切会全部跳过 ⇒ head_fp 永远为空 ⇒ running_matches_head 永远"不可判定"，
        #   而且长得像"设计如此"。默认输出 `mode type sha	path` 里的 tab 是真的（cat -A 见 ^I）。
        r = subprocess.run(["git", "-C", str(base), "ls-tree", "-r", "HEAD", "--", "src"],
                           capture_output=True, text=True, timeout=3)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    return _parse_ls_tree(r.stdout)
    return out


STALE_UNKNOWN_REASON = ("boot 内容指纹不可用（进程启动于本机制之前，或不在 git 环境里）"
                       "⇒ 需下次重启后才可判，这里不给 False 蒙人")


def _parse_ls_tree(text: str) -> dict:
    """解析 `git ls-tree -r HEAD -- src` 的**默认输出**：`mode type sha	路径`。

    只收 blob + `.py` + 排除 `__pycache__`。分隔符必须是真 tab —— git 的 `--format` 不解释
    `	`（实测输出字面 "src/a.py\tb917..."），那种写法会让本函数返回 {}，从而让
    running_matches_head 永远"不可判定"且看起来像设计如此（09-24 实测抓到过）。
    """
    out = {}
    for line in (text or "").splitlines():
        if "	" not in line:
            continue
        meta, path = line.split("	", 1)
        parts = meta.split()
        if len(parts) != 3:
            continue
        _mode, typ, sha = parts
        if typ != "blob" or not path.endswith(".py") or "__pycache__" in path:
            continue
        out[path] = sha
    return out


def needs_restart(boot_fp: str, wt_fp: str, head_fp: str) -> Optional[bool]:
    """真"需要重启"：boot 时的运行时字节 != 当前工作区字节。

    返回 None = 不可判定（没有 boot 指纹），**绝不**因为 commit 指针动了就报 True。
    """
    if not boot_fp:
        return None
    if not wt_fp:
        return None          # 当前工作区指纹也拿不到 ⇒ 同样不可判定，不猜
    return wt_fp != boot_fp


def running_matches_head(boot_fp: str, head_fp: str) -> Optional[bool]:
    """肯定式声称：跑着的字节内容 == HEAD 的内容。缺任一证据 ⇒ None（不可虚报）。"""
    if not boot_fp or not head_fp:
        return None
    return boot_fp == head_fp


def content_fp(blobs) -> str:
    """把 {路径: blob sha} 压成一个短指纹。**顺序无关**（排序后拼接），空清单 ⇒ ""。"""
    if not blobs:
        return ""
    h = hashlib.sha1()
    for k in sorted(blobs):
        h.update(("%s\0%s\n" % (k, blobs[k])).encode())
    return h.hexdigest()[:12]


def fp_age_s() -> Optional[int]:
    if _fp_cache["wt"] is None and _fp_cache["at"] == 0.0:
        return None
    import time as _t
    return int(_t.monotonic() - _fp_cache["at"])


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
    # 内容指纹与脏度同节奏刷新（都在 to_thread 里，绝不在 /health 请求路径上算）
    try:
        _fp_cache.update(at=_t.monotonic(), wt=worktree_blobs(root), head=head_blobs(root))
    except Exception:  # noqa: BLE001  指纹拿不到就报 None，不影响脏度字段
        _fp_cache.update(at=_t.monotonic(), wt=None, head=None)
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
    global _boot_sha, _boot_mono, _boot_wall, _pid, _boot_fp
    import time as _t
    if base is not None:
        set_repo(Path(base))
    _boot_sha = head_sha()
    # ★记的是**启动那一刻工作区字节**的指纹，不是 commit 指针。这样"带未提交改动重启、
    #   之后再提交"就不会被误报成 stale（09-24 改语义的全部动机）。
    _boot_fp = content_fp(worktree_blobs(Path(_repo))) if _repo else ""
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
    wt_fp = content_fp(_fp_cache["wt"]) if _fp_cache.get("wt") else ""
    head_fp = content_fp(_fp_cache["head"]) if _fp_cache.get("head") else ""
    # 三条正交判据：需不需要重启 / 跑的是不是 HEAD 的内容 / sha 只作溯源
    nr = needs_restart(_boot_fp, wt_fp, head_fp)
    rmh = running_matches_head(_boot_fp, head_fp)
    reason = "" if nr is not None else STALE_UNKNOWN_REASON
    out = {
        "git_sha_boot": short(_boot_sha),        # ↓ 这两个自 09-24 起**只是溯源信息**
        "git_sha_now": short(now),
        "code_fp_boot": _boot_fp or None,
        "code_fp_worktree": wt_fp or None,
        "code_fp_head": head_fp or None,
        "needs_restart": nr,
        "running_matches_head": rmh,
        "code_stale_reason": reason or None,
        "code_stale": bool(nr),        # 兼容别名：不可判定按 False 报，但 reason 里写明
        "code_fp_age_s": fp_age_s(),
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
    # 同一件事的内容指纹版：优先给指纹版结论，sha 版保留一版做兼容对照
    out["code_matches_head_content"] = rmh
    return out
