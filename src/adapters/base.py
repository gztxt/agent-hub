"""适配器抽象基类"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, AsyncIterator


class BaseAdapter(ABC):
    """Agent 适配器基类"""
    
    def __init__(self, config):
        self.config = config
        self.agent_id = self.__class__.__name__.lower()
    
    @abstractmethod
    async def chat(self, message: str, session_id: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """发送消息并获取响应"""
        pass
    
    @abstractmethod
    async def chat_stream(self, message: str, session_id: Optional[str] = None, **kwargs) -> AsyncIterator[str]:
        """流式对话"""
        pass
    
    @abstractmethod
    async def get_sessions(self, limit: int = 10) -> list:
        """获取会话历史"""
        pass
    
    @abstractmethod
    async def get_models(self) -> list:
        """获取可用模型列表"""
        pass
    
    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        return {"status": "unknown", "agent": self.agent_id}
    
    def build_headers(self) -> Dict[str, str]:
        """构建认证头（子类重写）"""
        return {}
