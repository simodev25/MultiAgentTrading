from __future__ import annotations

import json
import threading
import time
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.connector_config import ConnectorConfig

DEFAULT_AGENT_LLM_ENABLED: dict[str, bool] = {
    'technical-analyst': False,
    'news-analyst': True,
    'market-context-analyst': False,
    'bullish-researcher': True,
    'bearish-researcher': True,
    'trader-agent': False,
    'agentic-runtime-planner': True,
    'risk-manager': False,
    'execution-manager': False,
    'schedule-planner-agent': True,
    'order-guardian': False,
}

SUPPORTED_LLM_PROVIDERS = {'ollama', 'openai', 'mistral'}
DETERMINISTIC_ONLY_AGENTS: set[str] = set()
MAX_AGENT_SKILLS_PER_AGENT = 12
MAX_AGENT_SKILL_LENGTH = 500
SUPPORTED_DECISION_MODES = {'conservative', 'balanced', 'permissive'}
DEFAULT_DECISION_MODE = 'balanced'
DEFAULT_MEMORY_CONTEXT_ENABLED = False
LEGACY_AGENT_ALIASES: dict[str, str] = {
    'macro-analyst': 'market-context-analyst',
    'sentiment-agent': 'market-context-analyst',
}
AGENT_TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    'news_search': {
        'label': 'News Search',
        'description': "Normalise, déduplique et score un lot de news par pertinence symbole via MCP.",
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.news_search',
    },
    'macro_calendar_or_event_feed': {
        'label': 'Macro Event Feed',
        'description': "Filtre et score les événements macro-économiques par impact via MCP.",
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.macro_event_feed',
    },
    'symbol_relevance_filter': {
        'label': 'Symbol Relevance Filter',
        'description': "Filtre news et macro par seuil de pertinence pour un symbole donné via MCP.",
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.symbol_relevance_filter',
    },
    'sentiment_or_event_impact_parser': {
        'label': 'Sentiment Impact Parser',
        'description': "Parse le sentiment directionnel depuis les headlines avec dictionnaires par classe d'actif via MCP.",
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.sentiment_parser',
    },
    'market_snapshot': {
        'label': 'Market Snapshot',
        'description': 'Snapshot marché normalisé avec métriques dérivées (spread ratio, candle ratios) via MCP.',
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.market_snapshot',
    },
    'indicator_bundle': {
        'label': 'Indicator Bundle',
        'description': 'Calcul réel RSI, EMA, MACD, ATR depuis données OHLC — pas de passthrough — via MCP.',
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.indicator_bundle',
    },
    'divergence_detector': {
        'label': 'Divergence Detector',
        'description': 'Détection divergences RSI-prix haussières/baissières sur N barres via MCP.',
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.divergence_detector',
    },
    'support_resistance_or_structure_detector': {
        'label': 'Support/Resistance Detector',
        'description': 'Identification niveaux S/R par clustering de pivots avec comptage de touches via MCP.',
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.support_resistance_detector',
    },
    'pattern_detector': {
        'label': 'Pattern Detector',
        'description': 'Détection patterns chandeliers : doji, hammer, engulfing, pin bar, shooting star via MCP.',
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.pattern_detector',
    },
    'multi_timeframe_context': {
        'label': 'Multi Timeframe Context',
        'description': "Synthèse alignement multi-TF avec score de confluence et direction dominante via MCP.",
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.multi_timeframe_context',
    },
    'market_regime_context': {
        'label': 'Market Regime Detector',
        'description': 'Classification régime marché (trending/ranging/volatile/calm) par slope + ATR via MCP.',
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.market_regime_detector',
    },
    'session_context': {
        'label': 'Session Context',
        'description': 'Sessions marché actives, overlaps et conditions de liquidité en temps réel via MCP.',
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.session_context',
    },
    'correlation_context': {
        'label': 'Correlation Analyzer',
        'description': "Corrélation Pearson rolling entre deux séries de prix avec analyse lead/lag via MCP.",
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.correlation_analyzer',
    },
    'volatility_context': {
        'label': 'Volatility Analyzer',
        'description': 'ATR, volatilité historique, Bollinger bandwidth, percentile de volatilité via MCP.',
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.volatility_analyzer',
    },
    'evidence_query': {
        'label': 'Evidence Query',
        'description': 'Agrégation et scoring des évidences agents avec consensus directionnel via MCP.',
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.evidence_query',
    },
    'thesis_support_extractor': {
        'label': 'Thesis Support Extractor',
        'description': 'Normalisation et pondération arguments de thèse pour agents de débat via MCP.',
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.thesis_support_extractor',
    },
    'scenario_validation': {
        'label': 'Scenario Validation',
        'description': "Validation scénario trading avec géométrie SL/TP et ratio risk/reward via MCP.",
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.scenario_validation',
    },
    'position_size_calculator': {
        'label': 'Position Size Calculator',
        'description': "Calcul taille position adapté par classe d'actif avec vérification marge via MCP.",
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.position_size_calculator',
    },
    'memory_query': {
        'label': 'Memory Query',
        'description': "Accès mémoire agentique : recherche, feedback, statistiques par agent via MCP.",
        'enabled_by_default': True,
        'reference_origin': 'mcp_trading_server.memory_query',
    },
}
DEFAULT_AGENT_ALLOWED_TOOLS: dict[str, tuple[str, ...]] = {
    'technical-analyst': (
        'market_snapshot',
        'indicator_bundle',
        'divergence_detector',
        'support_resistance_or_structure_detector',
        'pattern_detector',
        'multi_timeframe_context',
    ),
    'news-analyst': (
        'news_search',
        'macro_calendar_or_event_feed',
        'symbol_relevance_filter',
        'sentiment_or_event_impact_parser',
    ),
    'market-context-analyst': (
        'market_regime_context',
        'session_context',
        'correlation_context',
        'volatility_context',
    ),
    'bullish-researcher': (
        'evidence_query',
        'thesis_support_extractor',
        'scenario_validation',
        'memory_query',
    ),
    'bearish-researcher': (
        'evidence_query',
        'thesis_support_extractor',
        'scenario_validation',
        'memory_query',
    ),
    'trader-agent': (
        'evidence_query',
        'scenario_validation',
        'position_size_calculator',
        'memory_query',
    ),
    'risk-manager': (
        'scenario_validation',
        'position_size_calculator',
    ),
    'execution-manager': (
        'scenario_validation',
        'position_size_calculator',
    ),
    'schedule-planner-agent': (),
    'order-guardian': (
        'memory_query',
    ),
    'agentic-runtime-planner': (),
}


