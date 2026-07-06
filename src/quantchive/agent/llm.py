"""LLM provider 抽象（QuantChive agent）。provider 无关:openai 兼容 / anthropic。

中性 messages 由各 client 内部翻译成 provider 原生格式,使 AgentRunner 的 ReAct 循环
与 provider 解耦。tools 是中性 schema(来自 mcp.list_tools),各 client 翻译成自家 tool 格式。
测试注入 FakeLLMClient,不联网(宪章 IV)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMTurn:
    """LLM 一轮输出:思考文本(可空) + 工具调用(可空)。两者可并存(ReAct:先思考再调)。"""
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


# 中性 message 结构（AgentRunner 维护,client 翻译）:
#   {"role":"user"|"assistant", "content": str}                        普通轮
#   {"role":"assistant", "tool_calls":[ToolCall...], "content": str?}  助手发起工具调用
#   {"role":"tool", "tool_call_id": str, "name": str, "content": str}  工具结果回灌


class LLMClient(Protocol):
    def chat(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMTurn: ...


def _tool_to_openai(t: dict) -> dict:
    return {"type": "function", "function": {
        "name": t["name"], "description": t.get("description", ""),
        "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}}


def _tool_to_anthropic(t: dict) -> dict:
    return {"name": t["name"], "description": t.get("description", ""),
            "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}}}


class OpenAICompatClient:
    """OpenAI 兼容(OpenAI/DeepSeek/月之暗面/通义/智谱…,靠 base_url)。"""

    def __init__(self, *, api_key: str, model: str, base_url: str = "") -> None:
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key, base_url=base_url or None)
        self._model = model

    def chat(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMTurn:
        oai_msgs: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            oai_msgs.append(_neutral_to_openai(m))
        kwargs: dict[str, Any] = {"model": self._model, "messages": oai_msgs}
        if tools:
            kwargs["tools"] = [_tool_to_openai(t) for t in tools]
        resp = self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = [ToolCall(id=tc.id, name=tc.function.name,
                          arguments=json.loads(tc.function.arguments or "{}"))
                 for tc in (msg.tool_calls or [])]
        return LLMTurn(text=msg.content, tool_calls=calls)


def _neutral_to_openai(m: dict) -> dict:
    if m["role"] == "tool":
        return {"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]}
    if m.get("tool_calls"):
        return {"role": "assistant", "content": m.get("content") or None,
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                               for c in m["tool_calls"]]}
    return {"role": m["role"], "content": m.get("content", "")}


class AnthropicClient:
    """Anthropic Claude（可复用环境 ANTHROPIC_AUTH_TOKEN/BASE_URL）。"""

    def __init__(self, *, api_key: str, model: str, base_url: str = "") -> None:
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key, base_url=base_url or None)
        self._model = model

    def chat(self, *, system: str, messages: list[dict], tools: list[dict]) -> LLMTurn:
        resp = self._client.messages.create(
            model=self._model, max_tokens=2048, system=system,
            messages=[_neutral_to_anthropic(m) for m in messages],
            tools=[_tool_to_anthropic(t) for t in tools] if tools else [])
        text_parts, calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        return LLMTurn(text="".join(text_parts) or None, tool_calls=calls)


def _neutral_to_anthropic(m: dict) -> dict:
    if m["role"] == "tool":
        return {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}]}
    if m.get("tool_calls"):
        content: list[dict] = []
        if m.get("content"):
            content.append({"type": "text", "text": m["content"]})
        for c in m["tool_calls"]:
            content.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
        return {"role": "assistant", "content": content}
    return {"role": m["role"], "content": m.get("content", "")}


def resolve_llm_config(settings) -> tuple[str, str, str, str]:
    """分层回退解析 (provider, api_key, base_url, model)。

    优先级:显式 QUANTCHIVE_LLM_*(settings) > 标准环境变量(ANTHROPIC_*/OPENAI_*)。
    默认 provider=openai;若 openai 无 key 但本地有 Anthropic key → 自动切 anthropic
    (复用本地 ANTHROPIC_AUTH_TOKEN/BASE_URL,省重复配置)。
    """
    import os

    def _anthropic_key() -> str:
        return (settings.llm_api_key or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN") or "")

    def _openai_key() -> str:
        return settings.llm_api_key or os.environ.get("OPENAI_API_KEY") or ""

    provider = settings.llm_provider
    if provider == "openai" and not _openai_key() and (
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        provider = "anthropic"   # 默认 openai 无 key,回退到本地 Anthropic

    if provider == "anthropic":
        key = _anthropic_key()
        base = settings.llm_base_url or os.environ.get("ANTHROPIC_BASE_URL") or ""
        # 模型:显式非 gpt 名沿用;否则给 claude 默认(用户可 QUANTCHIVE_LLM_MODEL 覆盖)
        model = settings.llm_model
        if not model or model.startswith("gpt"):
            model = "claude-sonnet-5"
    else:
        provider = "openai"
        key = _openai_key()
        base = settings.llm_base_url or os.environ.get("OPENAI_BASE_URL") or ""
        model = settings.llm_model or "gpt-4o"
    return provider, key, base, model


def make_llm(settings) -> LLMClient:
    """按分层回退解析 provider/key/base_url/model。无任何 key 抛 ValueError(端点转明确引导)。"""
    provider, key, base, model = resolve_llm_config(settings)
    if not key:
        raise ValueError(
            "未找到 LLM key（设 QUANTCHIVE_LLM_API_KEY,或本地 OPENAI_API_KEY / ANTHROPIC_AUTH_TOKEN）")
    if provider == "anthropic":
        return AnthropicClient(api_key=key, model=model, base_url=base)
    return OpenAICompatClient(api_key=key, model=model, base_url=base)
