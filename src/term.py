"""Web 终端（P2-B：hub 自建 pty + WebSocket + xterm.js）

安全模型：
- 可执行命令 = profiles 画像白名单（terminal.cmd），API 只接受 agent_id，绝不接受任意命令
- v0.13.0 续聊：命令仍只出自画像白名单 + 后端模板（sessions_store.resume_argv），
  客户端最多传一个过正则且实盘存在的 session id，传不进命令
- 会话仅创建者主机可见（服务本身 LAN/Tailscale 信任域）；可选 TERM_TOKEN 鉴权（ws ?token=）
- 空闲 TTL（默认 45min）自动回收；hub 重启即全部销毁（无残留 shell）
"""
import asyncio
import errno
import hmac
import json
import os
import pty
import secrets
import shlex
import signal
import struct
import termios
import time
import uuid
import fcntl
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

import modelcfg
import profiles
import sessions_store
import term_record

router = APIRouter()

TERM_TOKEN = os.getenv("TERM_TOKEN", "")
if not TERM_TOKEN:
    # 兜底：未配置 token 时自动生成，避免"空 token=不鉴权"的裸奔状态
    TERM_TOKEN = secrets.token_urlsafe(32)
    print(f"[term] TERM_TOKEN 未配置，已自动生成随机 token（前4位={TERM_TOKEN[:4]}，len={len(TERM_TOKEN)}）")
IDLE_TTL_S = int(os.getenv("TERM_IDLE_TTL", "2700"))
MAX_SESSIONS = 8
# 回收心跳间隔（P0-4）：早先 _reap() 只寄生在 list_sessions() 上，前端一关就没人回收
REAP_INTERVAL_S = float(os.getenv("TERM_REAP_INTERVAL", "60"))
# P0-3：PTY 写背压时的让出时长。5ms 与 coalescer 窗口同量级——短到用户无感，
# 长到足以让事件循环去处理别的 IO（否则忙等反而更糟）。
_WRITE_BACKPRESSURE_S = 0.005
# P0-3：这些 errno 是「对端还活着，只是现在写不进去」⇒ 重试而不是断链。
_WRITE_RETRY_ERRNOS = frozenset({errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR})
# P0-2：连续多少次「PTY 读端可读、但读到 EIO」才判定子进程已死并收尸。
# 单次 EIO 就回收是错的——见 _reap_once 的注释（EIO 也可能是对端刚写完最后一个
# 字节的瞬时态）。这里给 3 次连续确认，配合 REAP 间隔，最坏多占 3 分钟配额。
_REAP_EOF_CONFIRM = 3


# ===== 子进程即时感知（PT-20260929-02）=====
# 问题：子进程若零输出即死，PTY master 仅产生 POLLHUP，asyncio 的
# add_reader（仅监 EPOLLIN）不触发回调 ⇒ hub 无感知路径，exit_status 恒 None、
# alive 恒 True、前端回放空 ring ⇒ 用户看到永久空白终端。
# 修法：后台任务以短周期（默认 2s）跑 waitpid(-1, WNOHANG) 全量扫描，
# 任意子进程一退出立刻把对应 Session 标记为死，写入 exit_status，触发清理。
# 该任务与现有 60s 的 reap_loop 并行，互不干扰、各司其职。
_REAP_CHILD_POLL_S = float(os.getenv("TERM_REAP_CHILD_POLL", "2.0"))
# 允许把子进程轮询完全关掉（设 0），仅依赖 60s reap_loop —— 兼容旧行为



# 内存耗尽时 bun/JSC 会**主动** abort：ASSERTION FAILED: MemoryExhaustion →
# JSC::LocalAllocator::allocateSlowCase 调 __builtin_trap() → ud2 → SIGILL。
# 内核只记一行 `trap invalid opcode`，终端上原本只显示「[会话结束]」⇒ 用户无从得知
# 是内存问题（2026-09-25 排查 opencode 菜单秒退时实测：dmesg + objdump + ulimit -v
# 三步才定性，全程 hub 没有任何提示）。SIGKILL 同理：可能是 OOM-killer 也可能是 hub 强杀。
_MEM_SUSPECT_SIGNALS = frozenset({signal.SIGILL, signal.SIGSEGV, signal.SIGBUS,
                                  signal.SIGABRT, signal.SIGKILL})


def describe_exit(status: Optional[int], hub_killed: bool = False) -> str:
    """把 waitpid 的 status 解成人话（纯函数，供单测直接喂值）。

    hub_killed：本进程是不是 hub 自己动的手（用户点 × / 空闲 TTL / 服务退出）。
    SIGKILL 与 SIGTERM 是**歧义信号** —— 既可能是内核 OOM-killer，也可能是我们自己发的。
    知道是自己发的就如实说，绝不把"用户主动关会话"渲染成"内存不足"吓人。

    返回**纯文本**片段（不含 ANSI，调用方自己包样式）；无信息时返回空串，
    调用方据此回落到原来的「[会话结束]」，绝不编造原因。
    """
    if status is None:
        return ""
    try:
        if os.WIFSIGNALED(status):
            sig = os.WTERMSIG(status)
            try:
                name = signal.Signals(sig).name
            except ValueError:
                name = f"signal {sig}"
            if hub_killed and sig in (signal.SIGTERM, signal.SIGKILL):
                return f"由 hub 主动终止（{name}）"
            hint = "（疑似内存不足）" if sig in _MEM_SUSPECT_SIGNALS else ""
            return f"被信号 {name}({sig}) 终止{hint}"
        if os.WIFEXITED(status):
            code = os.WEXITSTATUS(status)
            return "正常退出" if code == 0 else f"退出码 {code}"
    except (ValueError, OSError):
        return ""
    return ""


def _check_term_token(provided: str, source: str) -> None:
    """缺 token 即拒：校验失败记一行拒绝原因（绝不记录 token 值）"""
    if not provided or not hmac.compare_digest(provided, TERM_TOKEN):
        print(f"[term] 拒绝：token 校验失败（{source}）")
        raise HTTPException(status_code=401, detail="term token required or invalid")


def child_env() -> Dict[str, str]:
    """终端子进程的环境（纯函数，供单测直接断言）。

    PATH 必须前置 `profiles.extra_path_dirs()`：hub 服务进程 PATH 不含 nvm，而
    `pi` 这类 `#!/usr/bin/env node` 的 npm 全局 CLI 会让内核拿系统 node v20 去跑
    需要 Node 22+ 的 bundle ⇒ 启动即 SyntaxError（详见 profiles.extra_path_dirs）。
    只补子进程、不动服务进程自身 PATH、不动 systemd 单元配置。"""
    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    extra = profiles.extra_path_dirs()
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


