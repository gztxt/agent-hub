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

import profiles
import sessions_store

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
            except Exception:  # noqa: BLE001
                os._exit(127)
        self.alive = True
        self.created = time.time()
        self.last_io = time.time()
        self.cols, self.rows = 80, 24
        # 每个观看者一条**独立**队列（P1-5）。
        # 旧做法：全会话共用一个 outputs 队列，而每个 WS 连接的 pump 都在同一个队列上 get()
        # ⇒ 两台设各（桌面 + 手机）同时看同一会话时，两个消费者会**互相偷字节**，
        #   各自只拿到一半输出（流被劈成两半，不是「少看到一些」而是内容永久错乱）。
        self.viewers: Dict[str, asyncio.Queue] = {}
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
                "idle_s": round(time.time() - self.last_io)}

    def _signal_group(self, sig):
        """pty.fork 子进程是会话首进程（pgid=pid）→ 组灭可带走它派生的子进程"""
        try:
            os.killpg(self.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(self.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass

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
    sid = uuid.uuid4().hex[:10]
    sess = Session(sid, prof["id"], cmd, cwd)
    sess.resume_of = body.session_id or ""
    _sessions[sid] = sess
    _attach_reader(sess)
    title = (sessions_store.live_titles(prof["id"]) or {}).get(sess.pid, "")
    if not title and sess.resume_of:     # 刚起来时各 CLI 未必已登记 pid，直接按 id 查盘上标题
        title = sessions_store.title_for(prof["id"], sess.resume_of, cwd)
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
            titles[s.agent_id] = sessions_store.live_titles(s.agent_id) or {}
        t = titles[s.agent_id].get(s.pid, "")
        if not t and s.resume_of:      # pid 反查不到（jcode 只在退出时写 last_pid、codex/qoder 无映射）
            t = sessions_store.title_for(s.agent_id, s.resume_of, s.cwd)   # 那就按 resume_of 直查盘上标题
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
    return {"status": "killed", "id": sid}


def _reap():
    for s in list(_sessions.values()):
        if not s.alive:
            s._cleanup()
            _sessions.pop(s.id, None)
            continue
        if time.time() - s.last_io > IDLE_TTL_S:
            s.kill()


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
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[term] reap_loop 异常（下轮重试）：{type(e).__name__}: {e}", flush=True)


def alive_count() -> int:
    """活会话数（自证端点与限流判据共用一个口径，别再各算各的）"""
    return len([s for s in _sessions.values() if s.alive])


def idle_max_s() -> int:
    """最久没 IO 的活会话闲置秒数（判断 TTL 有没有真的在跑）"""
    live = [time.time() - s.last_io for s in _sessions.values() if s.alive]
    return int(max(live)) if live else 0


def _attach_reader(sess: Session):
    loop = asyncio.get_event_loop()

    def on_readable():
        try:
            data = os.read(sess.fd, 65536)
            if data:
                sess.last_io = time.time()
                sess.ring.extend(data)
                if len(sess.ring) > 65536:
                    del sess.ring[:len(sess.ring) - 65536]
                for vid, q in list(sess.viewers.items()):
                    try:
                        q.put_nowait(data)
                    except asyncio.QueueFull:
                        # 不静默丢：丢多少字节记账，由该观看者的 pump 把提示发回终端
                        sess.dropped[vid] = sess.dropped.get(vid, 0) + len(data)
        except (OSError, BlockingIOError) as e:
            if isinstance(e, OSError) and e.errno in (errno.EIO, errno.EBADF):
                sess.alive = False
                sess._cleanup()  # 摘 reader + 关 fd + 收尸（幂等，替代原散落逻辑）
                # _cleanup() 已记下 exit_status ⇒ 这里能把「为什么没了」一起说出来。
                # 原样只有一句「[会话结束]」，用户看到的就是"点一下闪退、什么都不告诉我"。
                reason = describe_exit(sess.exit_status, sess.hub_killed)
                tail = f"\r\n\x1b[90m[进程 {reason}]\x1b[0m" if reason else ""
                try:
                    for q in list(sess.viewers.values()):
                        q.put_nowait(("\x1b[?25h\r\n[会话结束]" + tail).encode())
                except asyncio.QueueFull:
                    pass

    loop.add_reader(sess.fd, on_readable)


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
    # 回放最近输出（重连不白屏）
    if sess.ring:
        try:
            await ws.send_bytes(bytes(sess.ring))
        except Exception:  # noqa: BLE001
            pass
    # 输出泵：queue → ws；带超时兜底，进程死亡且队列排空即收口，绝不无限挂起
    async def pump():
        while True:
            try:
                data = await asyncio.wait_for(vq.get(), timeout=2.0)
            except asyncio.TimeoutError:
                if not sess.alive:
                    break
                continue
            # 慢消费者必须可见（P1-5）：丢了就明说，绝不静默吞字节
            lost = sess.dropped.pop(vid, 0)
            if lost:
                try:
                    await ws.send_bytes(("\r\n\x1b[90m[hub: 输出过快，已丢弃 %d 字节]\x1b[0m\r\n"
                                          % lost).encode())
                except Exception:  # noqa: BLE001
                    break
            # 真正修：send_bytes 必须 try（手机断网/切网络 → WS 已断 → 1006）
            try:
                await ws.send_bytes(data)
            except Exception:  # noqa: BLE001
                # WS 已断开：pump 退场，由收尾 try 兜底
                break
            if not sess.alive:
                break
        # 收尾：捕获 WS 已断开的情况（手机重连/切网络 → 客户端 1006），不再把异常抛回 event loop
        # 与 on_readable 的 EIO 分支同理：有面因就一并说出来，别让「为什么没了」变成哑谜。
        # 走到这里必然 alive=False，而 alive 只由 _cleanup() 置（全仓两处，另一处紧随其后调它）
        # ⇒ exit_status 已记录，直接读即可，不必补调 _cleanup()。
        reason = describe_exit(sess.exit_status, sess.hub_killed)
        tail = f" ({reason})" if reason else ""
        try:
            await ws.send_bytes(("\r\n\x1b[90m[process exited" + tail + "]\x1b[0m").encode())
        except Exception:  # noqa: BLE001
            pass
        try:
            await ws.close(code=4410)
        except Exception:  # noqa: BLE001
            pass
    pump_task = asyncio.create_task(pump())
    try:
        while True:
            msg = await ws.receive()
            sess.last_io = time.time()
            if msg.get("text") is not None:
                try:
                    j = json.loads(msg["text"])
                    if j.get("type") == "resize":
                        sess.cols, sess.rows = int(j["cols"]), int(j["rows"])
                        fcntl.ioctl(sess.fd, termios.TIOCSWINSZ,
                                    struct.pack("HHHH", sess.rows, sess.cols, 0, 0))
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
                try:
                    os.write(sess.fd, data)
                except OSError:
                    break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        pump_task.cancel()
        sess.viewers.pop(vid, None)
        sess.dropped.pop(vid, None)


def kill_all():
    """服务退出：立即 TERM+KILL 双发（组级），不等 call_later（loop 即将关闭）"""
    for s in _sessions.values():
        s.hub_killed = True      # 同上：别让"服务重启"被报成"内存不足"
        s._signal_group(signal.SIGTERM)
        s._signal_group(signal.SIGKILL)