def normalize_agent_name(agent_name: str | None) -> str:
    normalized = str(agent_name or '').strip()
    if not normalized:
        return ''
    return LEGACY_AGENT_ALIASES.get(normalized, normalized)


def _legacy_agent_aliases_for(agent_name: str) -> tuple[str, ...]:
    if agent_name != 'market-context-analyst':
        return ()
    return ('macro-analyst', 'sentiment-agent')


def normalize_llm_provider(value: str | None, fallback: str = 'ollama') -> str:
    normalized = str(value or '').strip().lower()
    if normalized in SUPPORTED_LLM_PROVIDERS:
        return normalized
    return fallback if fallback in SUPPORTED_LLM_PROVIDERS else 'ollama'


def normalize_decision_mode(value: object, fallback: str = DEFAULT_DECISION_MODE) -> str:
    normalized = str(value or '').strip().lower()
    if normalized in SUPPORTED_DECISION_MODES:
        return normalized
    return fallback if fallback in SUPPORTED_DECISION_MODES else DEFAULT_DECISION_MODE


def _normalize_bool_setting(value: object, *, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'1', 'true', 'yes', 'on'}:
            return True
        if normalized in {'0', 'false', 'no', 'off'}:
            return False
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    return fallback


def _default_agent_tools_map() -> dict[str, dict[str, bool]]:
    return {
        agent_name: {
            tool_id: bool(AGENT_TOOL_DEFINITIONS.get(tool_id, {}).get('enabled_by_default', True))
            for tool_id in allowed_tools
        }
        for agent_name, allowed_tools in DEFAULT_AGENT_ALLOWED_TOOLS.items()
    }


def _extract_tool_enabled_value(value: object, *, fallback: bool) -> bool:
    if isinstance(value, dict):
        for key in ('enabled_current', 'enabled', 'active', 'value'):
            if key in value:
                return _normalize_bool_setting(value.get(key), fallback=fallback)
    return _normalize_bool_setting(value, fallback=fallback)


def _normalize_tools_for_agent(raw_value: object, *, allowed_tools: tuple[str, ...]) -> dict[str, bool]:
    defaults = {
        tool_id: bool(AGENT_TOOL_DEFINITIONS.get(tool_id, {}).get('enabled_by_default', True))
        for tool_id in allowed_tools
    }
    if not isinstance(raw_value, dict):
        return defaults

    normalized = dict(defaults)
    for raw_tool_id, raw_tool_payload in raw_value.items():
        tool_id = str(raw_tool_id or '').strip()
        if not tool_id or tool_id not in defaults:
            continue
        normalized[tool_id] = _extract_tool_enabled_value(raw_tool_payload, fallback=defaults[tool_id])
    return normalized


