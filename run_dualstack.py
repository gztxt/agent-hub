"""单 socket 真双栈启动器（2026-09-29，批 1-4 授权）。

为什么不用 `--host ::`：
    CPython 的 `asyncio.start_server` 在自建 socket 时**硬写** `IPV6_V6ONLY=1`，
    与 `net.ipv6.bindv6only` 无关。所以 `--host ::` 得到的永远是 IPv6-only 监听。
    本机 2026-09-29 18:0x 实测（端口 39999 对照）：
        plain socket bind('::')    -> IPv4 连通 / IPv6 连通
        asyncio.start_server('::')  -> IPv4 errno 111 / IPv6 连通
    代价：3102/3103 全部 IPv4 拒连，手机与局域网打不开（本次回归的实际成因）。

这里怎么做：
    1) 自建 AF_INET6 socket，**显式** V6ONLY=0（不依赖 sysctl，sysctl 被改也不影响）；
    2) bind("::", PORT) + listen 后，把**已建好的 fd** 交给 `uvicorn.run(fd=...)`；
    3) uvicorn 走 `config.py` 的 `elif self.fd is not None: socket.fromfd(...)` 分支
       （不使用 bind_socket），`asyncio` 因为拿到了现成 socket 也不会再碰 V6ONLY
       ⇒ v4/v6 共用同一个监听，不需要第二个进程、第二份 lifespan。

端口真值：只读 .env 的 PORT（与 src/config.py 同一个文件），环境变量优先。
本文件只负责监听，不含任何业务逻辑；业务入口仍是 src.main:app。
"""
import os
import socket

import uvicorn

ENV_FILE = "/fs/1000/ftp/技术文档/agenthub/.env"
BACKLOG = 2048  # 与 uvicorn 默认一致，避免突发连接被截断


def _port() -> int:
    """端口只认 .env 的 PORT（唯一真值），环境变量可覆盖。"""
    if os.getenv("PORT"):
        return int(os.environ["PORT"])
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PORT="):
                    return int(line.split("=", 1)[1].strip())
    except OSError:
        pass
    return 3102


def main() -> None:
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # 这一行是全部要点：显式 0 = 接受 IPv4-mapped 连接。
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    host = os.getenv("DUAL_HOST", "::")
    sock.bind((host, _port()))
    sock.listen(BACKLOG)
    sock.set_inheritable(True)
    print(f"[Agent Hub] 双栈监听 {host}:{_port()}（V6ONLY=0，v4/v6 同一 socket）", flush=True)
    uvicorn.run("src.main:app", fd=sock.fileno(), log_level="info")


if __name__ == "__main__":
    main()