class Session:
    def __init__(self, sid: str, agent_id: str, cmd: List[str], cwd: str):
        self.id = sid
        self.agent_id = agent_id
        self.cmd = cmd
        self.cwd = cwd
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # 子进程：替换为终端程序
            try:
                env = child_env()
                os.chdir(cwd)
                os.execvpe(cmd[0], cmd, env)
            except Exception as exc:  # noqa: BLE001
                # 09-30 可观测性：原实现 `os._exit(127)` 把死因**完全吞掉**——
                # 父进程只看到「PTY 零字节 + 干净关闭」，与「CLI 自己崩了」同签名，
                # 于是无法区分「exec 失败」和「CLI 启动即退」。现在双写：PTY（用户可见）
                # + stdout（可 grep）。成本仅在失败路径。
                import traceback
                print("[term] 子进程 exec 失败 cmd=%r cwd=%r: %s"
                      % (cmd[0], cwd, traceback.format_exc()), flush=True)
                try:
                    os.write(2, ("\r\n[hub: 无法启动 %s —— %s]\r\n" % (cmd[0], exc)).encode())
                except OSError:
                    pass
                os._exit(127)
        self.alive = True
        self.created = time.time()
        # 两个时钟刻意分开（v0.13.58 / 档二）：
        #   last_io      = 客户端最后一次**真实交互**（心跳不算）。只用于「用户多久没动手」的展示。
        #   last_activity = 会话最后一次**有生命迹象**（客户端交互 或 pty 产出）。
        # 旧实现只有一个时钟且被回收逻辑当「死活」判据 ⇒ 换设备/切后台时客户端静默
        # 45min，正在跑的任务被腰斩（实弹：重连收到 4410）。两个时钟分开后，
        # 回收只看 last_activity，「客户端不在」不再等价于「会话该死」。
        self.last_io = time.time()
        self.last_activity = time.time()
        self.cols, self.rows = 80, 24
        # 每个观看者一条**独立**队列（P1-5）。
        # 旧做法：全会话共用一个 outputs 队列，而每个 WS 连接的 pump 都在同一个队列上 get()
        # ⇒ 两台设各（桌面 + 手机）同时看同一会话时，两个消费者会**互相偷字节**，
        #   各自只拿到一半输出（流被劈成两半，不是「少看到一些」而是内容永久错乱）。
        self.viewers: Dict[str, asyncio.Queue] = {}
        # 每个观看者一个输出合并器（B1）：与 viewers 同生共死，键同为 vid。
        # 放在「读一块就发一帧」之前，PTY 突发输出才不会被原样放大成 WS 帧洪水。
        self.coalescers: Dict[str, "_OutputCoalescer"] = {}
        self.dropped: Dict[str, int] = {}   # 观看者 -> 累计丢弃字节（慢消费者必须可见）
        self.ring = bytearray()  # 输出环形缓冲：重连回放，避免"重挂后白屏"
        self._cleaned = False    # 资源回收幂等守卫
        self.exit_status = None  # waitpid 原始 status；解出人话见 describe_exit()
        self.hub_killed = False  # True = 这次是 hub 自己动的手（点 × / TTL / 服务退出）
        self.resume_of = ""     # v0.13.0：非空 = 由某条磁盘历史续聊而来
        fcntl.fcntl(self.fd, fcntl.F_SETFL, os.O_NONBLOCK)

    def to_dict(self):
        return {"id": self.id, "agent_id": self.agent_id, "cmd": " ".join(self.cmd),
                "cwd": self.cwd, "alive": self.alive,
                "created": self.created, "resume_of": self.resume_of,
                "exit_reason": describe_exit(self.exit_status, self.hub_killed),
                "idle_s": round(time.time() - self.last_io),
                # activity_s：距上次「有生命迹象」的秒数。idle_s 涨而 activity_s 不涨
                # = 客户端不在、agent 也没在跑（这才是真该回收的形态）。
                # getattr 兜底同 _reap()：有测试用 Session.__new__ 绕过 __init__ 造实例，
                # 读路径不该因为少一个时钟字段就 AttributeError（契约不该由实现细节决定）。
                "activity_s": round(time.time() - getattr(
                    self, "last_activity", self.last_io))}

    def _signal_group(self, sig):
        """pty.fork 子进程是会话首进程（pgid=pid）→ 组灭可带走它派生的子进程"""
        try:
            os.killpg(self.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(self.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass

    def poll_exited(self) -> bool:
        """非阻塞探活（P0-2）：子进程是否已经退出。退出则顺带记录 exit_status。

        为什么必须用 waitpid 而不能只看 PTY 可读性：
        「PTY 读端可读」既可能是对端退出了，也可能是对端刚写了数据、也可能对端还活着
        只是暂时没输出——三种情况在 fd 层同签名。waitpid(WNOHANG) 是唯一能区分
        「进程还在」与「进程没了」的探针，而且不阻塞、不依赖有没有观看者。
        已经退出时把状态记进 exit_status，让 describe_exit() 能把死因上屏。
        """
        if self.exit_status is not None:
            return True                     # 已有结论：不必再探（_cleanup 已收尸过则此处无副作用）
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except (ChildProcessError, ProcessLookupError):
            # 没有这个子进程（已被 _cleanup 收走或压根没起来）⇒ 视同已退出
            self.exit_status = self.exit_status if self.exit_status is not None else -1
            return True
        except OSError:
            return False                    # 真·暂时查不到：保守当作还活着，下轮再探
        if pid == 0:
            return False                    # WNOHANG 返回 0 = 还在跑
        self.exit_status = status           # 已退出：记下原因（首次记录优先）
        return True

    def kill(self):
        """TUI 常忽略 SIGHUP：SIGTERM → 2s 后仍活则 SIGKILL 升级（组级）"""
        self.hub_killed = True   # 之后看到的 SIGTERM/SIGKILL 是我们自己发的，不是 OOM
        self._signal_group(signal.SIGTERM)
        try:
            loop = asyncio.get_event_loop()
            loop.call_later(2.0, self._force_kill)
        except RuntimeError:
            pass

    def _force_kill(self):
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid == 0:  # 还活着 → 强杀整组
                self._signal_group(signal.SIGKILL)
            elif self.exit_status is None:
                self.exit_status = status   # 已自行退出：留下面因（首次记录优先，不被后续覆盖）
        except (ChildProcessError, ProcessLookupError, PermissionError):
            pass
        # 升级后收尾：短暂宽限再收尸并释放资源（不依赖列表还在展示它）
        try:
            loop = asyncio.get_event_loop()
            loop.call_later(1.5, self._cleanup)
        except RuntimeError:
            self._cleanup()

    def _cleanup(self):
        """幂等回收：摘 reader → 关 fd → 收尸。任何路径（正常退出/组灭/reap）都走这里"""
        if self._cleaned:
            return
        self._cleaned = True
        self.alive = False
        _rec = getattr(self, "recorder", None)
        if _rec is not None:
            _rec.close()   # 落 ended + 跑 7 天保留期清理
        try:
            asyncio.get_event_loop().remove_reader(self.fd)
        except Exception:  # noqa: BLE001
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        for _ in range(2):
            try:
                pid, st = os.waitpid(self.pid, os.WNOHANG)
                if pid == 0:
                    break
                if self.exit_status is None:
                    self.exit_status = st   # 原为 `_st` 直接丢弃 ⇒ 崩溃原因永远上不了屏
            except (ChildProcessError, ProcessLookupError, OSError):
                break


_sessions: Dict[str, Session] = {}
#: 被显式杀掉、等待 reap_loop 兜底收尾的会话（key=id(sess) 防 sid 复用撞车）。
#: 与 _sessions 分开：已杀的会话不能继续出现在列表/配额里，但资源还得有人收。
_dying: Dict[int, "Session"] = {}


# 输出合并窗口（毫秒）。5ms 是 paseo 实测值（docs/terminal-performance.md:5-16），
# 不是随手取的：再大就会让按键回显可见地发黏，再小则合并不住 npm run build 这种
# 持续高频吐字节的场景。
TERM_COALESCE_MS = 5.0


class _OutputCoalescer:
    """前后沿节流的输出合并器（思路取自 paseo TerminalOutputCoalescer，按 Hub 形态重写）。

    为什么不能只用后沿（普通 debounce）：那样**每次**按键回显都会被平白加一个窗口的
    延迟（paseo 文档原话：reverting to trailing-only adds a full window to every
    keystroke echo）。前沿规则是：距上次刷出已超过 delay ⇒ 立刻发，交互延迟不升反降。

    部署位置：挂在「PTY 读回调 → 每观看者队列」之间，即离 PTY 最近的那一层。
    挪到更下游（比如 WS 发送前）只是换了个挨打的地方——主循环照样被高频唤醒。
    """

    def __init__(self, on_flush, delay_ms: float = TERM_COALESCE_MS):
        self._on_flush = on_flush
        self._delay = delay_ms / 1000.0
        self._buf = bytearray()
        self._timer = None          # asyncio TimerHandle；非 None = 正在攒一趟
        self._last = None           # 上次刷出的时刻（前沿判定用）

    def handle(self, data: bytes) -> None:
        if not data:
            return
        self._buf += data
        if self._timer is not None:
            return                  # 已在攒，等那个定时器统一刷
        now = time.monotonic()
        if self._last is None or now - self._last >= self._delay:
            self.flush()            # 前沿：空闲后的第一块立刻发（保按键回显）
            return
        self._timer = asyncio.get_event_loop().call_later(self._delay, self.flush)

    def flush(self) -> None:
        """立刻刷出缓冲。带外消息（退出提示等）发送前必须调它，否则会插到输出前面。"""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if not self._buf:
            return
        payload = bytes(self._buf)
        self._buf.clear()
        self._last = time.monotonic()
        self._on_flush(payload)

    def close(self) -> None:
        """观看者离开时收尸：定时器不取消的话会一直攥着 loop 的引用。"""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._buf.clear()


# sid -> 持有尺寸所有权的观看者 vid。用 vid 字符串而非对象引用：
# Python 的弱引用语义与 paseo 的 WeakRef<object> 不同，拿 vid 记账更简单也更可查。
_size_owner: Dict[str, str] = {}

# 行列上限。pty 尺寸是 struct.pack("HHHH")，超过 65535 会抛 struct.error
# 打断整条 WS 收包循环；下界 1 是防止 0 行让 TUI 直接崩。
_ROWS_RANGE = (1, 500)
_COLS_RANGE = (2, 1000)


def _clamp(v, lo: int, hi: int) -> Optional[int]:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, n))


