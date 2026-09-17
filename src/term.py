"""Web 终端（P2-B：hub 自建 pty + WebSocket + xterm.js）

安全模型：
- 可执行命令 = profiles 画像白名单（terminal.cmd），API 只接受 agent_id，绝不接受任意命令
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

router = APIRouter()

TERM_TOKEN = os.getenv("TERM_TOKEN", "")
if not TERM_TOKEN:
    # 兜底：未配置 token 时自动生成，避免"空 token=不鉴权"的裸奔状态
    TERM_TOKEN = secrets.token_urlsafe(32)
    print(f"[term] TERM_TOKEN 未配置，已自动生成随机 token（前4位={TERM_TOKEN[:4]}，len={len(TERM_TOKEN)}）")
IDLE_TTL_S = int(os.getenv("TERM_IDLE_TTL", "2700"))
MAX_SESSIONS = 8


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
        self.outputs: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.viewers: set = set()
        self.ring = bytearray()  # 输出环形缓冲：重连回放，避免"重挂后白屏"
        self._cleaned = False    # 资源回收幂等守卫
        fcntl.fcntl(self.fd, fcntl.F_SETFL, os.O_NONBLOCK)

    def to_dict(self):
        return {"id": self.id, "agent_id": self.agent_id, "cmd": " ".join(self.cmd),
                "cwd": self.cwd, "alive": self.alive,
                "created": self.created, "idle_s": round(time.time() - self.last_io)}

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


@router.post("/api/term/sessions")
async def create_session(body: CreateIn, request: Request):
    _check_term_token(request.headers.get("x-term-token", "")
                      or request.query_params.get("token", ""), "POST /api/term/sessions")
    prof = profiles.get_profile(body.agent_id)
    if not prof or not prof.get("terminal"):
        raise HTTPException(400, f"{body.agent_id} 无终端入口（仅画像白名单可拉起）")
    if len([s for s in _sessions.values() if s.alive]) >= MAX_SESSIONS:
        raise HTTPException(429, f"终端会话数达上限 {MAX_SESSIONS}")
    cmd = shlex.split(prof["terminal"]["cmd"])
    resolved = profiles.which(cmd[0])
    if not resolved:
        raise HTTPException(400, f"命令 {cmd[0]} 未在本机找到")
    cmd[0] = resolved
    cwd = prof["terminal"].get("cwd") or os.path.expanduser("~")
    sid = uuid.uuid4().hex[:10]
    sess = Session(sid, prof["id"], cmd, cwd)
    _sessions[sid] = sess
    _attach_reader(sess)
    return {"session": sess.to_dict()}


@router.get("/api/term/sessions")
async def list_sessions():
    _reap()
    # 只展示活会话：已退出记录不再以"僵尸条目"出现在会话记录里
    return {"sessions": [s.to_dict() for s in _sessions.values() if s.alive]}


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
                for q in list(sess.outputs.values()) if isinstance(sess.outputs, dict) else [sess.outputs]:
                    try:
                        q.put_nowait(data)
                    except asyncio.QueueFull:
                        pass
        except (OSError, BlockingIOError) as e:
            if isinstance(e, OSError) and e.errno in (errno.EIO, errno.EBADF):
                sess.alive = False
                sess._cleanup()  # 摘 reader + 关 fd + 收尸（幂等，替代原散落逻辑）
                try:
                    sess.outputs.put_nowait("\x1b[?25h\r\n[会话结束]".encode())
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
    sess.viewers.add(ws)
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
                data = await asyncio.wait_for(sess.outputs.get(), timeout=2.0)
            except asyncio.TimeoutError:
                if not sess.alive:
                    break
                continue
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
        sess.viewers.discard(ws)


def kill_all():
    """服务退出：立即 TERM+KILL 双发（组级），不等 call_later（loop 即将关闭）"""
    for s in _sessions.values():
        s._signal_group(signal.SIGTERM)
        s._signal_group(signal.SIGKILL)
