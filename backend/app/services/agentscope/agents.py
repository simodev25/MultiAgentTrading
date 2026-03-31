"""Factory functions for creating the 8 trading ReActAgents."""
from __future__ import annotations

from agentscope.agent import ReActAgent
from agentscope.memory import InMemoryMemory


def _build_agent(
    name: str,
    model,
    formatter,
    toolkit,
    sys_prompt: str,
    max_iters: int = 3,
    parallel_tool_calls: bool = False,
) -> ReActAgent:
    return ReActAgent(
        name=name,
        sys_prompt=sys_prompt,
        model=model,
        formatter=formatter,
        toolkit=toolkit,
        memory=InMemoryMemory(),
        max_iters=max_iters,
        parallel_tool_calls=parallel_tool_calls,
    )


def build_technical_analyst(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 5) -> ReActAgent:
    return _build_agent("technical-analyst", model, formatter, toolkit, sys_prompt, max_iters, parallel_tool_calls=True)


def build_news_analyst(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 4) -> ReActAgent:
    return _build_agent("news-analyst", model, formatter, toolkit, sys_prompt, max_iters, parallel_tool_calls=True)


def build_market_context_analyst(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 5) -> ReActAgent:
    return _build_agent("market-context-analyst", model, formatter, toolkit, sys_prompt, max_iters, parallel_tool_calls=True)


def build_bullish_researcher(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 4) -> ReActAgent:
    return _build_agent("bullish-researcher", model, formatter, toolkit, sys_prompt, max_iters)


def build_bearish_researcher(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 4) -> ReActAgent:
    return _build_agent("bearish-researcher", model, formatter, toolkit, sys_prompt, max_iters)


def build_trader(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 5) -> ReActAgent:
    return _build_agent("trader-agent", model, formatter, toolkit, sys_prompt, max_iters)


def build_risk_manager(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 4) -> ReActAgent:
    return _build_agent("risk-manager", model, formatter, toolkit, sys_prompt, max_iters)


def build_execution_manager(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 4) -> ReActAgent:
    return _build_agent("execution-manager", model, formatter, toolkit, sys_prompt, max_iters)


def build_strategy_designer(*, model, formatter, toolkit, sys_prompt: str, max_iters: int = 6) -> ReActAgent:
    return _build_agent("strategy-designer", model, formatter, toolkit, sys_prompt, max_iters, parallel_tool_calls=True)


ALL_AGENT_FACTORIES = {
    "technical-analyst": build_technical_analyst,
    "news-analyst": build_news_analyst,
    "market-context-analyst": build_market_context_analyst,
    "bullish-researcher": build_bullish_researcher,
    "bearish-researcher": build_bearish_researcher,
    "trader-agent": build_trader,
    "risk-manager": build_risk_manager,
    "execution-manager": build_execution_manager,
}
