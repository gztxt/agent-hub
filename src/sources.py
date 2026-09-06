"""项目类型自动识别（Agent_Manager agent_sources.rs 识别规则的 Python 移植）

输入目录 → 输出 {type, command, args, entry, port, ui_hint}。
端口探测源：.env 文件、pyproject.toml、源码中的 port= / PORT= / --port。
"""
import json
import re
from pathlib import Path
from typing import Optional

PY_ENTRIES = ["main.py", "app.py", "server.py", "run.py", "agent.py",
              "start.py", "wsgi.py", "asgi.py", "manage.py", "__main__.py"]
NODE_PORT_RE = re.compile(r"(?:PORT|port)\s*[:=(]\s*(\d{2,5})")
# 与上游 scan_project_dir 对齐：键名等于 PORT 或以 _PORT 结尾，值 >1000
ENV_PORT_RE = re.compile(r"^\s*(?:[A-Z0-9_]*PORT)\s*=\s*(\d{3,5})\s*$", re.MULTILINE)


def _first_entry(root: Path) -> Optional[str]:
    for name in PY_ENTRIES:
        if (root / name).is_file():
            return name
    for name in PY_ENTRIES:  # 第二轮看 src/
        if (root / "src" / name).is_file():
            return f"src/{name}"
    return None


def _detect_port(root: Path) -> Optional[int]:
    """扫描 .env / 配置中的端口声明（与 agent_sources.rs 的端口探测同源思路）"""
    candidates = list(root.glob(".env*"))[:5]
    for f in candidates:
        try:
            m = ENV_PORT_RE.search(f.read_text(errors="ignore"))
            if m:
                port = int(m.group(1))
                if 1000 < port <= 65535:
                    return port
        except OSError:
            continue
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            m = re.search(r"--port[= ]+(\d{2,5})", pyproject.read_text(errors="ignore"))
            if m:
                return int(m.group(1))
        except OSError:
            pass
    # 浅层源码扫描（最多 3 个入口文件，控制成本）
    for name in PY_ENTRIES[:3]:
        f = root / name
        if f.is_file():
            try:
                m = NODE_PORT_RE.search(f.read_text(errors="ignore")[:8000])
                if m:
                    port = int(m.group(1))
                    if 1024 <= port <= 65535:
                        return port
            except OSError:
                continue
    return None


def detect_project(root_dir: str) -> dict:
    root = Path(root_dir).expanduser()
    result = {"type": "unknown", "command": None, "args": [], "entry": None,
              "port": None, "ui_hint": False, "name": root.name}
    if not root.is_dir():
        result["error"] = f"目录不存在: {root}"
        return result

    def has(f: str) -> bool:
        return (root / f).is_file()

    # Python 系
    if has("manage.py"):
        result.update(type="django", command="python", args=["manage.py", "runserver", "0.0.0.0:8000"],
                      entry="manage.py", port=result["port"] or 8000, ui_hint=True)
    elif has("pyproject.toml") and has("uv.lock"):
        entry = _first_entry(root)
        result.update(type="python-uv", command="uv", args=["run", "python", entry or "main.py"],
                      entry=entry)
    elif has("pyproject.toml") and _read_req(root / "pyproject.toml") and "fastapi" in _read_req(root / "pyproject.toml"):
        entry = (_first_entry(root) or "main.py").replace(".py", "")
        port = _detect_port(root) or 8000
        result.update(type="fastapi", command="uvicorn", args=[f"{entry}:app", "--host", "0.0.0.0", "--port", str(port)],
                      entry=f"{entry}:app", port=port, ui_hint=True)
    else:
        req = _read_req(root / "requirements.txt")
        entry = _first_entry(root)
        if req and "streamlit" in req and entry:
            port = _detect_port(root) or 8501
            result.update(type="streamlit", command="streamlit",
                          args=["run", entry, "--server.port", str(port), "--server.address", "0.0.0.0"],
                          entry=entry, port=port, ui_hint=True)
        elif req and "fastapi" in req and entry:
            port = _detect_port(root) or 8000
            mod = entry.replace(".py", "")
            result.update(type="fastapi", command="uvicorn",
                          args=[f"{mod}:app", "--host", "0.0.0.0", "--port", str(port)],
                          entry=f"{mod}:app", port=port, ui_hint=True)
        elif req and "flask" in req and entry:
            result.update(type="flask", command="python", args=[entry], entry=entry,
                          port=_detect_port(root) or 5000, ui_hint=True)
        elif entry:
            result.update(type="python", command="python", args=[entry], entry=entry,
                          port=_detect_port(root))
        elif has("package.json"):
            scripts = _npm_scripts(root / "package.json")
            dev = "dev" if "dev" in scripts else ("start" if "start" in scripts else None)
            result.update(type="node", command="npm", args=["run", dev] if dev else ["install"],
                          entry=dev, port=_detect_port(root) or (5173 if dev == "dev" else None),
                          ui_hint=dev is not None)
        elif has("Cargo.toml"):
            result.update(type="rust", command="cargo", args=["run"])
        elif has("go.mod"):
            result.update(type="go", command="go", args=["run", "."],
                          port=_detect_port(root))
    if result["port"] is None:
        result["port"] = _detect_port(root)
    return result


def _read_req(path: Path) -> str:
    try:
        return path.read_text(errors="ignore").lower() if path.is_file() else ""
    except OSError:
        return ""


def _npm_scripts(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(errors="ignore"))
        return data.get("scripts", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}
