"""QuantChive agent ReAct 循环。工具复用 mcp_server(单一定义):schema 来自 list_tools、
执行来自 _TOOLS 名→函数直调(返 dict)。provider 无关(llm.py)。可测(注入 FakeLLMClient)。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from quantchive.agent.llm import LLMClient
from quantchive import mcp_server

_SYSTEM = """你是 QuantChive 市场观测助手,回答 A 股资金流/行情问题,只用提供的工具查真实数据。

ReAct 方式:先用一句话说明你要查什么(思考),再调用工具;拿到结果后基于观察继续或作答。

铁律(必须遵守):
- 红涨绿跌(A股语义):净额为正=资金净流入、负=净流出。
- 金额是字符串(元),只如实转述,不要自己加减/换算错。
- 无前视:不编造未来数据;只依据工具返回。
- 诚实:如实说明数据的覆盖率(coverage_pct)、时点、是否非实时(is_stale)、断点;工具没返的别编。
- 数据来自工具,不确定就再查或明说不知道,绝不杜撰行情/个股数字。

回答简洁、用中文、给出关键数字和它的时点/来源。"""


@dataclass
class AgentResult:
    answer: str
    steps: list[dict] = field(default_factory=list)   # {type: thought|tool|observation|error, ...}
    provider: str = ""
    model: str = ""


def _tool_registry() -> dict[str, Any]:
    """名→函数(执行用),来自 mcp_server._TOOLS(单一定义)。"""
    return {fn.__name__: fn for fn in mcp_server._TOOLS}


def _tool_schemas() -> list[dict]:
    """中性 schema(给 LLM),来自 mcp.list_tools()——FastMCP 已从类型注解生成。"""
    tools = asyncio.run(mcp_server.mcp.list_tools())
    return [{"name": t.name, "description": t.description or "",
             "inputSchema": t.inputSchema} for t in tools]


def _summarize(obs: Any, limit: int = 1500) -> str:
    """观察结果转字符串回灌 LLM(截断防爆 token)。"""
    s = json.dumps(obs, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + "…(截断)"


class AgentRunner:
    def __init__(self, llm: LLMClient, *, tool_schemas: list[dict] | None = None,
                 tool_funcs: dict[str, Any] | None = None, max_steps: int = 6,
                 provider: str = "", model: str = "") -> None:
        self._llm = llm
        self._schemas = tool_schemas if tool_schemas is not None else _tool_schemas()
        self._funcs = tool_funcs if tool_funcs is not None else _tool_registry()
        self._max_steps = max_steps
        self._provider, self._model = provider, model

    def run(self, question: str, history: list[dict] | None = None) -> AgentResult:
        messages: list[dict] = list(history or [])
        messages.append({"role": "user", "content": question})
        steps: list[dict] = []

        for _ in range(self._max_steps):
            turn = self._llm.chat(system=_SYSTEM, messages=messages, tools=self._schemas)
            if turn.text:
                steps.append({"type": "thought", "text": turn.text})
            if not turn.tool_calls:
                return AgentResult(answer=turn.text or "(无回答)", steps=steps,
                                   provider=self._provider, model=self._model)
            # 记助手发起的工具调用轮 → 回灌
            messages.append({"role": "assistant", "content": turn.text,
                             "tool_calls": turn.tool_calls})
            for call in turn.tool_calls:
                fn = self._funcs.get(call.name)
                if fn is None:
                    obs = {"error": {"code": "UNKNOWN_TOOL", "message": f"无工具 {call.name}"}}
                else:
                    try:
                        obs = fn(**call.arguments)
                    except Exception as exc:   # 工具异常不崩循环,作为观察回灌
                        obs = {"error": {"code": "TOOL_EXCEPTION", "message": str(exc)}}
                steps.append({"type": "tool", "name": call.name, "arguments": call.arguments})
                steps.append({"type": "observation", "name": call.name, "summary": _summarize(obs)})
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "name": call.name, "content": _summarize(obs)})
        # 到达步数上限仍未收束 → 让 LLM 基于已有观察给最终答（不再给工具）
        final = self._llm.chat(system=_SYSTEM, messages=messages, tools=[])
        return AgentResult(answer=final.text or "(达到最大步数,未得结论)", steps=steps,
                           provider=self._provider, model=self._model)
