"""LLM 网关客户端（Anthropic 协议 → 本机 FCC:8082 /v1/messages）

2026-09-06 实测校正：FCC 推理入口只有 /v1/messages(Anthropic) 与
/v1/responses(OpenAI Responses, 仅流式)，**没有** /v1/chat/completions。
FCC→tokenrouter/qwen3.8-flash 的 tool_use 已由 PT-20260905-01 端到端验证。

配置（.env）：
  MANAGER_LLM_BASE_URL   默认 http://127.0.0.1:8082
  MANAGER_LLM_API_KEY    FCC PROXY_AUTH token（Bearer）
  MANAGER_LLM_MODEL      默认 tokenrouter/qwen3.8-flash
"""
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

BASE_URL = os.getenv("MANAGER_LLM_BASE_URL", "http://127.0.0.1:8082").rstrip("/")
API_KEY = os.getenv("MANAGER_LLM_API_KEY", "")
MODEL = os.getenv("MANAGER_LLM_MODEL", "tokenrouter/qwen3.8-flash")
TIMEOUT = aiohttp.ClientTimeout(total=240)
MAX_TOKENS = int(os.getenv("MANAGER_LLM_MAX_TOKENS", "4096"))


def configured() -> bool:
    return bool(BASE_URL)


def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


def _openai_tools_to_anthropic(tools: List[dict]) -> List[dict]:
    """把 OpenAI function 风格 tools 转成 Anthropic tools"""
    out = []
    for t in tools:
        fn = t.get("function", t)
        out.append({"name": fn["name"], "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return out


async def _create_message(system: Optional[str], messages: List[dict],
                          tools: Optional[List[dict]] = None,
                          model: Optional[str] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"model": model or MODEL, "max_tokens": MAX_TOKENS,
                            "messages": messages}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = _openai_tools_to_anthropic(tools)
        body["tool_choice"] = {"type": "auto"}
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{BASE_URL}/v1/messages", headers=_headers(),
                          json=body, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                text = (await resp.text())[:600]
                raise RuntimeError(f"LLM 网关 HTTP {resp.status}: {text}")
            return await resp.json()


async def chat_tools_loop(messages: List[dict], tools: List[dict],
                          dispatch_tool=None, max_rounds: int = 6,
                          model: Optional[str] = None) -> Tuple[str, List[dict]]:
    """使用指定 model（默认用全局 MODEL）

    多轮工具环（上游 mcp_agent.rs 收敛策略）：
    stop_reason=tool_use → 执行 → tool_result 回填 → 继续；end_turn 终止。
    messages 为 OpenAI 风格 {role,content}，内部转 Anthropic 结构。
    返回 (final_text, steps)。
    """
    active_model = model or MODEL
    system = None
    anon_msgs = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            anon_msgs.append({"role": m["role"], "content": m["content"]})
    convo: List[dict] = [{"role": m["role"], "content": m["content"]} for m in anon_msgs]
    steps: List[dict] = []

    for _ in range(max_rounds):
        data = await _create_message(system, convo, tools or None, model=active_model)
        content_blocks = data.get("content") or []
        texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        thoughts = [b.get("thinking", "") for b in content_blocks if b.get("type") == "thinking"]
        for th in thoughts:
            if th.strip():
                steps.append({"kind": "thought", "content": th[:800]})
        tool_uses = [b for b in content_blocks if b.get("type") == "tool_use"]
        if not tool_uses:
            # FCC 上游把正文切成多个 text block（夹 thinking），直接拼接不加换行
            answer = "".join(texts).strip()
            steps.append({"kind": "answer", "content": answer})
            return answer, steps
        # 记录 assistant 块原样回填
        convo.append({"role": "assistant", "content": content_blocks})
        tool_results = []
        for tu in tool_uses:
            name = tu.get("name", "")
            args = tu.get("input") or {}
            steps.append({"kind": "toolcall", "tool": name, "tool_input": args,
                          "content": f"调用 {name}"})
            try:
                out = await dispatch_tool(name, args)
            except Exception as e:  # noqa: BLE001
                out = {"error": repr(e)}
            payload = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
            steps.append({"kind": "toolresult", "tool": name, "content": payload[:2000]})
            tool_results.append({"type": "tool_result", "tool_use_id": tu.get("id", ""),
                                 "content": payload[:8000]})
        convo.append({"role": "user", "content": tool_results})

    # 轮数耗尽：无 tools 强制收尾
    data = await _create_message(system, convo, None, model=active_model)
    answer = "".join(b.get("text", "") for b in (data.get("content") or [])
                     if b.get("type") == "text").strip() or "（达到最大工具调用轮数）"
    steps.append({"kind": "answer", "content": answer})
    return answer, steps
