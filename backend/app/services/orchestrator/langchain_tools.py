"""Agent tool layer — MCP‑backed tools exposed via LangChain interface.

Each tool delegates to the MCP Trading Server for **real computation**
instead of echoing pre‑assembled data.  The LangChain ``@tool`` wrappers
remain for backward‑compatibility with the agent invocation loop, but all
actual logic lives in the MCP server (``mcp_trading_server.py``).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool


def _get_mcp_client():
    """Lazy import to avoid circular dependency with agent_runtime."""
    from app.services.agent_runtime.mcp_client import get_mcp_client
    return get_mcp_client()


def _as_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return dict(payload)
    return {}


# ---------------------------------------------------------------------------
# MCP‑backed tools
# ---------------------------------------------------------------------------

@tool('news_search')
def news_search_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize, deduplicate and score news batch by symbol relevance via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('news_search', _as_dict(payload))
    return result.data if result.status == 'ok' else {'items': [], 'count': 0, 'error': result.error}


@tool('macro_calendar_or_event_feed')
def macro_calendar_or_event_feed_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Filter and score macro-economic events by impact via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('macro_event_feed', _as_dict(payload))
    return result.data if result.status == 'ok' else {'items': [], 'count': 0, 'error': result.error}


@tool('symbol_relevance_filter')
def symbol_relevance_filter_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Filter news and macro by relevance threshold for a symbol via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('symbol_relevance_filter', _as_dict(payload))
    return result.data if result.status == 'ok' else {
        'retained_news_count': 0, 'retained_macro_count': 0,
        'strongest_relevance': 0.0, 'average_relevance': 0.0,
    }


@tool('sentiment_or_event_impact_parser')
def sentiment_or_event_impact_parser_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Parse directional sentiment from headlines via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('sentiment_parser', _as_dict(payload))
    return result.data if result.status == 'ok' else {
        'bullish_hints': 0, 'bearish_hints': 0, 'neutral_hints': 0,
    }


@tool('market_snapshot')
def market_snapshot_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalized market snapshot with derived metrics (spread ratio, candle ratios) via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('market_snapshot', _as_dict(payload))
    return result.data if result.status == 'ok' else {'error': result.error or 'market_snapshot_failed'}


@tool('indicator_bundle')
def indicator_bundle_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Real RSI, EMA, MACD, ATR calculation from OHLC data via MCP — no passthrough."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('indicator_bundle', _as_dict(payload))
    return result.data if result.status == 'ok' else {'error': result.error or 'indicator_bundle_failed'}


@tool('divergence_detector')
def divergence_detector_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """RSI-price bullish/bearish divergence detection via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('divergence_detector', _as_dict(payload))
    return result.data if result.status == 'ok' else {'divergences': [], 'count': 0}


@tool('support_resistance_or_structure_detector')
def support_resistance_or_structure_detector_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """S/R level identification by pivot clustering via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('support_resistance_detector', _as_dict(payload))
    return result.data if result.status == 'ok' else {'levels': [], 'count': 0}


@tool('pattern_detector')
def pattern_detector_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Candlestick pattern detection (doji, hammer, engulfing, pin bar) via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('pattern_detector', _as_dict(payload))
    return result.data if result.status == 'ok' else {'patterns': [], 'count': 0}


@tool('multi_timeframe_context')
def multi_timeframe_context_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Multi-TF alignment synthesis with confluence and dominant direction via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('multi_timeframe_context', _as_dict(payload))
    return result.data if result.status == 'ok' else {'error': result.error or 'multi_timeframe_context_failed'}


@tool('market_regime_context')
def market_regime_context_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Market regime classification (trending/ranging/volatile/calm) via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('market_regime_detector', _as_dict(payload))
    return result.data if result.status == 'ok' else {'regime': 'unknown'}


@tool('session_context')
def session_context_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Active market sessions, overlaps and real-time liquidity via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('session_context', _as_dict(payload))
    return result.data if result.status == 'ok' else {'active_sessions': [], 'liquidity': 'unknown'}


@tool('correlation_context')
def correlation_context_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Rolling Pearson correlation between price series via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('correlation_analyzer', _as_dict(payload))
    return result.data if result.status == 'ok' else {'correlation': 0.0, 'strength': 'unknown'}


@tool('volatility_context')
def volatility_context_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """ATR, historical volatility, Bollinger bandwidth and percentile via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('volatility_analyzer', _as_dict(payload))
    return result.data if result.status == 'ok' else {'volatility_regime': 'unknown'}


