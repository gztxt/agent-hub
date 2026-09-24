"""TDAI 记忆适配器

v0.13.16（PT-20260924-08 / T0-4）：以前 `search_memory()` 是个**带 TODO 的空壳**
（注释写「TDAI 的记忆 API 需要进一步探索，目前返回空列表」），而 `health_check()` 真能打通
—— 这正是「在健在却查不到任何东西」能长期瞒着人的原因：健康是绿的，能力是空的。
现成接口契约已从 TDAI 源码取证并转调 `tdai_client`（路径/方法/两个鉴权头全在那里统一）。

失败口径：BaseAdapter 定的返回形状是 `list`，装不下错误。因此除了返回空列表，
**额外记一条 warning 日志**，并把结构化原因挂在实例属性 `last_error` 上，
供 discovery / 调试端点读取 —— 不再让它默默变成「没有记忆」。
"""
import logging
from typing import Any, AsyncIterator, Dict, Optional

import tdai_client
from .base import BaseAdapter

log = logging.getLogger("agent-hub.tdai")


class TDAIAdapter(BaseAdapter):
    """TDAI 记忆中心适配器"""

    def __init__(self, config):
        super().__init__(config)
        self.base_url = config.tdaI_url.rstrip('/')
        self.last_error: Optional[str] = None

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
        """返回 [] 是有语义的，不是未实现：TDAI 没有「会话列表」这个对象，
        只有 L0 原始对话流（`/v2/conversation/search`，按 query 检索）。
        要拿历史请走 search_conversations / hub 的 /api/memory/search?episodic=N。"""
        return []

    async def get_models(self) -> list:
        """TDAI 无模型概念"""
        return []

    async def search_memory(self, query: str, limit: int = 5) -> list:
        """语义检索 L1 结构化记忆（转调 tdai_client，契约已源码取证）。"""
        r = await tdai_client.search_memories(query, limit)
        if not r.get("ok"):
            self.last_error = r.get("error")
            # 空列表不区分「没命中」与「查不通」，所以必须开声；但绝不把 key 写进日志
            log.warning("[tdai] search_memory 失败（非「无命中」）：%s", self.last_error)
            return []
        self.last_error = None
        return r.get("items") or []

    async def health_check(self) -> Dict[str, Any]:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
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
                    return {"status": "error", "agent": "tdai",
                            "error": f"HTTP {resp.status}"}
        except Exception as e:  # noqa: BLE001 — 原来是光秃秃的 `except:`（E722）且只报 'Connection failed'
            return {"status": "error", "agent": "tdai",
                    "error": f"{type(e).__name__}: {str(e)[:160]}"}
