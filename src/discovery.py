"""Agent 发现（画像驱动版：按本机实际运行检测，非端口判定）"""
import asyncio
import json
import re
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp

import profiles


@dataclass
class AgentInfo:
    id: str
    name: str
    kind: str          # agent|gateway|service|memory|tool
    status: str        # running|installed|stopped
    endpoint: str
    port: Optional[int]
    auth_required: bool
    description: str = ""
    config_path: Optional[str] = None
    last_seen: Optional[str] = None
    builtin: bool = True
    working_dir: Optional[str] = None
    entries: List[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


class AgentDiscovery:
    def __init__(self, config, db=None):
        self.config = config
        self.db = db
        self._cache: Dict[str, AgentInfo] = {}

    async def discover_all(self) -> List[AgentInfo]:
        procs, units, dockers = await asyncio.to_thread(self._probe_env)
        # 第一遍：全部状态（供跨画像联动，如 claude 的 Web UI 宿主 cloudcli）
        prof_list = profiles.all_profiles()
        status_map: Dict[str, str] = {}
        for p in prof_list:
            st = profiles.detect_status(p, procs, units, dockers)
            port = p.get("port")
            if st == "stopped" and port and self._sync_check_port(port):
                st = "running"
            status_map[p["id"]] = st
        agents: List[AgentInfo] = []
        for p in prof_list:
            status = status_map[p["id"]]
            ui = p.get("ui")
            entries = profiles.entries_for(p, status)
            # 通用宿主探测：dict ui = 独立 Web 界面宿主（如 claude←cloudcli :3010），
            # 端口活才出「原生会话」入口，且计入实体 running 证据
            if isinstance(ui, dict):
                ui_alive = self._sync_check_port(ui.get("port"))
                if ui_alive:
                    entries = ([{"type": "embed", "label": "原生会话", "url": ui["url"]},
                                {"type": "open", "label": "↗UI", "url": ui["url"]}]
                               + entries)
                    if status in ("installed", "stopped"):
                        status = "running"
                endpoint = ui["url"] if ui_alive else (p.get("panel") or "")
            else:
                endpoint = ui or p.get("panel") or (f"http://127.0.0.1:{p['port']}" if p.get("port") else "")
            # 死面板不出按钮（probe_port 过滤）
            entries = [e for e in entries
                       if not (e.get("probe_port") and not self._sync_check_port(e["probe_port"]))]
            agents.append(AgentInfo(
                id=p["id"], name=p["name"], kind=p["kind"], status=status,
                endpoint=endpoint, port=p.get("port"),
                auth_required=bool(p.get("panel_auth")) or
                bool(p.get("panel") and re.search(r"18083|12700", str(p.get("panel")))),
                description=p.get("desc", ""),
                config_path=self._find_config_path(p["id"]),
                last_seen=self._iso_now(), builtin=True,
                working_dir=(p.get("terminal") or {}).get("cwd"),
                entries=entries))
        # 动态注册/扫描项（一律 service 类，仅快捷方式）
        for c in self._custom_agents():
            port = c.get("port")
            alive = bool(port and self._sync_check_port(port))
            agents.append(AgentInfo(
                id=c["id"], name=c["name"], kind="service",
                status="running" if alive else "stopped",
                endpoint=c.get("endpoint") or "", port=port, auth_required=False,
                description=c.get("description") or "", last_seen=self._iso_now(),
                builtin=False, working_dir=c.get("working_dir"),
                entries=([{"type": "open", "label": "打开面板", "url": c["endpoint"]}]
                         if c.get("endpoint") else []) + [{"type": "detail", "label": "详情"}]))
        for a in agents:
            self._cache[a.id] = a
        return agents

    def _probe_env(self):
        return (profiles.list_processes(),
                profiles.running_systemd_units(),
                profiles.docker_states())

    def _custom_agents(self) -> List[Dict]:
        if not self.db:
            return []
        try:
            rows = self.db.query("SELECT * FROM custom_agents ORDER BY created_at")
        except Exception:  # noqa: BLE001
            return []
        # 与画像去重：端口命中任一画像 port/panel 端口的 scan 项跳过
        prof_ports = set()
        for p in profiles.all_profiles():
            for k in ("port", "ui", "panel"):
                v = p.get(k)
                if isinstance(v, int):
                    prof_ports.add(v)
                elif isinstance(v, str):
                    m = re.search(r":(\d{2,5})", v)
                    if m:
                        prof_ports.add(int(m.group(1)))
        out = []
        for r in rows:
            if r["port"] and r["port"] in prof_ports:
                continue
            out.append(r)
        return out

    def reload(self):
        self._cache.clear()

    def _sync_check_port(self, port: Optional[int]) -> bool:
        if not port:
            return False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.2)
            ok = s.connect_ex(("127.0.0.1", port)) == 0
            s.close()
            return ok
        except OSError:
            return False

    def _find_config_path(self, agent_id: str) -> Optional[str]:
        paths = {
            "claude": Path.home() / ".claude" / "settings.json",
            "pi": Path.home() / ".pi" / "settings.json",
            "jcode": Path.home() / ".config" / "jcode" / "config.toml",
            "hermes": Path.home() / ".hermes" / "config.yaml",
            "ccr": Path.home() / ".ccr" / "config.json",
        }
        path = paths.get(agent_id)
        return str(path) if path and path.exists() else None

    def _iso_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        return self._cache.get(agent_id)
