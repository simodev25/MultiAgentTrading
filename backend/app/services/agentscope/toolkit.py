"""Per-agent Toolkit builder — maps agent names to MCP tool subsets."""
from __future__ import annotations

import functools
import inspect
import json
import logging
from typing import Any

from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock

from app.services.mcp.client import get_mcp_client

logger = logging.getLogger(__name__)

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
    "strategy-designer": [
        "indicator_bundle", "market_regime_detector", "technical_scoring",
        "volatility_analyzer", "strategy_templates_info", "strategy_builder",
    ],
}


def _build_docstring(tool_id: str, original_fn) -> str:
    """Build a docstring with proper Args section from the original function signature."""
    sig = inspect.signature(original_fn)
    doc_lines = [original_fn.__doc__.strip().split("\n")[0] if original_fn.__doc__ else f"Execute the {tool_id} tool."]
    doc_lines.append("")
    doc_lines.append("Args:")

    for pname, p in sig.parameters.items():
        ann = p.annotation
        if ann is inspect.Parameter.empty:
            type_str = "Any"
        elif hasattr(ann, "__name__"):
            type_str = ann.__name__
        else:
            type_str = str(ann).replace("typing.", "")

        if p.default is not inspect.Parameter.empty:
            doc_lines.append(f"    {pname} ({type_str}):")
            doc_lines.append(f"        Default: {p.default!r}")
        else:
            doc_lines.append(f"    {pname} ({type_str}):")
            doc_lines.append(f"        Required parameter.")

    return "\n".join(doc_lines)


def _wrap_mcp_tool(tool_id: str, original_fn) -> Any:
    """Create an async wrapper that preserves the original function's signature.

    AgentScope parses function signatures and docstrings to build JSON schemas.
    By copying the real signature, the LLM sees the actual parameter names and types.
    """
    client = get_mcp_client()
    sig = inspect.signature(original_fn)

    @functools.wraps(original_fn)
    async def tool_fn(*args: Any, **kwargs: Any) -> ToolResponse:
        try:
            # Bind positional args to parameter names
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            result = await client.call_tool(tool_id, dict(bound.arguments))
            return ToolResponse(
                content=[TextBlock(type="text", text=json.dumps(result, default=str))],
            )
        except Exception as exc:
            logger.warning("Tool %s execution failed: %s", tool_id, exc, exc_info=True)
            error_result = {"error": f"{type(exc).__name__}: {exc}", "tool_id": tool_id}
            return ToolResponse(
                content=[TextBlock(type="text", text=json.dumps(error_result, default=str))],
            )

    # Override docstring with a clean Args section for AgentScope parsing
    tool_fn.__doc__ = _build_docstring(tool_id, original_fn)
    return tool_fn


OHLC_PARAMS = frozenset({"closes", "highs", "lows", "opens"})


async def build_toolkit(
    agent_name: str,
    ohlc: dict[str, list[float]] | None = None,
    news: dict | None = None,
    analysis_outputs: dict | None = None,
    skills: list[str] | None = None,
    snapshot: dict | None = None,
) -> Toolkit:
    """Build a Toolkit with the MCP tools assigned to the given agent.

    Args:
        agent_name: Agent identifier.
        ohlc: Optional dict with keys "opens", "highs", "lows", "closes".
        news: Optional dict with keys "news" (list), "macro_events" (list).
        analysis_outputs: Optional dict of agent outputs for evidence_query tool.
        skills: Optional list of skill strings from DB to inject via AgentScope native mechanism.
        snapshot: Optional market snapshot dict for pre-injecting authoritative
            values into trader-agent tools (contradiction_detector, trade_sizing)
            so the LLM cannot invent incorrect market data.
    """
    from app.services.mcp import trading_server
    from agentscope.tool._toolkit import AgentSkill

    toolkit = Toolkit(
        agent_skill_instruction="# Agent Behavioral Rules\nYou MUST follow these rules strictly:\n",
        agent_skill_template="## {name}\n{description}",
    )

    # Try loading skills from SKILL.md file first (AgentScope native)
    import os
    # backend/config/skills/{agent_name}/SKILL.md
    _backend_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    skill_dir = os.path.join(_backend_root, "config", "skills", agent_name)
    if os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
        try:
            toolkit.register_agent_skill(skill_dir)
            logger.info("Loaded SKILL.md for %s from %s", agent_name, skill_dir)
        except Exception as exc:
            logger.warning("Failed to load SKILL.md for %s: %s", agent_name, exc)

    # Also inject DB skills as additional rules (if not already loaded from file)
    if skills and not toolkit.skills:
        for i, skill_text in enumerate(skills):
            skill_name = f"{agent_name}-rule-{i + 1}"
            toolkit.skills[skill_name] = AgentSkill(
                name=skill_name,
                description=skill_text,
                dir="",
            )

    tool_ids = AGENT_TOOL_MAP.get(agent_name, [])
    ohlc = ohlc or {}
    news = news or {}

    for tool_id in tool_ids:
        original_fn = getattr(trading_server, tool_id, None)
        if original_fn is None:
            logger.warning("MCP tool %s not found in trading_server, skipping", tool_id)
            continue

        wrapped = _wrap_mcp_tool(tool_id, original_fn)

        # Auto-inject OHLC arrays as preset kwargs when the tool accepts them
        sig = inspect.signature(original_fn)
        preset = {}
        for param_name in sig.parameters:
            if param_name in OHLC_PARAMS and param_name in ohlc:
                preset[param_name] = ohlc[param_name]

        # Inject analysis_outputs for evidence tools
        if tool_id == "evidence_query" and analysis_outputs:
            # Build summary dict for evidence_query
            preset["analysis_outputs"] = {
                k: v.get("metadata", {}) for k, v in (analysis_outputs or {}).items()
            }

        # Inject news items for news-related tools
        if tool_id == "news_search" and news.get("news"):
            preset["items"] = news["news"]
        elif tool_id == "macro_event_feed" and news.get("macro_events"):
            preset["items"] = news["macro_events"]
        elif tool_id == "sentiment_parser" and news.get("news"):
            preset["headlines"] = [n.get("title", "") for n in news["news"] if n.get("title")]
        elif tool_id == "symbol_relevance_filter":
            if news.get("news"):
                preset["news_items"] = news["news"]
            if news.get("macro_events"):
                preset["macro_items"] = news["macro_events"]

        # Pre-inject authoritative market data into trader-agent tools so the
        # LLM cannot invent incorrect values (e.g. macd_diff=-0.01 instead of
        # the real -0.000119).  The LLM still chooses *when* to call these
        # tools and provides qualitative args (trend, momentum, decision_side),
        # but the numerical market facts come from the snapshot.
        if snapshot and tool_id == "contradiction_detector":
            preset["macd_diff"] = snapshot.get("macd_diff", 0.0)
            preset["atr"] = snapshot.get("atr", 0.001)
        elif snapshot and tool_id == "trade_sizing":
            preset["price"] = snapshot.get("last_price", 0.0)
            preset["atr"] = snapshot.get("atr", 0.0)

        toolkit.register_tool_function(wrapped, preset_kwargs=preset if preset else None)

    return toolkit
