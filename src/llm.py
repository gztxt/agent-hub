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
                          dispatch_tool=None, max_rounds: int = 10,
                          model: Optional[str] = None,
                          on_step=None) -> Tuple[str, List[dict]]:
    """使用指定 model（默认用全局 MODEL）

    多轮工具环（上游 mcp_agent.rs 收敛策略）：
    stop_reason=tool_use → 执行 → tool_result 回填 → 继续；end_turn 终止。
    messages 为 OpenAI 风格 {role,content}，内部转 Anthropic 结构。
    返回 (final_text, steps)。

    v0.5 增量回调：on_step(step) 在每个 thought/toolcall/toolresult 完成后被调用一次，
    用来支持 SSE 流式渲染（前端可看到思考过程，不显假死）。可选，不传 = 老行为。

    v0.5.3 软熔断：检测到连续 3 次同类型工具错误指纹，提前 end_turn 给用户答复；
    max_rounds 仅作兜底，正常情况 3 轮就停。指纹 = tool_name:error_type:field:first50。
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
    # v0.5.3 软熔断：最近 3 个 tool 调用的指纹；元素 = "name:err_type:field:first50"
    recent_signatures: List[str] = []

    def _is_err_result(r: Any) -> bool:
        """判定工具结果是否为错误。兼容 dict {error:...} / {ok:False,...} / 字符串含 'error'。"""
        if isinstance(r, dict):
            if "error" in r:
                return True
            if "ok" in r and not r.get("ok"):
                return True
            return False
        if isinstance(r, str):
            try:
                j = json.loads(r)
                if isinstance(j, dict):
                    return _is_err_result(j)
            except Exception:
                pass
            return "error" in r.lower()
        return False

    def _signature(tool_name: str, r: Any) -> str:
        """提取指纹。成功结果 = '<name>:ok'，失败 = '<name>:err_type:field:first50'。"""
        if not _is_err_result(r):
            return f"{tool_name}:ok"
        # 取结构化字段（如果 result 是 dict 或可解 JSON 字符串）
        obj: Dict[str, Any] = {}
        if isinstance(r, dict):
            obj = r
        elif isinstance(r, str):
            try:
                obj = json.loads(r)
            except Exception:
                obj = {}
        err_type = str(obj.get("error_type", "unknown")) if isinstance(obj, dict) else "unknown"
        field = str(obj.get("field", "")) if isinstance(obj, dict) else ""
        # 错误主文本：error 或 message 或 detail
        msg = ""
        if isinstance(obj, dict):
            msg = str(obj.get("error", "") or obj.get("message", "") or obj.get("detail", ""))
        elif isinstance(r, str):
            msg = r
        first50 = msg[:50]
        return f"{tool_name}:{err_type}:{field}:{first50}"

    for _ in range(max_rounds):
        data = await _create_message(system, convo, tools or None, model=active_model)
        content_blocks = data.get("content") or []
        texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        thoughts = [b.get("thinking", "") for b in content_blocks if b.get("type") == "thinking"]
        for th in thoughts:
            if th.strip():
                s = {"kind": "thought", "content": th[:800]}
                steps.append(s)
                if on_step: await on_step(s)
        tool_uses = [b for b in content_blocks if b.get("type") == "tool_use"]
        if not tool_uses:
            # FCC 上游把正文切成多个 text block（夹 thinking），直接拼接不加换行
            answer = "".join(texts).strip()
            s = {"kind": "answer", "content": answer}
            steps.append(s)
            if on_step: await on_step(s)
            return answer, steps
        # 记录 assistant 块原样回填
        convo.append({"role": "assistant", "content": content_blocks})
        tool_results = []
        for tu in tool_uses:
            name = tu.get("name", "")
            args = tu.get("input") or {}
            s = {"kind": "toolcall", "tool": name, "tool_input": args,
                 "content": f"调用 {name}"}
            steps.append(s)
            if on_step: await on_step(s)
            try:
                out = await dispatch_tool(name, args)
            except Exception as e:  # noqa: BLE001
                out = {"error": repr(e)}
            payload = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
            s = {"kind": "toolresult", "tool": name, "content": payload[:2000]}
            steps.append(s)
            if on_step: await on_step(s)
            tool_results.append({"type": "tool_result", "tool_use_id": tu.get("id", ""),
                                 "content": payload[:8000]})

            # v0.5.3 软熔断：记录指纹 + 判定最近 3 次是否完全相同
            sig = _signature(name, out)
            recent_signatures.append(sig)
            if (len(recent_signatures) >= 3
                    and len(set(recent_signatures[-3:])) == 1
                    and not recent_signatures[-1].endswith(":ok")):
                # 强制 end_turn：构造 fallback answer 步骤（用户能看到"我停了，原因如下"）
                ok_count = sum(
                    1 for st in steps
                    if st.get("kind") == "toolresult"
                    and not _is_err_result(_try_parse_payload(st.get("content", "")))
                )
                ok_summary = (f"已成功收集到 {ok_count} 个工具结果"
                              if ok_count else "无成功结果")
                fallback_answer = (
                    f"我已连续 3 次尝试相同的工具调用但都失败（{sig}），"
                    f"按智管熔断规则提前结束这轮工具调用。{ok_summary}。"
                    f"建议：1) 换用别的工具；2) 重新描述任务；"
                    f"3) 直接告诉用户当前能力边界。"
                )
                s_fb = {"kind": "answer", "content": fallback_answer,
                        "forced_by": "soft_breaker", "signature": sig}
                steps.append(s_fb)
                if on_step:
                    await on_step(s_fb)
                return fallback_answer, steps

        convo.append({"role": "user", "content": tool_results})

    # 轮数耗尽：无 tools 强制收尾
    data = await _create_message(system, convo, None, model=active_model)
    answer = "".join(b.get("text", "") for b in (data.get("content") or [])
                     if b.get("type") == "text").strip() or "（达到最大工具调用轮数）"
    s = {"kind": "answer", "content": answer}
    steps.append(s)
    if on_step: await on_step(s)
    return answer, steps


def _try_parse_payload(content: Any) -> Any:
    """toolresult.content 可能是 JSON 字符串；尝试解 dict 用来判定是否错误。
    解析失败/非 JSON 时原样返回（避免误判）。"""
    if not isinstance(content, str):
        return content
    s = content.strip()
    if not s or s[0] not in "{[":
        return content
    try:
        import json as _json
        return _json.loads(s)
    except Exception:
        return content
