"""通用 OpenAI 兼容适配器（claude→CCR:3456 与 jcode:3457 共用）

Agent_Manager 的 llm.rs 同款协议：POST {base}/v1/chat/completions
支持非流式与 SSE 流式（stream=true, data: [DONE]）。
"""
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from .base import BaseAdapter


class OpenAICompatAdapter(BaseAdapter):
    """任何 OpenAI 兼容端点；auth_style: bearer | x_api_key | none"""

    def __init__(self, config, agent_id: str, base_url: str,
                 auth_token: str = "", auth_style: str = "bearer",
                 default_model: str = "", display_name: str = ""):
        super().__init__(config)
        self.agent_id = agent_id
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.auth_style = auth_style
        self.default_model = default_model
        self.display_name = display_name or agent_id

    def build_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            if self.auth_style == "x_api_key":
                headers["x-api-key"] = self.auth_token
            elif self.auth_style == "ccr":
                headers["x-ccr-core-auth"] = self.auth_token
            else:
                headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    async def chat(self, message: str, session_id: Optional[str] = None,
                   model: Optional[str] = None, history: Optional[List[dict]] = None,
                   **kwargs) -> Dict[str, Any]:
        messages = (history or []) + [{"role": "user", "content": message}]
        body = {"model": model or self.default_model or "default",
                "messages": messages, "stream": False}
        url = f"{self.base_url}/v1/chat/completions"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, headers=self.build_headers(), json=body,
                                  timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status != 200:
                        text = (await resp.text())[:500]
                        return {"success": False, "agent": self.agent_id,
                                "error": f"HTTP {resp.status}: {text}"}
                    data = await resp.json()
                    choice = (data.get("choices") or [{}])[0]
                    return {
                        "success": True,
                        "agent": self.agent_id,
                        "response": (choice.get("message") or {}).get("content", ""),
                        "model": data.get("model"),
                        "usage": data.get("usage"),
                    }
        except aiohttp.ClientError as e:
            return {"success": False, "agent": self.agent_id, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "agent": self.agent_id, "error": repr(e)}

    async def chat_stream(self, message: str, session_id: Optional[str] = None,
                          model: Optional[str] = None, history: Optional[List[dict]] = None,
                          **kwargs) -> AsyncIterator[str]:
        messages = (history or []) + [{"role": "user", "content": message}]
        body = {"model": model or self.default_model or "default",
                "messages": messages, "stream": True}
        url = f"{self.base_url}/v1/chat/completions"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, headers=self.build_headers(), json=body,
                                  timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    if resp.status != 200:
                        text = (await resp.text())[:300]
                        yield f"data: {json.dumps({'error': f'HTTP {resp.status}: {text}'}, ensure_ascii=False)}\n\n"
                        return
                    async for raw in resp.content:
                        line = raw.decode("utf-8", "ignore").strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            yield "data: [DONE]\n\n"
                            return
                        try:
                            chunk = json.loads(payload)
                            delta = (chunk.get("choices") or [{}])[0] \
                                .get("delta", {}).get("content")
                            if delta:
                                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
                        except json.JSONDecodeError:
                            continue
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'error': repr(e)}, ensure_ascii=False)}\n\n"

    async def get_models(self) -> list:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.base_url}/v1/models",
                                 headers=self.build_headers(),
                                 timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [m.get("id") for m in data.get("data", []) if m.get("id")]
        except Exception:  # noqa: BLE001
            pass
        return []

    async def get_sessions(self, limit: int = 10) -> list:
        return []  # 网关型端点无会话存储，统一对话记录在 hub 本地 SQLite

    async def health_check(self) -> Dict[str, Any]:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.base_url}/health",
                                 timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    ok = resp.status == 200
                    return {"status": "ok" if ok else "error", "agent": self.agent_id}
        except Exception:  # noqa: BLE001
            return {"status": "error", "agent": self.agent_id}
