from __future__ import annotations

import json
import time
from weakref import WeakKeyDictionary

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.connector_config import ConnectorConfig

DEFAULT_AGENT_LLM_ENABLED: dict[str, bool] = {
    'technical-analyst': False,
    'news-analyst': True,
    'macro-analyst': False,
    'sentiment-agent': False,
    'bullish-researcher': True,
    'bearish-researcher': True,
    'trader-agent': False,
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
DEFAULT_DECISION_MODE = 'conservative'


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


class AgentModelSelector:
    """Resolve per-agent LLM model overrides from connector settings."""

    _cache_ttl_seconds = 5.0
    _settings_cache = WeakKeyDictionary()

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
        default_enabled = DEFAULT_AGENT_LLM_ENABLED.get(agent_name, False)
        settings = self._load_llm_settings(db)
        raw_enabled = settings.get('agent_llm_enabled', {})
        if isinstance(raw_enabled, dict):
            value = raw_enabled.get(agent_name)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {'1', 'true', 'yes', 'on'}:
                    return True
                if normalized in {'0', 'false', 'no', 'off'}:
                    return False
        return default_enabled

    def resolve(self, db: Session | None, agent_name: str | None = None) -> str:
        provider = self.resolve_provider(db)
        fallback = self._provider_default_model(provider)
        settings = self._load_llm_settings(db)
        if agent_name:
            raw_agent_models = settings.get('agent_models', {})
            if isinstance(raw_agent_models, dict):
                model = str(raw_agent_models.get(agent_name, '')).strip()
                if model:
                    return model

        default_model = str(settings.get('default_model', '')).strip()
        return default_model or fallback

    def resolve_skills(self, db: Session | None, agent_name: str) -> list[str]:
        settings = self._load_llm_settings(db)
        raw_map = settings.get('agent_skills', {})
        if not isinstance(raw_map, dict):
            return []

        raw_value = raw_map.get(agent_name)
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

    def resolve_decision_mode(self, db: Session | None) -> str:
        fallback = normalize_decision_mode(getattr(self.settings, 'decision_mode', DEFAULT_DECISION_MODE))
        settings = self._load_llm_settings(db)
        return normalize_decision_mode(settings.get('decision_mode'), fallback=fallback)
