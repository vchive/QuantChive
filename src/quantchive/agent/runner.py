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

工具用法要点:
- 查**具体某只股/板块**(如"东山精密"、"半导体")→ 先 `search_subject("名字")` 拿 subject_id,
  再用该 id 调 `get_tiers_series`(单股四档博弈+背离)或 `get_series`。别用 list_subjects 翻全表。
- 查**全市场/横截面榜**(如"主力流入前10")→ `scan_market_stocks` 或 `rank_sectors`。
- 单股"多空力量":用 `get_tiers_series` 看主力净额(正=多方/净流入强、负=空方/净流出强)、
  四档流入流出、以及背离段(吸筹/派发)。
- 查**某信号历史胜率/前瞻研判**(如"东山精密吸筹信号后涨的概率")→ `signal_backtest`,
  返回该信号历史触发次数、后续T日胜率、Wilson置信区间、对照基准、样本是否可靠。
  这是**有据的历史统计**,可作前瞻参考;样本不足(n<30)必须如实说不可靠。
- 查**基本面**(如"东山精密业绩/ROE/净利增速/负债率")→ `describe_fundamentals`,
  返回业绩+三大报表(EPS/ROE/营收净利同比/毛利率/总资产/资产负债率/经营现金流等)。
  客观转述财报数字,不臆测估值高低或买卖建议。
- 查**基本面信号历史表现**(如"净利增速转正后涨的概率")→ `fundamental_signal_backtest`,
  返回净利转正/加速、ROE跳升、营收加速等信号,前向20/60日历史胜率+置信区间+基准。
  可见时点按**法定披露截止日**(保守估计);基本面事件稀疏,样本不足必说不可靠;历史统计非预测。

铁律(必须遵守):
- 红涨绿跌:净额为正=资金净流入(多方)、负=净流出(空方)。
- 金额是字符串(元),只如实转述,不要自己加减/换算错。
- **不预测、不编概率**:不能凭数据**编**"明天上涨概率 X%"。但可用 `signal_backtest` 给
  **历史条件胜率**(如"这类信号历史5日胜率62%,CI 55%~68%,样本80次")——这是历史统计非预测,
  必须标注"历史统计,非未来预测",且样本不足时说明不可靠。可基于今日资金面客观描述多空/背离。
- 无前视、诚实:只依据工具返回;如实说覆盖率/时点/is_stale;工具没返的别编。
- 数据来自工具,不确定就再查或明说不知道,绝不杜撰行情数字。

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
