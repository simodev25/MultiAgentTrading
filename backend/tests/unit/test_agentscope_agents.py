from unittest.mock import MagicMock
from app.services.agentscope.agents import (
    build_technical_analyst, build_news_analyst, build_market_context_analyst,
    build_bullish_researcher, build_bearish_researcher,
    build_trader, build_risk_manager, build_execution_manager,
    ALL_AGENT_FACTORIES,
)


def _mock_deps():
    model = MagicMock()
    formatter = MagicMock()
    toolkit = MagicMock()
    toolkit.get_json_schemas.return_value = []
    return model, formatter, toolkit


def test_all_factories_exist():
    assert len(ALL_AGENT_FACTORIES) == 8


def test_build_technical_analyst_name():
    model, formatter, toolkit = _mock_deps()
    agent = build_technical_analyst(model=model, formatter=formatter, toolkit=toolkit, sys_prompt="test")
    assert agent.name == "technical-analyst"


def test_build_trader_name():
    model, formatter, toolkit = _mock_deps()
    agent = build_trader(model=model, formatter=formatter, toolkit=toolkit, sys_prompt="test")
    assert agent.name == "trader-agent"


def test_all_agents_have_memory():
    model, formatter, toolkit = _mock_deps()
    for name, factory in ALL_AGENT_FACTORIES.items():
        agent = factory(model=model, formatter=formatter, toolkit=toolkit, sys_prompt="test")
        assert agent.memory is not None, f"{name} has no memory"


def test_analysts_have_parallel_tool_calls():
    model, formatter, toolkit = _mock_deps()
    for name in ("technical-analyst", "news-analyst", "market-context-analyst"):
        agent = ALL_AGENT_FACTORIES[name](model=model, formatter=formatter, toolkit=toolkit, sys_prompt="test")
        assert agent.parallel_tool_calls is True, f"{name} should have parallel_tool_calls=True"
