"""适配器抽象基类"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, AsyncIterator


class BaseAdapter(ABC):
    """Agent 适配器基类"""

    def __init__(self, config):
        self.config = config
        self.agent_id = self.__class__.__name__.lower()

    @abstractmethod
    async def chat(self, message: str, session_id: Optional[str] = None,
                   model: Optional[str] = None, history: Optional[list] = None,
                   cwd: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """发送消息并获取响应。

        model: 透传 model id（网关/上游路由键）
        history: 最近 N 轮 [{role,content}]
        cwd: 当前工作目录（语义：上下文元数据，由具体适配器决定是否注入 prompt）
        """
        pass

    @abstractmethod
    async def chat_stream(self, message: str, session_id: Optional[str] = None,
                          model: Optional[str] = None, history: Optional[list] = None,
                          cwd: Optional[str] = None, **kwargs) -> AsyncIterator[str]:
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
