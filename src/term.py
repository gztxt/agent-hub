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
from pydantic import BaseModel

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


def _check_term_token(provided: str, source: str) -> None:
    """缺 token 即拒：校验失败记一行拒绝原因（绝不记录 token 值）"""
    if not provided or not hmac.compare_digest(provided, TERM_TOKEN):
        print(f"[term] 拒绝：token 校验失败（{source}）")
        raise HTTPException(status_code=401, detail="term token required or invalid")


class Session:
    def __init__(self, sid: str, agent_id: str, cmd: List[str], cwd: str):
        self.id = sid
        self.agent_id = agent_id
        self.cmd = cmd
        self.cwd = cwd
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # 子进程：替换为终端程序
            try:
                env = dict(os.environ)
                env["TERM"] = "xterm-256color"
                env["COLORTERM"] = "truecolor"
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
        self.resume_of = ""     # v0.13.0：非空 = 由某条磁盘历史续聊而来
        fcntl.fcntl(self.fd, fcntl.F_SETFL, os.O_NONBLOCK)

    def to_dict(self):
        return {"id": self.id, "agent_id": self.agent_id, "cmd": " ".join(self.cmd),
                "cwd": self.cwd, "alive": self.alive,
                "created": self.created, "resume_of": self.resume_of,
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
                pid, _st = os.waitpid(self.pid, os.WNOHANG)
                if pid == 0:
                    break
            except (ChildProcessError, ProcessLookupError, OSError):
                break


_sessions: Dict[str, Session] = {}


class CreateIn(BaseModel):
    agent_id: str
    session_id: Optional[str] = None    # v0.13.0：续聊某条历史；仅接受形状合法且实盘存在的 id


@router.post("/api/term/sessions")
async def create_session(body: CreateIn, request: Request):
    _check_term_token(request.headers.get("x-term-token", "")
                      or request.query_params.get("token", ""), "POST /api/term/sessions")
    prof = profiles.get_profile(body.agent_id)
    if not prof or not prof.get("terminal"):
        raise HTTPException(400, f"{body.agent_id} 无终端入口（仅画像白名单可拉起）")
    if len([s for s in _sessions.values() if s.alive]) >= MAX_SESSIONS:
        raise HTTPException(429, f"终端会话数达上限 {MAX_SESSIONS}")
    cwd = prof["terminal"].get("cwd") or os.path.expanduser("~")
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
async def list_sessions():
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
async def kill_session(sid: str):
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
                try:
                    for q in list(sess.viewers.values()):
                        q.put_nowait("\x1b[?25h\r\n[会话结束]".encode())
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
    if not sess:
        await ws.close(code=4404)
        return
    if not sess.alive:
        # 死会话不再 accept：避免"回放旧画面+输入无效"的假加载（4410=已结束）
        await ws.close(code=4410)
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
        try:
            await ws.send_bytes(b"\r\n\x1b[90m[process exited]\x1b[0m")
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
        s._signal_group(signal.SIGTERM)
        s._signal_group(signal.SIGKILL)