def _norm_intent(raw) -> str:
    """归一化尺寸意图。

    缺省（老客户端没有 intent 字段）⇒ claim：否则一次前后端版本错配就会让尺寸永远改不动
    （paseo 同口径，terminal-size-ownership.ts:32-38）。
    未知的非空值 ⇒ update（保守）：宁可要它先拿到所有权，也不能让一个看不懂的字段
    绕过所有权检查直接改尺寸。
    """
    if raw is None or str(raw).strip() == "":
        return "claim"
    s = str(raw).strip().lower()
    return s if s in ("claim", "update") else "update"


def _apply_size(sess: Session, vid: str, rows, cols, intent: str) -> bool:
    """按所有权语义改 PTY 尺寸；返回是否真的改了（未改/被拒都返回 False）。

    纯记账 + 一次 ioctl，不抛异常：前端几何事件是高频且不可信的输入，
    让它炸掉 WS 收包循环等于把「拖一下窗口」变成「终端断开」。
    """
    # 只有 claim 能夺权；**其余一切**（含看不懂的意图）都要求「本端已是所有者」，
    # 否则一个手滑拼错的字段就等于把所有权检查整个旁路掉。
    if intent == "claim":
        _size_owner[sess.id] = vid        # claim 无条件夺权（含同尺寸也要转移）
    elif _size_owner.get(sess.id) != vid:
        return False                      # 非所有者：静默忽略（这就是要防的"偷尺寸"）
    r = _clamp(rows, *_ROWS_RANGE)
    c = _clamp(cols, *_COLS_RANGE)
    if r is None or c is None:
        return False
    if sess.rows == r and sess.cols == c:
        return False                      # 尺寸未变：不打扰 pty（等价于 paseo 的服务端短路）
    try:
        fcntl.ioctl(sess.fd, termios.TIOCSWINSZ, struct.pack("HHHH", r, c, 0, 0))
    except OSError:
        return False
    sess.rows, sess.cols = r, c
    return True


class CreateIn(BaseModel):
    agent_id: str
    session_id: Optional[str] = None    # v0.13.0：续聊某条历史；仅接受形状合法且实盘存在的 id
    cwd: Optional[str] = Field(default=None, max_length=500)   # v0.13.30：本机项目页——pty 起在指定项目目录；只进 os.chdir，绝不进命令拼装


def _cwd_or_none(raw: Optional[str]) -> Optional[str]:
    """body.cwd 的唯一入口校验（v0.13.30）。
    空/None → None（回落画像 cwd）；非空必须：绝对路径、实盘目录存在、
    不含 \\x00\\n;|&\\$\\`（与 resume_argv 的兜底栅栏同口径）。
    只返回已校验的字符串——create_session 里不许出现第二条读 body.cwd 的路径。"""
    if raw is None or not raw.strip():
        return None
    c = raw.strip()
    if not os.path.isabs(c):
        raise ValueError(f"cwd 必须是绝对路径: {c!r}")
    if any(ch in c for ch in "\x00\n;|&$`"):
        raise ValueError("cwd 含可疑字符")
    if not os.path.isdir(c):
        raise ValueError(f"cwd 目录不存在: {c}")
    return c





