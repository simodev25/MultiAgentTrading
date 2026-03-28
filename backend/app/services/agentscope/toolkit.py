"""Per-agent Toolkit builder — maps agent names to MCP tool subsets."""
from __future__ import annotations

import json

from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock

from app.services.mcp.client import get_mcp_client

AGENT_TOOL_MAP: dict[str, list[str]] = {
    "technical-analyst": [
        "indicator_bundle", "divergence_detector", "pattern_detector",
        "support_resistance_detector", "multi_timeframe_context",
        "technical_scoring",
    ],
    "news-analyst": [
        "news_search", "macro_event_feed", "sentiment_parser",
        "symbol_relevance_filter", "news_evidence_scoring",
        "news_validation",
    ],
    "market-context-analyst": [
        "market_regime_detector", "session_context",
        "volatility_analyzer", "correlation_analyzer",
    ],
    "bullish-researcher": ["evidence_query", "thesis_support_extractor"],
    "bearish-researcher": ["evidence_query", "thesis_support_extractor"],
    "trader-agent": [
        "scenario_validation", "decision_gating",
        "contradiction_detector", "trade_sizing",
    ],
    "risk-manager": ["position_size_calculator", "risk_evaluation"],
    "execution-manager": ["market_snapshot"],
}


def _wrap_mcp_tool(tool_id: str):
    """Create an async tool function that delegates to the in-process MCP client."""
    client = get_mcp_client()

    async def tool_fn(**kwargs) -> ToolResponse:
        result = await client.call_tool(tool_id, kwargs)
        return ToolResponse(
            content=[TextBlock(type="text", text=json.dumps(result, default=str))],
        )

    tool_fn.__name__ = tool_id
    tool_fn.__qualname__ = tool_id
    tool_fn.__doc__ = f"Call MCP tool '{tool_id}' with the given parameters.\n\nArgs:\n    **kwargs: Tool-specific parameters."
    return tool_fn


async def build_toolkit(agent_name: str) -> Toolkit:
    """Build a Toolkit with the MCP tools assigned to the given agent."""
    toolkit = Toolkit()
    tool_ids = AGENT_TOOL_MAP.get(agent_name, [])
    for tool_id in tool_ids:
        toolkit.register_tool_function(_wrap_mcp_tool(tool_id))
    return toolkit
