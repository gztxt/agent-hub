"""Agent 自动发现模块"""
import asyncio
import json
import socket
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import aiohttp


@dataclass
class AgentInfo:
    """Agent 信息"""
    id: str
    name: str
    type: str  # "claude" | "pi" | "jcode" | "tdai" | "custom"
    status: str  # "running" | "stopped" | "unknown"
    endpoint: str
    port: Optional[int]
    auth_required: bool
    description: str = ""
    config_path: Optional[str] = None
    last_seen: Optional[str] = None
    
    def to_dict(self):
        return asdict(self)


class AgentDiscovery:
    """Agent 自动发现器"""
    
    def __init__(self, config):
        self.config = config
        self.known_agents = self._load_known_agents()
    
    def _load_known_agents(self) -> List[Dict]:
        """加载已知 Agent 配置"""
        registry = self.config.registry_path
        if registry.exists():
            try:
                return json.loads(registry.read_text())
            except:
                pass
        return self._default_agents()
    
    def _default_agents(self) -> List[Dict]:
        """默认 Agent 列表"""
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
            }
        ]
    
    async def discover_all(self) -> List[AgentInfo]:
        """发现所有 Agent"""
        agents = []
        
        for agent_config in self._default_agents():
            agent = await self._check_agent(agent_config)
            agents.append(agent)
        
        return agents
    
    async def _check_agent(self, config: Dict) -> AgentInfo:
        """检查单个 Agent 状态"""
        agent_id = config["id"]
        
        # 检查端口
        is_running = await self._check_port(config.get("port"))
        
        # 检查 API 健康
        health_status = await self._check_health(config["endpoint"])
        
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
            last_seen=self._iso_now()
        )
    
    async def _check_port(self, port: Optional[int]) -> bool:
        """检查端口是否监听"""
        if not port:
            return False
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._sync_check_port, port)
        except:
            return False
    
    def _sync_check_port(self, port: int) -> bool:
        """同步端口检查"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            return result == 0
        except:
            return False
    
    async def _check_health(self, endpoint: str) -> str:
        """检查 API 健康"""
        try:
            # 根据不同 Agent 类型调用不同健康端点
            if "3456" in endpoint or "3457" in endpoint:
                health_url = endpoint.split("/v1")[0] + "/health"
            elif "30141" in endpoint:
                health_url = endpoint + "/api/sessions"
            elif "8420" in endpoint:
                health_url = endpoint + "/health"
            else:
                health_url = endpoint
            
            async with aiohttp.ClientSession() as session:
                async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    return "ok" if resp.status == 200 else "error"
        except:
            return "error"
    
    def _find_config_path(self, agent_id: str) -> Optional[str]:
        """查找配置文件路径"""
        paths = {
            "claude": Path.home() / ".claude" / "settings.json",
            "pi": Path.home() / ".pi" / "settings.json",
            "jcode": Path.home() / ".config" / "jcode" / "config.toml",
            "tdai": None
        }
        path = paths.get(agent_id)
        return str(path) if path and path.exists() else None
    
    def _iso_now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
    
    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        """获取单个 Agent 信息"""
        for config in self._default_agents():
            if config["id"] == agent_id:
                return AgentInfo(
                    id=config["id"],
                    name=config["name"],
                    type=config["type"],
                    status="unknown",
                    endpoint=config["endpoint"],
                    port=config.get("port"),
                    auth_required=config.get("auth_required", False),
                    description=config.get("description", ""),
                    config_path=self._find_config_path(agent_id)
                )
        return None
