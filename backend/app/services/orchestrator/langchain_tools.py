from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool


def _as_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return dict(payload)
    return {}


def _as_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return list(payload)
    return []


@tool('news_search')
def news_search_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise un lot de news brutes déjà collectées par le runtime."""
    data = _as_dict(payload)
    items = _as_list(data.get('items'))
    count = data.get('count')
    return {
        'items': items,
        'count': int(count) if isinstance(count, int) else len(items),
    }


@tool('macro_calendar_or_event_feed')
def macro_calendar_or_event_feed_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise un lot d'événements macro déjà collectés par le runtime."""
    data = _as_dict(payload)
    items = _as_list(data.get('items'))
    count = data.get('count')
    return {
        'items': items,
        'count': int(count) if isinstance(count, int) else len(items),
    }


@tool('symbol_relevance_filter')
def symbol_relevance_filter_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose des métriques de pertinence instrument pour les évidences news/macro."""
    data = _as_dict(payload)
    return {
        'retained_news_count': int(data.get('retained_news_count') or 0),
        'retained_macro_count': int(data.get('retained_macro_count') or 0),
        'strongest_relevance': float(data.get('strongest_relevance') or 0.0),
        'average_relevance': float(data.get('average_relevance') or 0.0),
    }


@tool('sentiment_or_event_impact_parser')
def sentiment_or_event_impact_parser_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose les compteurs directionnels issus des indices de sentiment/impact."""
    data = _as_dict(payload)
    return {
        'bullish_hints': int(data.get('bullish_hints') or 0),
        'bearish_hints': int(data.get('bearish_hints') or 0),
        'neutral_hints': int(data.get('neutral_hints') or 0),
    }


@tool('market_snapshot')
def market_snapshot_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose le snapshot marché brut utilisé par les analystes techniques/contexte."""
    return _as_dict(payload)


@tool('indicator_bundle')
def indicator_bundle_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose le bundle d'indicateurs techniques déjà calculés."""
    return _as_dict(payload)


@tool('support_resistance_or_structure_detector')
def support_resistance_or_structure_detector_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose les conditions de validation/invalidation de structure."""
    data = _as_dict(payload)
    return {
        'validation': str(data.get('validation') or '').strip(),
        'invalidation': str(data.get('invalidation') or '').strip(),
    }


@tool('multi_timeframe_context')
def multi_timeframe_context_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose le contexte multi-timeframe disponible."""
    return _as_dict(payload)


@tool('market_regime_context')
def market_regime_context_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose le régime marché synthétisé pour le contexte."""
    return _as_dict(payload)


@tool('session_context')
def session_context_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose le contexte de session de marché."""
    return _as_dict(payload)


@tool('correlation_context')
def correlation_context_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose les corrélations contextuelles heuristiques."""
    return _as_dict(payload)


@tool('volatility_context')
def volatility_context_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose les métriques de volatilité contextuelle."""
    return _as_dict(payload)


@tool('evidence_query')
def evidence_query_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose les sorties agents utiles au débat haussier/baissier."""
    data = _as_dict(payload)
    outputs = _as_dict(data.get('analysis_outputs'))
    count = data.get('analysis_count')
    return {
        'analysis_outputs': outputs,
        'analysis_count': int(count) if isinstance(count, int) else len(outputs),
    }


@tool('thesis_support_extractor')
def thesis_support_extractor_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise les arguments de support/opposition d'une thèse."""
    data = _as_dict(payload)
    return {
        'supporting_arguments': _as_list(data.get('supporting_arguments')),
        'opposing_arguments': _as_list(data.get('opposing_arguments')),
    }


@tool('scenario_validation')
def scenario_validation_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise les conditions d'invalidation d'un scénario."""
    data = _as_dict(payload)
    return {
        'invalidation_conditions': _as_list(data.get('invalidation_conditions')),
    }


LANGCHAIN_AGENT_TOOLS: dict[str, BaseTool] = {
    'news_search': news_search_tool,
    'macro_calendar_or_event_feed': macro_calendar_or_event_feed_tool,
    'symbol_relevance_filter': symbol_relevance_filter_tool,
    'sentiment_or_event_impact_parser': sentiment_or_event_impact_parser_tool,
    'market_snapshot': market_snapshot_tool,
    'indicator_bundle': indicator_bundle_tool,
    'support_resistance_or_structure_detector': support_resistance_or_structure_detector_tool,
    'multi_timeframe_context': multi_timeframe_context_tool,
    'market_regime_context': market_regime_context_tool,
    'session_context': session_context_tool,
    'correlation_context': correlation_context_tool,
    'volatility_context': volatility_context_tool,
    'evidence_query': evidence_query_tool,
    'thesis_support_extractor': thesis_support_extractor_tool,
    'scenario_validation': scenario_validation_tool,
}


def get_langchain_agent_tool(tool_id: str) -> BaseTool | None:
    key = str(tool_id or '').strip()
    if not key:
        return None
    return LANGCHAIN_AGENT_TOOLS.get(key)


def build_llm_tool_specs(tool_ids: list[str]) -> list[dict[str, Any]]:
    """Build OpenAI-compatible function tool specs from registered LangChain tools."""
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_tool_id in tool_ids:
        tool_id = str(raw_tool_id or '').strip()
        if not tool_id or tool_id in seen:
            continue
        seen.add(tool_id)
        tool = get_langchain_agent_tool(tool_id)
        if tool is None:
            continue
        description = str(getattr(tool, 'description', '') or '').strip() or f'Runtime tool: {tool_id}'
        specs.append(
            {
                'type': 'function',
                'function': {
                    'name': tool_id,
                    'description': description,
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'payload': {
                                'type': 'object',
                                'description': 'Optional tool arguments for runtime execution.',
                            }
                        },
                        'additionalProperties': True,
                    },
                },
            }
        )
    return specs
