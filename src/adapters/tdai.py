"""TDAI 记忆适配器"""
import aiohttp
import json
from typing import Dict, Any, Optional, AsyncIterator
from .base import BaseAdapter


class TDAIAdapter(BaseAdapter):
    """TDAI 记忆中心适配器"""
    
    def __init__(self, config):
        super().__init__(config)
        self.base_url = config.tdaI_url.rstrip('/')
    
    async def chat(self, message: str, session_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """TDAI 不直接对话，返回记忆信息"""
        return {
            "success": False,
            "error": "TDAI 是记忆中心，不是对话 Agent",
            "hint": "使用 /api/memory 访问记忆功能"
        }
    
    async def chat_stream(self, message: str, session_id: Optional[str] = None, **kwargs) -> AsyncIterator[str]:
        return
        yield
    
    async def get_sessions(self, limit: int = 10) -> list:
        """获取会话（从 TDAI 记忆库）"""
        return []
    
    async def get_models(self) -> list:
        """TDAI 无模型概念"""
        return []
    
    async def search_memory(self, query: str, limit: int = 5) -> list:
        """语义搜索记忆"""
        # TDAI 的记忆 API 需要进一步探索
        # 目前返回空列表，等待后续集成
        return []
    
    async def health_check(self) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    f"{self.base_url}/health",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "status": "ok",
                            "agent": "tdai",
                            "endpoint": self.base_url,
                            "version": data.get("version", "unknown"),
                            "stores": data.get("stores", {})
                        }
            except:
                pass
            return {
                "status": "error",
                "agent": "tdai",
                "error": "Connection failed"
            }
