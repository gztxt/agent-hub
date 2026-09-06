"""端口管理器（Agent_Manager ports.rs 的 Linux /proc 移植版）

只读枚举监听端口 → (port, proto, pid, process, addr)。
不提供任意 kill —— NAS 上的服务由 systemd/守护方管理，越权杀进程违反本机军规。
"""
import os
import re
import socket
from pathlib import Path
from typing import Optional

STATE_LISTEN = "0A"


def _parse_hex_addr(hexstr: str, family: int) -> tuple[str, int]:
    addr, port = hexstr.split(":")
    if family == socket.AF_INET:
        # 小端 4 字节
        b = bytes.fromhex(addr)
        ip = ".".join(str(x) for x in reversed(b))
    else:
        b = bytes.fromhex(addr)
        # tcp6 每 4 字节一组小端
        parts = [b[i:i + 4][::-1] for i in range(0, 16, 4)]
        raw = b"".join(parts)
        ip = socket.inet_ntop(socket.AF_INET6, raw)
    return ip, int(port, 16)


def _inode_to_pid() -> dict[str, tuple[int, str]]:
    """扫描 /proc/<pid>/fd 建 socket inode → (pid, comm) 索引"""
    idx: dict[str, tuple[int, str]] = {}
    my_pid = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == my_pid:
            continue
        try:
            comm = (entry / "comm").read_text().strip()
            for fd in (entry / "fd").iterdir():
                try:
                    link = os.readlink(fd)
                except OSError:
                    continue
                m = re.match(r"socket:\[(\d+)\]", link)
                if m:
                    idx[m.group(1)] = (pid, comm)
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            continue
    return idx


def list_listeners() -> list[dict]:
    inode_idx = _inode_to_pid()
    rows = []
    for proto, path, fam in (("tcp", "/proc/net/tcp", socket.AF_INET),
                             ("tcp6", "/proc/net/tcp6", socket.AF_INET6)):
        try:
            lines = Path(path).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            cols = line.split()
            if len(cols) < 10 or cols[3] != STATE_LISTEN:
                continue
            ip, port = _parse_hex_addr(cols[1], fam)
            inode = cols[9]
            pid, comm = inode_idx.get(inode, (None, None))
            rows.append({"proto": proto, "port": port, "address": ip,
                         "pid": pid, "process": comm})
    # udp 无连接，仅列出有 socket 持有者的
    try:
        for line in Path("/proc/net/udp").read_text().splitlines()[1:]:
            cols = line.split()
            if len(cols) < 10:
                continue
            ip, port = _parse_hex_addr(cols[1], socket.AF_INET)
            pid_comm = inode_idx.get(cols[9])
            if pid_comm:
                rows.append({"proto": "udp", "port": port, "address": ip,
                             "pid": pid_comm[0], "process": pid_comm[1]})
    except OSError:
        pass
    rows.sort(key=lambda r: (r["proto"], r["port"]))
    # 去重（同端口多地址）
    seen = set()
    out = []
    for r in rows:
        key = (r["proto"], r["port"], r["address"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0
