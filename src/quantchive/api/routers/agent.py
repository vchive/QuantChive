"""QuantChive agent 路由。POST /api/agent/ask → ReAct 循环查数回答（前端聊天面板用）。

LLM 可注入(测试用 FakeLLMClient,不联网)。未配 key → 明确错误引导,不静默。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from quantchive.agent.llm import make_llm, resolve_llm_config
from quantchive.agent.runner import AgentRunner
from quantchive.core.settings import get_settings

router = APIRouter(prefix="/api/agent", tags=["agent"])


class AskRequest(BaseModel):
    question: str
    history: list[dict] = []


class AskResponse(BaseModel):
    answer: str
    steps: list[dict] = []
    provider: str = ""
    model: str = ""


# 测试可 monkeypatch 这个工厂注入 FakeLLMClient
def _make_runner() -> AgentRunner:
    settings = get_settings()
    provider, _key, _base, model = resolve_llm_config(settings)  # 显示真实解析结果
    llm = make_llm(settings)
    return AgentRunner(llm, max_steps=settings.llm_max_steps,
                       provider=provider, model=model)


@router.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    """自然语言问 → agent ReAct 调工具 → 答案 + 思考/工具/观察轨迹。"""
    try:
        runner = _make_runner()
    except ValueError as exc:
        # 未配 LLM → 明确引导（宪章 V 不静默）
        return AskResponse(answer=f"⚠️ agent 未就绪:{exc}。请配置 QUANTCHIVE_LLM_API_KEY "
                                  f"(及 QUANTCHIVE_LLM_PROVIDER / _MODEL / _BASE_URL)。", steps=[])
    result = runner.run(req.question, history=req.history)
    return AskResponse(answer=result.answer, steps=result.steps,
                       provider=result.provider, model=result.model)