@tool('evidence_query')
def evidence_query_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Agent evidence aggregation and scoring with directional consensus via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('evidence_query', _as_dict(payload))
    return result.data if result.status == 'ok' else {'analysis_outputs': {}, 'analysis_count': 0}


@tool('thesis_support_extractor')
def thesis_support_extractor_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Thesis argument normalization and weighting via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('thesis_support_extractor', _as_dict(payload))
    return result.data if result.status == 'ok' else {
        'supporting_arguments': [], 'opposing_arguments': [],
    }


@tool('scenario_validation')
def scenario_validation_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Trading scenario validation with SL/TP geometry and R:R ratio via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('scenario_validation', _as_dict(payload))
    return result.data if result.status == 'ok' else {'invalidation_conditions': []}


@tool('position_size_calculator')
def position_size_calculator_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Asset-class-adapted position size calculation with margin verification via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('position_size_calculator', _as_dict(payload))
    return result.data if result.status == 'ok' else {'suggested_volume': 0.01, 'error': result.error}


@tool('memory_query')
def memory_query_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Agentic memory access: search, feedback, statistics via MCP."""
    adapter = _get_mcp_client()
    result = adapter.call_tool('memory_query', _as_dict(payload))
    return result.data if result.status == 'ok' else {'status': 'error', 'error': result.error}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

LANGCHAIN_AGENT_TOOLS: dict[str, BaseTool] = {
    'news_search': news_search_tool,
    'macro_calendar_or_event_feed': macro_calendar_or_event_feed_tool,
    'symbol_relevance_filter': symbol_relevance_filter_tool,
    'sentiment_or_event_impact_parser': sentiment_or_event_impact_parser_tool,
    'market_snapshot': market_snapshot_tool,
    'indicator_bundle': indicator_bundle_tool,
    'divergence_detector': divergence_detector_tool,
    'support_resistance_or_structure_detector': support_resistance_or_structure_detector_tool,
    'pattern_detector': pattern_detector_tool,
    'multi_timeframe_context': multi_timeframe_context_tool,
    'market_regime_context': market_regime_context_tool,
    'session_context': session_context_tool,
    'correlation_context': correlation_context_tool,
    'volatility_context': volatility_context_tool,
    'evidence_query': evidence_query_tool,
    'thesis_support_extractor': thesis_support_extractor_tool,
    'scenario_validation': scenario_validation_tool,
    'position_size_calculator': position_size_calculator_tool,
    'memory_query': memory_query_tool,
}


def get_langchain_agent_tool(tool_id: str) -> BaseTool | None:
    key = str(tool_id or '').strip()
    if not key:
        return None
    return LANGCHAIN_AGENT_TOOLS.get(key)


def build_llm_tool_specs(tool_ids: list[str]) -> list[dict[str, Any]]:
    """Build OpenAI-compatible function tool specs — now uses MCP adapter for typed schemas."""
    adapter = _get_mcp_client()
    mcp_specs = adapter.build_tool_specs(tool_ids)

    # Fallback for tools not in MCP (legacy compatibility)
    mcp_names = {spec['function']['name'] for spec in mcp_specs}
    seen: set[str] = set(mcp_names)

    for raw_tool_id in tool_ids:
        tool_id = str(raw_tool_id or '').strip()
        if not tool_id or tool_id in seen:
            continue
        seen.add(tool_id)
        lc_tool = get_langchain_agent_tool(tool_id)
        if lc_tool is None:
            continue
        description = str(getattr(lc_tool, 'description', '') or '').strip() or f'Runtime tool: {tool_id}'
        mcp_specs.append({
            'type': 'function',
            'function': {
                'name': tool_id,
                'description': description,
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'payload': {
                            'type': 'object',
                            'description': 'Tool arguments.',
                        }
                    },
                    'additionalProperties': False,
                },
            },
        })

    return mcp_specs