@router.post("/api/term/sessions")
async def create_session(body: CreateIn, request: Request):
    _check_term_token(request.headers.get("x-term-token", "")
                      or request.query_params.get("token", ""), "POST /api/term/sessions")
    prof = profiles.get_profile(body.agent_id)
    if not prof or not prof.get("terminal"):
        raise HTTPException(400, f"{body.agent_id} 无终端入口（仅画像白名单可拉起）")
    if len([s for s in _sessions.values() if s.alive]) >= MAX_SESSIONS:
        raise HTTPException(429, f"终端会话数达上限 {MAX_SESSIONS}")
    # v0.13.30：body.cwd 是新会话的起点目录（本机项目页）；校验失败 400。
    # cwd 只进 os.chdir（Session 子进程），绝不进命令拼装——命令仍只出自画像白名单。
    try:
        req_cwd = _cwd_or_none(body.cwd)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    cwd = req_cwd or prof["terminal"].get("cwd") or os.path.expanduser("~")
    if body.session_id:
        # 客户端只能给 id：命令仍由后端模板拼装，id 必须过形状正则 + 实盘存在双校验
        try:
            cmd = sessions_store.resume_argv(prof["id"], body.session_id, cwd)
        except ValueError as e:
            if "不在实盘清单" in str(e):
                raise HTTPException(404, str(e)) from e
            raise HTTPException(400, str(e)) from e
    else:
        cmd = shlex.split(prof["terminal"]["cmd"])
    if body.session_id:
        # 跳目录口径（09-22 裁定）之后，历史条目可能属于别的目录：pty 要起在**会话自己的 cwd**，
        # 否则等于"在技术文档目录里打开一条 agent-hub 的会话"。session_cwd 已校验目录真实存在。
        cwd = sessions_store.session_cwd(prof["id"], body.session_id, cwd)
    resolved = profiles.which(cmd[0])
    if not resolved:
        raise HTTPException(400, f"命令 {cmd[0]} 未在本机找到")
    cmd[0] = resolved
    # v0.13.41：设置→模型里给该 agent 存过默认模型 ⇒ 按白名单追加 --model。
    # v0.13.51：续聊**同样**追加（此前只对的新会话追加）。原因见 modelcfg.terminal_argv：
    # 不带 --model 的启动走的是 agent 配置文件，而 CCR 每次启动会把 claude 的
    # env 三兄弟改回旧模型 ⇒ 续聊"重启后退回 qwen"正是这条缝。逐家 `--help` 实测
    # --model/-m 都在（codex 的 `resume` 子命令 help 里也有 -m），追加在模板末尾
    # 不动 resume 的位置参数；白名单外的 agent 一律返回 []，绝不猜 flag。
    cmd += modelcfg.terminal_argv(prof["id"], modelcfg.hub_model(prof["id"]))
    sid = uuid.uuid4().hex[:10]
    sess = Session(sid, prof["id"], cmd, cwd)
    sess.resume_of = body.session_id or ""
    _sessions[sid] = sess
    # v0.13.59 录制器（PT-20260927-16）：TERM_RECORD 缺省 0 ⇒ active=False，全程零开销。
    # Recorder 只吃 pty 字节与客户端按键，从签名上够不到 child_env()（env 里有 TERM_TOKEN）。
    sess.recorder = term_record.Recorder(sid, prof["id"])
    _attach_reader(sess)
    # P0-8：live_titles 实测 jcode 分支要 json.load 159 个文件/29MB（冷缓存 130ms+），
    # 跑在 async handler 里会按住整个事件循环。丢进线程池，标题晚几十毫秒无所谓，
    # 终端卡一下很在意。
    title = (await asyncio.to_thread(sessions_store.live_titles_cached, prof["id"]) or {}).get(sess.pid, "")
    if not title and sess.resume_of:     # 刚起来时各 CLI 未必已登记 pid，直接按 id 查盘上标题
        title = await asyncio.to_thread(sessions_store.title_for, prof["id"], sess.resume_of, cwd)
    return {"session": dict(sess.to_dict(), title=title)}


@router.get("/api/term/sessions")
async def list_sessions(request: Request):
    # P1-7：列表本身是**控制平面入口** —— 它漏 sid + agent_id + cmd + cwd + alive，
    # 而 DELETE 以前不鉴权 ⇒ 任何能撑到 3102 的一方（单前 **绑定 0.0.0.0**，局域网可达）
    # 可以「列 → 拿 sid → 杀」接力杀掉别人正在跑的会话。只堵 DELETE 不堵 GET 等于没堵。
    _check_term_token(request.headers.get("x-term-token", "")
                      or request.query_params.get("token", ""), "GET /api/term/sessions")
    _reap()
    # 只展示活会话：已退出记录不再以"僵尸条目"出现（历史改由 /api/term/history 从磁盘直读）
    titles: Dict[str, Dict[int, str]] = {}
    out = []
    for s in _sessions.values():
        if not s.alive:
            continue
        if s.agent_id not in titles:
            # P0-8：同上，走线程池 + 指纹缓存，别把列表轮询变成阻塞源。
            titles[s.agent_id] = await asyncio.to_thread(
                sessions_store.live_titles_cached, s.agent_id) or {}
        t = titles[s.agent_id].get(s.pid, "")
        if not t and s.resume_of:      # pid 反查不到（jcode 只在退出时写 last_pid、codex/qoder 无映射）
            t = await asyncio.to_thread(              # P0-8：同上
                sessions_store.title_for, s.agent_id, s.resume_of, s.cwd)   # 按 resume_of 直查盘上标题
        out.append(dict(s.to_dict(), title=t))
    return {"sessions": out}


@router.get("/api/term/history/{agent_id}")
async def agent_history(agent_id: str, request: Request, limit: int = Query(default=3, ge=1, le=20)):
    _check_term_token(request.headers.get("x-term-token", "")
                      or request.query_params.get("token", ""), "GET /api/term/history")
    prof = profiles.get_profile(agent_id)
    if not prof or not prof.get("terminal"):
        raise HTTPException(400, f"{agent_id} 无终端入口，谈不上续聊历史")
    if not sessions_store.supports(prof["id"]):
        raise HTTPException(400, f"{agent_id} 无历史会话仓库")
    cwd = prof["terminal"].get("cwd") or os.path.expanduser("~")
    return dict(sessions_store.list_history(prof["id"], cwd, limit), agent=prof["id"])


