"""QuantChive agent ReAct runner 单测。FakeLLMClient 脚本化,不联网(宪章 IV)。"""

from __future__ import annotations

from quantchive.agent.llm import LLMTurn, ToolCall
from quantchive.agent.runner import AgentRunner


class FakeLLM:
    """按预设脚本逐轮返 LLMTurn（模拟 ReAct:先思考+调工具,拿到观察后作答）。"""

    def __init__(self, turns: list[LLMTurn]) -> None:
        self._turns = turns
        self._i = 0
        self.calls: list[dict] = []   # 记录每次 chat 的 messages(验证观察回灌)

    def chat(self, *, system, messages, tools) -> LLMTurn:
        self.calls.append({"messages": list(messages), "n_tools": len(tools)})
        turn = self._turns[min(self._i, len(self._turns) - 1)]
        self._i += 1
        return turn


def test_react_loop_calls_tool_then_answers() -> None:
    """第一轮思考+调工具 → 执行 → 第二轮据观察作答。"""
    fake = FakeLLM([
        LLMTurn(text="我查一下全市场主力净流入榜。",
                tool_calls=[ToolCall(id="c1", name="scan_market_stocks",
                                     arguments={"sort_by": "main_net", "top_n": 3})]),
        LLMTurn(text="今天主力净流入前列的是……(基于工具结果)"),
    ])
    # 注入假工具:不碰真 DB
    fake_tool_result = {"top_inflow": [{"display_name": "甲", "main_net": "500000000.00"}],
                        "top_outflow": [], "coverage_pct": "93.5"}
    runner = AgentRunner(
        fake, tool_schemas=[{"name": "scan_market_stocks", "description": "全市场榜",
                             "inputSchema": {"type": "object", "properties": {}}}],
        tool_funcs={"scan_market_stocks": lambda **kw: fake_tool_result},
        provider="fake", model="fake-1")
    res = runner.run("今天哪些股主力流入最多")
    assert "基于工具结果" in res.answer
    # ReAct 轨迹:思考 → 工具 → 观察 → 思考
    types = [s["type"] for s in res.steps]
    assert types == ["thought", "tool", "observation", "thought"]
    assert res.steps[1]["name"] == "scan_market_stocks"
    assert "甲" in res.steps[2]["summary"]           # 观察含工具结果
    # 第二轮 messages 里应有工具结果回灌
    assert any(m.get("role") == "tool" for m in fake.calls[1]["messages"])


def test_tool_exception_becomes_observation() -> None:
    """工具抛异常不崩循环,作为观察回灌,agent 仍能收束。"""
    def _boom(**kw):
        raise RuntimeError("db down")
    fake = FakeLLM([
        LLMTurn(tool_calls=[ToolCall(id="c1", name="market_overview", arguments={})]),
        LLMTurn(text="抱歉,数据暂不可用。"),
    ])
    runner = AgentRunner(
        fake, tool_schemas=[{"name": "market_overview", "description": "大盘",
                             "inputSchema": {"type": "object", "properties": {}}}],
        tool_funcs={"market_overview": _boom})
    res = runner.run("大盘怎么样")
    assert "数据暂不可用" in res.answer
    obs = next(s for s in res.steps if s["type"] == "observation")
    assert "TOOL_EXCEPTION" in obs["summary"] and "db down" in obs["summary"]


def test_direct_answer_no_tool() -> None:
    """无需工具时直接作答(无 tool step)。"""
    fake = FakeLLM([LLMTurn(text="你好,我是 QuantChive 助手。")])
    runner = AgentRunner(fake, tool_schemas=[], tool_funcs={})
    res = runner.run("你是谁")
    assert res.answer.startswith("你好")
    assert all(s["type"] == "thought" for s in res.steps)


def test_max_steps_forces_final_answer() -> None:
    """一直返工具调用 → 到步数上限强制作答(不无限循环)。"""
    fake = FakeLLM([
        LLMTurn(tool_calls=[ToolCall(id="c", name="market_overview", arguments={})]),
    ])  # 永远返工具调用
    runner = AgentRunner(
        fake, tool_schemas=[{"name": "market_overview", "description": "大盘",
                             "inputSchema": {"type": "object", "properties": {}}}],
        tool_funcs={"market_overview": lambda **kw: {"ok": 1}}, max_steps=3)
    res = runner.run("循环测试")
    assert res.answer   # 有最终答案(强制收束),不挂


def test_agent_endpoint_with_injected_runner(monkeypatch) -> None:
    """端点：monkeypatch _make_runner 注入 fake，验证返 answer+steps（不联网、不需 key）。"""
    from fastapi.testclient import TestClient
    from quantchive.app import create_app
    import quantchive.api.routers.agent as agent_router
    fake = FakeLLM([LLMTurn(text="大盘今日主力净流出约 760 亿。")])
    runner = AgentRunner(fake, tool_schemas=[], tool_funcs={}, provider="fake", model="fake-1")
    monkeypatch.setattr(agent_router, "_make_runner", lambda: runner)
    c = TestClient(create_app())
    r = c.post("/api/agent/ask", json={"question": "大盘怎么样"})
    assert r.status_code == 200
    j = r.json()
    assert "760" in j["answer"] and j["provider"] == "fake"


# ---- 分层回退 LLM 配置解析 ----

class _S:
    """最小 settings 桩,只带 LLM 字段。"""
    def __init__(self, provider="openai", api_key="", base_url="", model="gpt-4o"):
        self.llm_provider = provider
        self.llm_api_key = api_key
        self.llm_base_url = base_url
        self.llm_model = model


def test_resolve_explicit_quantchive_key_wins(monkeypatch) -> None:
    from quantchive.agent.llm import resolve_llm_config
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai")
    p, key, base, model = resolve_llm_config(_S(provider="openai", api_key="explicit"))
    assert p == "openai" and key == "explicit"   # 显式优先于环境


def test_resolve_fallback_openai_env(monkeypatch) -> None:
    from quantchive.agent.llm import resolve_llm_config
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai")
    p, key, base, model = resolve_llm_config(_S(provider="openai", api_key=""))
    assert p == "openai" and key == "env-openai"


def test_resolve_auto_switch_to_anthropic(monkeypatch) -> None:
    """默认 openai 无 key,但本地有 Anthropic → 自动切 anthropic,复用其 token/base。"""
    from quantchive.agent.llm import resolve_llm_config
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "env-claude")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gw.example/anthropic")
    p, key, base, model = resolve_llm_config(_S(provider="openai", api_key="", model="gpt-4o"))
    assert p == "anthropic" and key == "env-claude"
    assert base == "https://gw.example/anthropic"
    assert model == "claude-sonnet-5"    # gpt 默认名 → 换 claude 默认


def test_resolve_explicit_anthropic_provider(monkeypatch) -> None:
    """显式 provider=anthropic,key 回退 ANTHROPIC_AUTH_TOKEN。"""
    from quantchive.agent.llm import resolve_llm_config
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    p, key, base, model = resolve_llm_config(_S(provider="anthropic", api_key="", model="claude-opus-4-8"))
    assert p == "anthropic" and key == "tok" and model == "claude-opus-4-8"   # 显式 claude 名沿用