def normalize_agent_tools_settings(raw_agent_tools: object) -> dict[str, dict[str, bool]]:
    normalized = _default_agent_tools_map()
    if not isinstance(raw_agent_tools, dict):
        return normalized

    for raw_agent_name, raw_agent_tools_map in raw_agent_tools.items():
        agent_name = normalize_agent_name(str(raw_agent_name or '').strip())
        if not agent_name:
            continue
        allowed_tools = DEFAULT_AGENT_ALLOWED_TOOLS.get(agent_name)
        if allowed_tools is None:
            continue
        normalized[agent_name] = _normalize_tools_for_agent(
            raw_agent_tools_map,
            allowed_tools=allowed_tools,
        )
    return normalized


def build_agent_tools_catalog(agent_tools: object | None = None) -> dict[str, list[dict[str, Any]]]:
    resolved_tools = normalize_agent_tools_settings(agent_tools)
    catalog: dict[str, list[dict[str, Any]]] = {}
    for agent_name, allowed_tools in DEFAULT_AGENT_ALLOWED_TOOLS.items():
        current_tools = resolved_tools.get(agent_name, {})
        rows: list[dict[str, Any]] = []
        for tool_id in allowed_tools:
            meta = AGENT_TOOL_DEFINITIONS.get(tool_id, {})
            enabled_by_default = bool(meta.get('enabled_by_default', True))
            rows.append(
                {
                    'tool_id': tool_id,
                    'label': str(meta.get('label') or tool_id),
                    'description': str(meta.get('description') or ''),
                    'enabled_by_default': enabled_by_default,
                    'enabled_current': bool(current_tools.get(tool_id, enabled_by_default)),
                }
            )
        catalog[agent_name] = rows
    return catalog


def validate_agent_tools_payload(raw_agent_tools: object) -> list[str]:
    if raw_agent_tools is None:
        return []
    if not isinstance(raw_agent_tools, dict):
        return ['agent_tools must be an object mapping agent_name -> tool states.']

    issues: list[str] = []
    for raw_agent_name, raw_agent_tools_map in raw_agent_tools.items():
        agent_name = normalize_agent_name(str(raw_agent_name or '').strip())
        if not agent_name:
            continue
        allowed_tools = DEFAULT_AGENT_ALLOWED_TOOLS.get(agent_name)
        if allowed_tools is None:
            issues.append(f"Unknown agent '{raw_agent_name}' in agent_tools.")
            continue
        if not isinstance(raw_agent_tools_map, dict):
            issues.append(f"agent_tools['{raw_agent_name}'] must be an object.")
            continue
        for raw_tool_id, raw_tool_payload in raw_agent_tools_map.items():
            tool_id = str(raw_tool_id or '').strip()
            if not tool_id:
                continue
            if tool_id in allowed_tools:
                continue
            if _extract_tool_enabled_value(raw_tool_payload, fallback=False):
                issues.append(
                    f"Tool '{tool_id}' is not allowed for agent '{agent_name}'."
                )
    return issues