@router.get("/api/term/recording/{sid}")
async def get_recording(sid: str, request: Request,
                        limit: int = Query(default=2000, ge=1, le=20000)):
    """回放某会话的录制（PT-20260927-16）。

    **按「含用户键入内容 = 敏感」定鉴权，不按「只读 = 宽松」定**：拿得到录制就等于拿到了
    别人刚刚敲的每一行 —— 与 09-23「写端点不设防」收口同一口径，fail-closed 走 term token。
    """
    _check_term_token(request.headers.get("x-term-token", "")
                      or request.query_params.get("token", ""), "GET /api/term/recording")
    out = term_record.status(sid)
    out["tape"] = term_record.frames(sid, limit)
    return out


@router.delete("/api/term/sessions/{sid}")
async def kill_session(sid: str, request: Request):
    # P1-7：杀掉别的会话 = 服务打断，与“建会话”同级危险（建已经被闸了）。
    # 前端六个调用点本来就带 termHeaders()，故此处只补服务端，不会造成界面断流。
    _check_term_token(request.headers.get("x-term-token", "")
                      or request.query_params.get("token", ""), "DELETE /api/term/sessions/{sid}")
    # 先摘出登记表 → 列表/新 WS 立即看不到（❌ 即时生效）；进程灭杀与资源回收走 kill→_force_kill→_cleanup
    sess = _sessions.pop(sid, None)
    if not sess:
        raise HTTPException(404, "session not found")
    sess.kill()
    # 旧实现只 pop + kill，把「关 fd / 摘 add_reader / 收尸」全交给 kill 内的
    # call_later(2s)→_force_kill→call_later(1.5s)。正常路径没事，但**服务正在关闭**
    # 时 loop 不再跑这些定时器 ⇒ fd 与 reader 泄漏（hub 重启即清，问题不大）；
    # 更常见的是 WS 仍挂着时用户连点多次 ×。这里显式登记一条「已被显式杀掉」的会话，
    # 由 reap_loop 兜底收尾，不依赖定时器一定跑得起来。
    _dying[id(sess)] = sess
    return {"status": "killed", "id": sid}


# ── P0-1：last_io 的「什么才算交互」唯一判据 ──────────────────
# 前端 15s 一帧心跳（02-nav-and-poll.js:206 TERM_HB_SEND_MS）在旧实现里每次 receive
# 都无条件续 last_io ⇒ 45min TTL 形同虚设。这里把判据收敛成两个函数，
# _reap() / to_dict() / 前端「空闲多久」展示全部复用，杜绝各算各的。
_HEARTBEAT_TYPES = frozenset({"hb", "ping", "pong", "ack"})


def _touch(sess: "Session", text: str) -> None:
    """收到一帧客户端文本：只有**非心跳、非回执**的内容才算用户交互。

    同时续 last_activity（会话活着）；last_io（客户端静默）语义不变。

    - resize：用户拖窗口 ⇒ 真交互，必须续命（否则拖窗口看源码会被 TTL 杀掉）。
    - 其余文本帧（输入的按键/粘贴）⇒ 真交互。
    - hb/ping/pong/ack ⇒ 链路保活，不续命（这正是 P0-1 的病根）。
    纯心跳帧不抛异常、不记日志——它每 15s 来一次，日志会被刷爆。
    """
    t = text.strip()
    if not t:
        return
    if t[0] not in "{[":
        _mark_interaction(sess)           # 非 JSON 的裸帧：保守当交互
        return
    try:
        j = json.loads(t)
    except (json.JSONDecodeError, ValueError):
        _mark_interaction(sess)           # 解析不了当输入处理，不因格式怪就丢 TTL
        return
    if not isinstance(j, dict):
        _mark_interaction(sess)
        return
    if str(j.get("type") or "") in _HEARTBEAT_TYPES:
        return                             # 心跳：不续命（P0-1 的病根）
    _mark_interaction(sess)


def _mark_interaction(sess: "Session") -> None:
    """一次真实交互：续 last_io（客户端静默）与 last_activity（会话存活）。"""
    now = time.time()
    sess.last_io = now
    sess.last_activity = now


def _touch_bytes(sess: "Session") -> None:
    """收到二进制帧：xterm 的输入走 data 通道 ⇒ 一定是交互，两个时钟都续。"""
    _mark_interaction(sess)


def _reap():
    """回收两件事，缺一不可（这两条曾各自单独失效 ⇒ 配额被占死）：

    ① **进程已退但登记还在**：PTY 对端关闭后，内核把 master fd 标为可读且 read() 返回
       0 或 EIO。若这条链路没人触发（没有观看者 WS ⇒ 没有 pump；前端只有心跳 ⇒
       也没有人发数据），`add_reader` 回调也可能因 `os.read` 返回空而早退 ⇒
       session 永远 alive=True 地留在 `_sessions`，一直占 MAX_SESSIONS(8) 的名额，
       用户表现为「开过几个终端之后再也开不出新的」。

    ② **无生命迹象超 TTL**（v0.13.58 档二改判据）：判据是 last_activity 且要求**无人观看**。
       「没人观看」用 `viewers` 为空判断，而不是「客户端静默」——
       客户端静默只是「这台设备不在」，不等于「没人要这个会话」。

    P0-2 的修法：这里主动 waitpid(WNOHANG) 探活——它是唯一不依赖「有没有人在看」
    的探针。子进程一退出，waitpid 立刻返回 pid，登记当轮就被摘掉。

    ② 判据为什么换过（实弹证据，不是推断）：
       旧口径 `now - s.last_io > TTL` 把「客户端静默」当死活判据，实测在
       TERM_IDLE_TTL=45s 的实例上：agent 明明每 2s 在产出（idle_s 涨到 40s），
       会话仍被回收，另一端重连直接收到 4410（已结束）。
       用户要求的语义是「状态跨客户端连续」⇒ 客户端不在 ≠ 会话该死。
       现在：**pty 有产出**（_attach_reader 续 last_activity）或**有人看着**（viewers 非空）
       都不会被回收；只有「没人看 + 进程活着但 pty 长时间一个字节都不吐」才收。
    """
    now = time.time()
    for s in list(_sessions.values()):
        if not s.alive:
            _drop(s)
            continue
        # ① 探活（P0-2）：waitpid 能独立回答「子进程还在不在」，不依赖有没有人看。
        # getattr 兜底是为了让轻量 stub（tests/test_term_reaper._Stub 只暴露
        # id/alive/last_io/kill/_cleanup）也能过 —— 契约不该由实现细节决定。
        probe = getattr(s, "poll_exited", None)
        if callable(probe) and probe():
            _drop(s)
            continue
        # ② 无生命迹象超 TTL（档二）：三个条件同时成立才回收。
        #    watched：有观看者 ⇒ 有人在用，绝不回收（哪怕客户端静止不动）。
        #    activity：last_activity 比 last_io 更能代表「会话活着」；
        #      老 stub 没有这个字段 ⇒ getattr 兜底退回 last_io（契约由实现细节决定不得人心）。
        watched = bool(getattr(s, "viewers", None))
        activity = getattr(s, "last_activity", None)
        base = s.last_io if activity is None else max(activity, s.last_io)
        if watched:
            continue
        if now - base > IDLE_TTL_S:
            s.kill()


