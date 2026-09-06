"""Agent 自动发现模块（融合版：内置清单 + custom_agents 动态注册）"""
import asyncio
import json
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp


@dataclass
class AgentInfo:
    """Agent 信息"""
    id: str
    name: str
    type: str  # "claude" | "pi" | "jcode" | "tdai" | "custom" | ...
    status: str  # "running" | "stopped" | "unknown"
    endpoint: str
    port: Optional[int]
    auth_required: bool
    description: str = ""
    config_path: Optional[str] = None
    last_seen: Optional[str] = None
    builtin: bool = True
    working_dir: Optional[str] = None

    def to_dict(self):
        return asdict(self)


class AgentDiscovery:
    """Agent 自动发现器"""

    def __init__(self, config, db=None):
        self.config = config
        self.db = db
        self._cache: Dict[str, AgentInfo] = {}

    # ── 配置源 ────────────────────────────────────────────────

    def _default_agents(self) -> List[Dict]:
        """内置 Agent 清单（本机实况：CCR/pi/jcode/TDAI/FCC/QwenPaw）"""
        return [
            {
                "id": "claude",
                "name": "Claude Code",
                "type": "claude",
                "endpoint": f"{self.config.ccr_url}/v1/chat/completions",
                "port": 3456,
                "auth_required": True,
                "description": "Anthropic Claude Code CLI，通过 CCR 网关访问"
            },
            {
                "id": "pi",
                "name": "Pi Agent",
                "type": "pi",
                "endpoint": self.config.pi_url,
                "port": 30141,
                "auth_required": False,
                "description": "Pi Coding Agent，Web UI + HTTP API"
            },
            {
                "id": "jcode",
                "name": "JCode",
                "type": "jcode",
                "endpoint": f"{self.config.jcode_url}/v1/chat/completions",
                "port": 3457,
                "auth_required": True,
                "description": "OpenAI 兼容 API 网关"
            },
            {
                "id": "tdai",
                "name": "TDAI Memory",
                "type": "tdai",
                "endpoint": self.config.tdaI_url,
                "port": 8420,
                "auth_required": False,
                "description": "腾讯 DB Agent 记忆核心"
            },
            {
                "id": "qwenpaw",
                "name": "QwenPaw",
                "type": "qwenpaw",
                "endpoint": "http://127.0.0.1:8088",
                "port": 8088,
                "auth_required": False,
                "description": "QwenPaw 个人助理框架（本 Agent 宿主，WebUI :8088）"
            },
            {
                "id": "fcc",
                "name": "FCC Gateway",
                "type": "gateway",
                "endpoint": "http://127.0.0.1:8082",
                "port": 8082,
                "auth_required": True,
                "description": "free-claude-code 模型网关（Anthropic /v1/messages；中继 :18083）"
            },
        ]

    def _custom_agents(self) -> List[Dict]:
        if not self.db:
            return []
        try:
            rows = self.db.query("SELECT * FROM custom_agents ORDER BY created_at")
        except Exception:  # noqa: BLE001
            return []
        out = []
        for r in rows:
            out.append({
                "id": r["id"], "name": r["name"], "type": r["type"],
                "endpoint": r["endpoint"] or "", "port": r["port"],
                "auth_required": False,
                "description": r["description"] or f"自定义（{r['type']}） {r['working_dir'] or ''}",
                "working_dir": r["working_dir"], "builtin": False,
            })
        return out

    def all_configs(self) -> List[Dict]:
        return self._default_agents() + self._custom_agents()

    def reload(self):
        self._cache.clear()

    # ── 探测 ──────────────────────────────────────────────────

    async def discover_all(self) -> List[AgentInfo]:
        """发现所有 Agent（并发探测，避免串行 2s×N 的等待）"""
        results = await asyncio.gather(
            *(self._check_agent(c) for c in self.all_configs()),
            return_exceptions=True)
        agents = []
        for r in results:
            if isinstance(r, AgentInfo):
                self._cache[r.id] = r
                agents.append(r)
        return agents

    async def _check_agent(self, config: Dict) -> AgentInfo:
        agent_id = config["id"]
        is_running = await self._check_port(config.get("port"))
        health_status = await self._check_health(config["endpoint"], config.get("port"))
        status = "running" if (is_running or health_status == "ok") else "stopped"
        return AgentInfo(
            id=agent_id,
            name=config["name"],
            type=config["type"],
            status=status,
            endpoint=config["endpoint"],
            port=config.get("port"),
            auth_required=config.get("auth_required", False),
            description=config.get("description", ""),
            config_path=self._find_config_path(agent_id),
            last_seen=self._iso_now(),
            builtin=config.get("builtin", True),
            working_dir=config.get("working_dir"),
        )

    async def _check_port(self, port: Optional[int]) -> bool:
        if not port:
            return False
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._sync_check_port, port)
        except Exception:  # noqa: BLE001
            return False

    def _sync_check_port(self, port: int) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            return result == 0
        except Exception:  # noqa: BLE001
            return False

    async def _check_health(self, endpoint: str, port: Optional[int] = None) -> str:
        """检查 API 健康 —— 按端点类型推导健康 URL"""
        if not endpoint:
            return "error"
        base = endpoint.split("/v1")[0].rstrip("/")
        candidates = [base + "/health"]
        if "30141" in endpoint:
            candidates = [base + "/api/sessions"]
        for url in candidates:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url,
                                           timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        if resp.status in (200, 401, 403):
                            # 401/403 = 服务在岗但需鉴权（CCR/FCC），按 Agent_Manager
                            # relay 探针口径视为"在岗"
                            return "ok"
            except Exception:  # noqa: BLE001
                continue
        return "error"

    def _find_config_path(self, agent_id: str) -> Optional[str]:
        paths = {
            "claude": Path.home() / ".claude" / "settings.json",
            "pi": Path.home() / ".pi" / "settings.json",
            "jcode": Path.home() / ".config" / "jcode" / "config.toml",
            "tdai": None
        }
        path = paths.get(agent_id)
        return str(path) if path and path.exists() else None

    def _iso_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── 同步查询（缓存优先 + 快速端口复核）─────────────────────

    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        if agent_id in self._cache:
            info = self._cache[agent_id]
            info.port = info.port
            info.status = "running" if (
                info.port and self._sync_check_port(info.port)) else "stopped"
            info.last_seen = self._iso_now()
            return info
        for config in self.all_configs():
            if config["id"] == agent_id:
                port = config.get("port")
                alive = bool(port and self._sync_check_port(port))
                return AgentInfo(
                    id=config["id"], name=config["name"], type=config["type"],
                    status="running" if alive else "unknown",
                    endpoint=config["endpoint"], port=port,
                    auth_required=config.get("auth_required", False),
                    description=config.get("description", ""),
                    config_path=self._find_config_path(agent_id),
                    builtin=config.get("builtin", True),
                    working_dir=config.get("working_dir"),
                    last_seen=self._iso_now())
        return None