class AgentModelSelector:
    """Resolve per-agent LLM model overrides from connector settings."""

    _cache_ttl_seconds = 5.0
    _settings_cache = WeakKeyDictionary()
    _cache_lock = threading.Lock()

    def __init__(self) -> None:
        self.settings = get_settings()

    @classmethod
    def clear_cache(cls) -> None:
        cls._settings_cache = WeakKeyDictionary()

    @classmethod
    def _load_llm_settings(cls, db: Session | None) -> dict:
        if db is None:
            return {}

        now = time.monotonic()
        with cls._cache_lock:
            cached = cls._settings_cache.get(db)
            if cached and now - cached[0] <= cls._cache_ttl_seconds:
                return cached[1]

            connector = (
                db.query(ConnectorConfig)
                .filter(ConnectorConfig.connector_name == 'ollama')
                .first()
            )
            settings = connector.settings if connector is not None and isinstance(connector.settings, dict) else {}
            cls._settings_cache[db] = (now, settings)

            if len(cls._settings_cache) > 128:
                fresh_cache = WeakKeyDictionary()
                for cache_key, cache_value in cls._settings_cache.items():
                    if now - cache_value[0] <= cls._cache_ttl_seconds:
                        fresh_cache[cache_key] = cache_value
                cls._settings_cache = fresh_cache
            return settings

    @classmethod
    def _load_ollama_settings(cls, db: Session | None) -> dict:
        # Backward-compatible alias kept for historical callsites/tests.
        return cls._load_llm_settings(db)

    def resolve_provider(self, db: Session | None) -> str:
        default_provider = normalize_llm_provider(self.settings.llm_provider, fallback='ollama')
        settings = self._load_llm_settings(db)
        raw_provider = settings.get('provider')
        if isinstance(raw_provider, str):
            return normalize_llm_provider(raw_provider, fallback=default_provider)
        return default_provider

    def _provider_default_model(self, provider: str) -> str:
        normalized_provider = normalize_llm_provider(provider, fallback='ollama')
        if normalized_provider == 'openai':
            return str(self.settings.openai_model or '').strip() or 'gpt-4o-mini'
        if normalized_provider == 'mistral':
            return str(self.settings.mistral_model or '').strip() or 'mistral-small-latest'
        return str(self.settings.ollama_model or '').strip() or 'llama3.1'

    def is_enabled(self, db: Session | None, agent_name: str) -> bool:
        normalized_agent_name = normalize_agent_name(agent_name)
        default_enabled = DEFAULT_AGENT_LLM_ENABLED.get(normalized_agent_name, False)
        settings = self._load_llm_settings(db)
        raw_enabled = settings.get('agent_llm_enabled', {})
        if isinstance(raw_enabled, dict):
            candidate_names = (normalized_agent_name, *_legacy_agent_aliases_for(normalized_agent_name))
            for candidate_name in candidate_names:
                value = raw_enabled.get(candidate_name)
                if value is not None:
                    return _normalize_bool_setting(value, fallback=default_enabled)
        return default_enabled

    def resolve(self, db: Session | None, agent_name: str | None = None) -> str:
        provider = self.resolve_provider(db)
        fallback = self._provider_default_model(provider)
        settings = self._load_llm_settings(db)
        if agent_name:
            normalized_agent_name = normalize_agent_name(agent_name)
            raw_agent_models = settings.get('agent_models', {})
            if isinstance(raw_agent_models, dict):
                candidate_names = (normalized_agent_name, *_legacy_agent_aliases_for(normalized_agent_name))
                for candidate_name in candidate_names:
                    model = str(raw_agent_models.get(candidate_name, '')).strip()
                    if model:
                        return model

        default_model = str(settings.get('default_model', '')).strip()
        return default_model or fallback

    def resolve_skills(self, db: Session | None, agent_name: str) -> list[str]:
        normalized_agent_name = normalize_agent_name(agent_name)
        settings = self._load_llm_settings(db)
        raw_map = settings.get('agent_skills', {})
        if not isinstance(raw_map, dict):
            return []

        raw_value = raw_map.get(normalized_agent_name)
        if raw_value is None:
            for candidate_name in _legacy_agent_aliases_for(normalized_agent_name):
                if candidate_name in raw_map:
                    raw_value = raw_map.get(candidate_name)
                    break
        raw_items: list[str]
        if isinstance(raw_value, str):
            text = raw_value.strip()
            if not text:
                return []
            if text.startswith('['):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        raw_items = [str(item).strip() for item in parsed]
                    else:
                        raw_items = [text]
                except json.JSONDecodeError:
                    raw_items = [part.strip() for part in text.splitlines()]
            elif '\n' in text:
                raw_items = [part.strip() for part in text.splitlines()]
            elif '||' in text:
                raw_items = [part.strip() for part in text.split('||')]
            elif ';' in text:
                raw_items = [part.strip() for part in text.split(';')]
            else:
                raw_items = [text]
        elif isinstance(raw_value, (list, tuple, set)):
            raw_items = [str(item).strip() for item in raw_value]
        else:
            return []

        deduped: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            cleaned = item.strip()
            if not cleaned:
                continue
            if len(cleaned) > MAX_AGENT_SKILL_LENGTH:
                cleaned = cleaned[:MAX_AGENT_SKILL_LENGTH].rstrip()
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(cleaned)
            if len(deduped) >= MAX_AGENT_SKILLS_PER_AGENT:
                break

        return deduped

    def resolve_enabled_tools(self, db: Session | None, agent_name: str) -> list[str]:
        normalized_agent_name = normalize_agent_name(agent_name)
        allowed_tools = DEFAULT_AGENT_ALLOWED_TOOLS.get(normalized_agent_name, ())
        if not allowed_tools:
            return []

        settings = self._load_llm_settings(db)
        resolved = normalize_agent_tools_settings(settings.get('agent_tools'))
        agent_tool_state = resolved.get(normalized_agent_name, {})
        enabled_tools: list[str] = []
        for tool_id in allowed_tools:
            default_enabled = bool(AGENT_TOOL_DEFINITIONS.get(tool_id, {}).get('enabled_by_default', True))
            if bool(agent_tool_state.get(tool_id, default_enabled)):
                enabled_tools.append(tool_id)
        return enabled_tools

    def resolve_decision_mode(self, db: Session | None) -> str:
        fallback = normalize_decision_mode(getattr(self.settings, 'decision_mode', DEFAULT_DECISION_MODE))
        settings = self._load_llm_settings(db)
        return normalize_decision_mode(settings.get('decision_mode'), fallback=fallback)

    def resolve_memory_context_enabled(self, db: Session | None) -> bool:
        settings = self._load_llm_settings(db)
        return _normalize_bool_setting(
            settings.get('memory_context_enabled'),
            fallback=DEFAULT_MEMORY_CONTEXT_ENABLED,
        )