def _drop(s: "Session") -> None:
    """把一条会话从登记表里摘掉并回收资源（幂等）。"""
    s._cleanup()
    _sessions.pop(s.id, None)


async def reap_loop():
    """独立回收心跳（P0-4）。

    缺陷根因（实测）：`_reap()` 全仓唯一调用点是 `list_sessions()`，而 startup 只建了
    sweep_stale_tasks / vitals_loop 两个后台任务 ⇒ 浏览器一关就再没人调
    GET /api/term/sessions ⇒ 45min 空闲 TTL 形同虚设、死会话不从 _sessions 摘除，
    MAX_SESSIONS(8) 被占满后新终端直接 429「开不出来」。
    回收必须由服务自己按时做，不能依赖有没有人在看。
    单轮异常不许打死循环 —— 否则又回到「静默不回收」。
    """
    while True:
        await asyncio.sleep(REAP_INTERVAL_S)
        try:
            _reap()
            _reap_dying()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[term] reap_loop 异常（下轮重试）：{type(e).__name__}: {e}", flush=True)


async def _child_reap_loop() -> None:
    """子进程即时感知轮询（PT-20260929-02）。

    以短周期（默认 2s，可用 TERM_REAP_CHILD_POLL=0 关闭）跑 waitpid(-1, WNOHANG)
    全量扫描。任何子进程一退出，立刻把对应 Session 的 exit_status 写入、
    alive 置 False、触发 _cleanup —— 解决「零输出即死、add_reader 捕捉不到
# POLLHUP」导致的永久空白终端与假存活。

    该任务与 60s 的 reap_loop 并行，互不干扰：
    - reap_loop：TTL 回收、配额释放、已死会话的最终兜底
    - _child_reap_loop：子进程退出的即时感知与 exit_status 回填
    """
    if _REAP_CHILD_POLL_S <= 0:
        print("[term] 子进程即时感知已关闭（TERM_REAP_CHILD_POLL <= 0）", flush=True)
        return
    print(f"[term] 子进程即时感知已启动，轮询间隔 {_REAP_CHILD_POLL_S}s", flush=True)
    while True:
        await asyncio.sleep(_REAP_CHILD_POLL_S)
        try:
            _reap_all_children()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[term] _child_reap_loop 异常（下轮重试）：{type(e).__name__}: {e}", flush=True)


def _reap_all_children() -> None:
    """跑一次 waitpid(-1, WNOHANG) 扫描所有已退出子进程，标记对应 Session。

    关键点：waitpid(-1, ...) 返回的是**任意**已退出子进程的 pid/status，
    不局限于某个 Session 自家的 pid。这能捕获那些「零输出即死」
    而 add_reader 永不触发的会话。
    """
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            # 没有子进程了
            break
        except OSError:
            # 暂时不可用，下轮再试
            break
        if pid <= 0:
            # 没有已退出的子进程
            break
        # 找到对应的 Session
        sess = None
        for s in _sessions.values():
            if s.pid == pid:
                sess = s
                break
        if sess is None:
            # 可能是 _dying 里的，或者已被清理
            for s in _dying.values():
                if s.pid == pid:
                    sess = s
                    break
        if sess is None:
            # 完全找不到（可能是外部进程、或已清理），记录忽略
            print(f"[term] 回收到未知子进程 pid={pid} status={status}", flush=True)
            continue
        # 只在首次记录时写入（poll_exited / _cleanup 也会写，保持首次优先）
        if sess.exit_status is None:
            sess.exit_status = status
        sess.alive = False
        print(f"[term] 即时感知：sid={sess.id} pid={pid} exit_status={status} → alive=False", flush=True)
        # 触发清理（幂等，_cleanup 内会 remove_reader、close fd、收尸）
        sess._cleanup()
        _sessions.pop(sess.id, None)


def _reap_dying() -> None:
    """收尾已被显式杀掉的会话：进程确实没了就回收资源；还在等 SIGTERM 升级就跳过。"""
    for key, sess in list(_dying.items()):
        if sess.poll_exited():
            sess._cleanup()
            _dying.pop(key, None)


def alive_count() -> int:
    """活会话数（自证端点与限流判据共用一个口径，别再各算各的）"""
    return len([s for s in _sessions.values() if s.alive])


def idle_max_s() -> int:
    """最久没有生命迹象（无输出且无人观看）的活会话闲置秒数（判断 TTL 有没有真的在跑）

    口径必须与 _reap() 的判据一致（档二）：报 last_io 而回收看 last_activity，
    会让 /health 的自证字段证明「一条正在被回收的会话也不忙」⇒ 假绿。
    """
    live = [time.time() - max(getattr(s, "last_activity", s.last_io), s.last_io)
            for s in _sessions.values() if s.alive]
    return int(max(live)) if live else 0


