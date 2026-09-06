"""pi Agent 适配器"""
import aiohttp
from typing import Dict, Any, Optional, AsyncIterator
from .base import BaseAdapter


class PiAdapter(BaseAdapter):
    """Pi Agent 适配器"""
    
    def __init__(self, config):
        super().__init__(config)
        self.base_url = config.pi_url.rstrip('/')
        self.api_url = f"{self.base_url}/api/sessions"
    
    def build_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.pi_api_key:
            headers["Authorization"] = f"Bearer {self.config.pi_api_key}"
        return headers
    
    async def chat(self, message: str, session_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """pi 通过 Web UI 交互，无直接 API，返回提示信息"""
        return {
            "success": False,
            "error": "pi Agent 需通过 Web UI (:{}) 交互".format(
                self.config.pi_url.split(':')[-1].split('/')[0]
            ),
            "hint": f"请访问 http://192.168.5.102:{self.config.pi_url.split(':')[-1].split('/')[0]}/"
        }
    
    async def chat_stream(self, message: str, session_id: Optional[str] = None, **kwargs) -> AsyncIterator[str]:
        """pi 不支持流式 API"""
        return
        yield  # 使函数成为生成器
    
    async def get_sessions(self, limit: int = 10) -> list:
        """获取会话列表"""
        headers = self.build_headers()
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    self.api_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        sessions = data.get("sessions", [])
                        return sessions[:limit]
            except:
                pass
        return []
    
    async def get_models(self) -> list:
        """获取模型列表（需认证）"""
        return []
    
    async def health_check(self) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    self.base_url,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    return {
                        "status": "ok" if resp.status == 200 else "error",
                        "agent": "pi",
                        "endpoint": self.base_url
                    }
            except:
                return {
                    "status": "error",
                    "agent": "pi",
                    "error": "Connection failed"
                }