def _attach_reader(sess: Session):
    loop = asyncio.get_event_loop()
    # 09-30 可观测性：本次接入的读侧统计（只存在于 attach 后的这条闭包里）
    _stat = {"reads": 0, "bytes": 0, "sent": 0}

    def on_readable():
        try:
            _stat["calls"] = _stat.get("calls", 0) + 1
            if _stat["calls"] <= 3:
                import select as _sel
                _rd, _, _ = _sel.select([sess.fd], [], [], 0)
                print("[term] on_readable 第%d次进入 sid=%s 可读=%s 即将 os.read"
                      % (_stat["calls"], sess.id, bool(_rd)), flush=True)
            data = os.read(sess.fd, 65536)
            if data:
                _stat["reads"] += 1
                _stat["bytes"] += len(data)
                if _stat["reads"] <= 3:
                    print("[term] on_readable sid=%s 第%d次读 %d 字节（ring 现在 %d，viewers=%d）"
                          % (sess.id, _stat["reads"], len(data), len(sess.ring), len(sess.viewers)),
                          flush=True)
                # 档二关键一行：pty 有产出 = 会话活着，续 last_activity。
                # 刻意**不**续 last_io —— 「agent 在跑」不等于「用户刚敲过键盘」，
                # 两者混成一个时钟才会把正在跑的任务判成空闲（见 __init__ 注释）。
                sess.last_activity = time.time()
                rec = getattr(sess, "recorder", None)
                if rec is not None:
                    rec.feed("out", data)   # 脱敏在 Recorder 内部、落盘前完成
                sess.ring.extend(data)
                if len(sess.ring) > 65536:
                    del sess.ring[:len(sess.ring) - 65536]
                # 不再「读一块发一块」：交给各自的合并器攒一趟（前沿立刻刷 / 后沿攒 5ms）。
                # 合并器内部才 put 进队列 —— 队列帧数下降与 WS 帧数下降是同一件事。
                for co in list(sess.coalescers.values()):
                    co.handle(data)
        except (OSError, BlockingIOError) as e:
            if isinstance(e, OSError) and e.errno in (errno.EIO, errno.EBADF):
                print("[term] on_readable 命中 EIO/EBADF sid=%s errno=%s ⇒ 判定子进程已死"
                      % (sess.id, e.errno), flush=True)
                sess.alive = False
                sess._cleanup()  # 摘 reader + 关 fd + 收尸（幂等，替代原散落逻辑）
                # _cleanup() 已记下 exit_status ⇒ 这里能把「为什么没了」一起说出来。
                # 原样只有一句「[会话结束]」，用户看到的就是"点一下闪退、什么都不告诉我"。
                reason = describe_exit(sess.exit_status, sess.hub_killed)
                tail = f"\r\n\x1b[90m[进程 {reason}]\x1b[0m" if reason else ""
                # 保序（P1-1）：[会话结束] 是带外消息，必须先让合并器把攒着的输出落进
                # 队列，才能排到它后面。不 flush 的话退出提示会插在最后一段输出**之前**，
                # 表现为「屏幕上一半输出提示在前、内容在后」这类极难复现的错乱。
                for co in list(sess.coalescers.values()):
                    co.flush()
                # P1：旧写法 try 包住整个 for ⇒ **第一个**观看者队列满就跳过其余全部，
                # 后来的观看者永远收不到「[会话结束]」，表现为「第二条终端不提示已结束、
                # 画面停住」。改成逐个 try，满了记丢弃、其余照常送达。
                end_payload = ("\x1b[?25h\r\n[会话结束]" + tail).encode()
                for vid, q in list(sess.viewers.items()):
                    try:
                        q.put_nowait(end_payload)
                    except asyncio.QueueFull:
                        sess.dropped[vid] = sess.dropped.get(vid, 0) + len(end_payload)

    # 09-30 可观测性：读侧挂载快照。ring 当时的长度 = 「从建会话起已经攒下多少字节」；
    # 后续 WS 接入时若 ring 仍为 0，说明 PTY 一个字节都没被读走（空白终端的判定点）。
    print("[term] 挂读侧 sid=%s pid=%s fd=%s ring=%d 字节"
          % (sess.id, sess.pid, sess.fd, len(sess.ring)), flush=True)
    loop.add_reader(sess.fd, on_readable)
    print("[term] add_reader 已挂 sid=%s" % sess.id, flush=True)


@router.websocket("/ws/term/{sid}")
async def term_ws(ws: WebSocket, sid: str, token: str = Query(default="")):
    # 缺 token 即拒：query ?token= 与 header X-TERM-TOKEN 两种都接受
    provided = token or ws.headers.get("x-term-token", "")
    if not provided or not hmac.compare_digest(provided, TERM_TOKEN):
        print("[term] 拒绝：ws 握手 token 校验失败（/ws/term）")
        # WS 握手阶段不能用 HTTPException（Starlette 在 ws 上下文不可靠），显式关闭 4401=未授权
        await ws.close(code=4401)
        return
    sess = _sessions.get(sid)
    reject = None
    if not sess:
        reject = 4404
    elif not sess.alive:
        # 死会话不再服务：避免"回放旧画面+输入无效"的假加载（4410=已结束）
        reject = 4410
    if reject is not None:
        # 业务码必须在 accept() **之后** close。早先是在 accept 前 close：
        # Starlette 此时只会回一个 HTTP 403 拒掉握手 ⇒ 浏览器侧看到的是 1006
        # （异常关闭），与"链路断了"同签名 —— 前端就会对着一条已不存在的 sid
        # 无限重连。实测（pre-accept 版）：websockets 客户端 InvalidStatus、
        # http=403、close code = None。4401（鉴权失败）仍留在 accept 前：
        # 不给未授权方完成握手。
        await ws.accept()
        await ws.close(code=reject)
        return
    await ws.accept()
    # 每个观看者一条**独立**队列（P1-5）。旧做法全会话共用一个 outputs 队列，
    # 而每条 WS 的 pump 都在同一个队列上 get() ⇒ 桌面 + 手机同看一条会话时，
    # 两个消费者会互相偷字节，各自只拿到一半流（内容永久错乱，不是“少看几行”）。
    vid = uuid.uuid4().hex[:8]
    vq: asyncio.Queue = asyncio.Queue(maxsize=2000)
    sess.viewers[vid] = vq

    def _enqueue(payload: bytes) -> None:
        """合并器的出口：入本观看者的队列。慢消费者照旧记账，绝不静默吞字节。"""
        try:
            vq.put_nowait(payload)
        except asyncio.QueueFull:
            sess.dropped[vid] = sess.dropped.get(vid, 0) + len(payload)

    sess.coalescers[vid] = _OutputCoalescer(_enqueue)
    # 09-30 可观测性：本观看者的发送统计
    _stat = {"sent": 0}
    # 回放最近输出（重连不白屏）
    # 09-30 可观测性：ring 长度与回放结果一并上屏。ring=0 ⇒ PTY 真的没产出过任何字节，
    # 这时空白就不是「回放漏了」而是「CLI 侧没画出来」，两者的修法完全不同。
    if sess.ring:
        try:
            await ws.send_bytes(bytes(sess.ring))
            print("[term] 回放 sid=%s vid=%s ring=%d 字节已发出" % (sess.id, vid, len(sess.ring)),
                  flush=True)
        except Exception as e:  # noqa: BLE001
            print("[term] 回放失败 sid=%s vid=%s: %r" % (sess.id, vid, e), flush=True)
    else:
        print("[term] 回放 sid=%s vid=%s ring 为空（PTY 至今零产出）" % (sess.id, vid), flush=True)
    # 输出泵：queue → ws；带超时兜底，进程死亡且队列排空即收口，绝不无限挂起
    async def pump():
        print(f"[term] pump 启动 sid={sess.id} vid={vid}", flush=True)
        while True:
            try:
                print(f"[term] pump 等待数据 sid={sess.id} alive={sess.alive}", flush=True)
                data = await asyncio.wait_for(vq.get(), timeout=2.0)
                print(f"[term] pump 收到数据 sid={sess.id} len={len(data)} alive={sess.alive}", flush=True)
                _stat["sent"] += 1
                if _stat["sent"] <= 3:
                    print("[term] pump sid=%s 第%d帧下发 %d 字节" % (sess.id, _stat["sent"], len(data)),
                          flush=True)
            except asyncio.TimeoutError:
                print(f"[term] pump 超时 sid={sess.id} alive={sess.alive}", flush=True)
                if not sess.alive:
                    break
                continue
            except asyncio.CancelledError:
                print(f"[term] pump 被取消 sid={sess.id}", flush=True)
                raise
            except Exception as e:
                print(f"[term] pump 异常 sid={sess.id}: {type(e).__name__}: {e}", flush=True)
                break
            # 慢消费者必须可见（P1-5）：丢了就明说，绝不静默吞字节
            lost = sess.dropped.pop(vid, 0)
            if lost:
                try:
                    await ws.send_bytes(("\r\n\x1b[90m[hub: 输出过快，已丢弃 %d 字节]\x1b[0m\r\n"
                                          % lost).encode())
                except Exception:  # noqa: BLE001
                    break
            # 真正修：send_bytes 必须 try（手机断网/切网络 → WS 已断 → 1006）
            # 直接尝试发送；WS 已关闭时 send_bytes 抛 WebSocketDisconnect
            try:
                await ws.send_bytes(data)
            except Exception:  # noqa: BLE001
                # WS 已关闭：把数据写入临时文件，供后续回放取用
                import tempfile, os
                _tmp = tempfile.mktemp(suffix=".term", dir=os.environ.get("HUB_DATA_DIR", "/tmp"))
                try:
                    with open(_tmp, "ab") as f:
                        f.write(data)

                except Exception:  # noqa: BLE001
                    pass
                break
            if not sess.alive:
                # 进程已死：发送退出提示并关闭 WS，然后返回（handler 会收到 WebSocketDisconnect）
                reason = describe_exit(sess.exit_status, sess.hub_killed)
                tail = f" ({reason})" if reason else ""
                exit_payload = ("\r\n\x1b[90m[process exited" + tail + "]\x1b[0m").encode()

                try:
                    await ws.send_bytes(exit_payload)
                    # 给客户端一点时间读取数据帧，再发关闭帧
                    await asyncio.sleep(0.5)
                    await ws.close(code=4410)
                except Exception as e:  # noqa: BLE001
                    print(f"[term] pump 退出提示发送失败 sid={sess.id}: {e}", flush=True)
                return
        # 正常退出（WS 已由上方 return 关闭）
    pump_task = asyncio.create_task(pump())
    try:
        while True:
            msg = await ws.receive()
            # P0-1：last_io 只认「真实交互」，**不信心跳**。
            # 前端每 15s 发一帧 {"type":"hb"}（02-nav-and-poll.js:206），而旧实现在
            # 每次 receive 后无条件续 last_io ⇒ 45min TTL 对任何还开着的终端**永不触发**，
            # 「空闲自动回收」这个承诺实际是废的。这里只在有意义的帧上续命：
            #   resize = 用户拖了窗口；其余文本帧按内容判（hb/回执不算）。
            # 判据放在 _touch() 里，_reap() 与空闲统计共用同一口径。
            if msg.get("text") is not None:
                _touch(sess, msg["text"])
            elif msg.get("bytes"):
                _touch_bytes(sess)
            if msg.get("text") is not None:
                try:
                    j = json.loads(msg["text"])
                    if j.get("type") == "resize":
                        # 尺寸所有权（B2 / paseo 融合）：任何连接都能改尺寸 ⇒ 后台的手机端
                        # 一次 ResizeObserver 就能把桌面上正开着的 vim 压扁，
                        # 用户看到的是「我什么都没做，终端自己乱了」，极难归因。
                        #   claim  = 「我是主人，按我的尺寸来」（无条件夺权，含同尺寸）
                        #   update = 「我只是几何变了」；非所有者一律静默忽略
                        # 老客户端没有 intent 字段 ⇒ 缺省按 claim 处理（与 paseo 同口径），
                        # 否则一次前后端版本错配就会让尺寸永远改不动。
                        _apply_size(sess, vid, j.get("rows"), j.get("cols"),
                                    _norm_intent(j.get("intent")))
                        continue
                    if j.get("type") == "hb":
                        # 应用层心跳（P1-1 前端自愈靠它识半开连接）：只回执，
                        # **绕不写进 pty** —— 否则每秒往终端里灌垃圾。
                        try:
                            await ws.send_text(json.dumps({"type": "hb", "t": time.time()}))
                        except Exception:  # noqa: BLE001
                            break
                        continue
                    data = str(j.get("data", "")).encode()
                except (json.JSONDecodeError, KeyError):
                    data = msg["text"].encode()
            else:
                data = msg.get("bytes") or b""
            if data and sess.alive:
                rec = getattr(sess, "recorder", None)
                if rec is not None:
                    # 录在写之前：PTY 背压把尾巴丢了(EAGAIN 分支)也不改「用户敲过什么」这个事实
                    rec.feed("in", data)
                try:
                    os.write(sess.fd, data)
                except BlockingIOError:
                    # P0-3：BlockingIOError **是** OSError 的子类 ⇒ 旧 `except OSError: break`
                    # 把「PTY 缓冲区瞬时写不进去（EAGAIN，正常的背压）」当成致命错误，
                    # 直接 break 掉整个收包循环 ⇒ 用户表现为「敲键盘偶尔就掉线」。
                    # 正确处置：让出事件循环重试，而不是杀连接。fd 是 O_NONBLOCK 的，
                    # 这里改成 await sleep 让 add_reader 再来；剩余尾巴丢了也比断线好
                    # （真丢内容前端会有回显错位，但链路不断）。
                    await asyncio.sleep(_WRITE_BACKPRESSURE_S)
                except OSError as e:
                    # 真死信号（EIO/EBADF：pty 对端已关）才收口。
                    if e.errno not in _WRITE_RETRY_ERRNOS:
                        break
                    await asyncio.sleep(_WRITE_BACKPRESSURE_S)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        # 先等 pump 完成清理（发送退出提示、关闭 WS），再拆观看者
        try:
            await pump_task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass
        sess.viewers.pop(vid, None)
        sess.dropped.pop(vid, None)
        # 所有者走了必须交还所有权：留着悬挂的 vid 会让**所有**客户端的 update 全被忽略，
        # 表现就是「重连之后尺寸再也改不动了」。
        if _size_owner.get(sid) == vid:
            _size_owner.pop(sid, None)
        co = sess.coalescers.pop(vid, None)
        if co is not None:
            co.close()   # 定时器不收会让 loop 一直攥着一个已离开的观看者


def kill_all():
    """服务退出：立即 TERM+KILL 双发（组级），不等 call_later（loop 即将关闭）"""
    for s in _sessions.values():
        s.hub_killed = True      # 同上：别让"服务重启"被报成"内存不足"
        s._signal_group(signal.SIGTERM)
        s._signal_group(signal.SIGKILL)
