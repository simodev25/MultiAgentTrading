import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes.connectors import (
    _sanitize_ollama_settings,
    _validate_decision_mode_value,
    list_connectors,
    update_connector,
)
from app.core.config import get_settings
from app.db.base import Base
from app.schemas.connector import ConnectorConfigUpdate


def test_sanitize_ollama_settings_preserves_enabled_flags() -> None:
    source = {
        'provider': 'ollama',
        'agent_llm_enabled': {
            'risk-manager': True,
            'execution-manager': True,
            'news-analyst': True,
        },
    }

    result = _sanitize_ollama_settings(source)

    assert result['agent_llm_enabled']['risk-manager'] is True
    assert result['agent_llm_enabled']['execution-manager'] is True
    assert result['agent_llm_enabled']['news-analyst'] is True


def test_sanitize_ollama_settings_normalizes_agent_skills() -> None:
    source = {
        'provider': 'ollama',
        'agent_skills': {
            'news-analyst': 'Prioriser impact macro\nciter incertitude\nprioriser impact macro',
            'trader-agent': ['Décision claire', 'Décision claire', 'Respecter SL/TP'],
            'risk-manager': "Valider le risque, sans casser la phrase.",
            '': ['ignore'],
            'macro-analyst': 123,
        },
    }

    result = _sanitize_ollama_settings(source)
    assert result['agent_skills']['news-analyst'] == ['Prioriser impact macro', 'citer incertitude']
    assert result['agent_skills']['trader-agent'] == ['Décision claire', 'Respecter SL/TP']
    assert result['agent_skills']['risk-manager'] == ["Valider le risque, sans casser la phrase."]
    assert '' not in result['agent_skills']
    assert 'macro-analyst' not in result['agent_skills']


def test_sanitize_ollama_settings_normalizes_decision_mode() -> None:
    source = {
        'provider': 'ollama',
        'decision_mode': 'BALANCED',
    }

    result = _sanitize_ollama_settings(source)
    assert result['decision_mode'] == 'balanced'

    fallback = _sanitize_ollama_settings({'provider': 'ollama', 'decision_mode': 'invalid-value'})
    assert fallback['decision_mode'] == 'conservative'


def test_sanitize_ollama_settings_normalizes_memory_context_flag() -> None:
    enabled = _sanitize_ollama_settings({'provider': 'ollama', 'memory_context_enabled': 'true'})
    assert enabled['memory_context_enabled'] is True

    disabled = _sanitize_ollama_settings({'provider': 'ollama', 'memory_context_enabled': 'off'})
    assert disabled['memory_context_enabled'] is False

    fallback = _sanitize_ollama_settings({'provider': 'ollama'})
    assert fallback['memory_context_enabled'] is False


def test_validate_decision_mode_value_rejects_invalid_values() -> None:
    _validate_decision_mode_value({'decision_mode': 'balanced'})
    with pytest.raises(HTTPException):
        _validate_decision_mode_value({'decision_mode': 'too-risky'})


def test_sanitize_invalid_decision_mode_uses_stable_default() -> None:
    settings = get_settings()
    previous = settings.decision_mode
    try:
        settings.decision_mode = 'balanced'
        result = _sanitize_ollama_settings({'provider': 'ollama', 'decision_mode': 'invalid-value'})
        assert result['decision_mode'] == 'conservative'
    finally:
        settings.decision_mode = previous


def test_update_connector_invalidates_runtime_settings_cache(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)
    called: dict[str, str | None] = {}

    def _capture_clear_cache(connector_name: str | None = None) -> None:
        called['connector_name'] = connector_name

    monkeypatch.setattr(
        'app.api.routes.connectors.RuntimeConnectorSettings.clear_cache',
        _capture_clear_cache,
    )

    with Session(engine) as db:
        payload = ConnectorConfigUpdate(enabled=True, settings={'provider': 'ollama'})
        update_connector('ollama', payload, db, _=None)

    assert called.get('connector_name') == 'ollama'


def test_update_connector_yfinance_invalidates_news_cache(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)
    called: dict[str, object] = {'runtime_cache': None, 'news_cache': 0}

    def _capture_clear_cache(connector_name: str | None = None) -> None:
        called['runtime_cache'] = connector_name

    class _FakeProvider:
        def clear_news_cache(self) -> int:
            called['news_cache'] = int(called.get('news_cache', 0) or 0) + 1
            return 1

    monkeypatch.setattr(
        'app.api.routes.connectors.RuntimeConnectorSettings.clear_cache',
        _capture_clear_cache,
    )
    monkeypatch.setattr(
        'app.api.routes.connectors.YFinanceMarketProvider',
        _FakeProvider,
    )

    with Session(engine) as db:
        payload = ConnectorConfigUpdate(enabled=True, settings={'NEWSAPI_API_KEY': 'k', 'news_providers': {'newsapi': {'enabled': True}}})
        update_connector('yfinance', payload, db, _=None)

    assert called.get('runtime_cache') == 'yfinance'
    assert called.get('news_cache') == 1


def test_list_connectors_injects_env_secret_defaults_when_missing() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    settings = get_settings()
    previous_values = {
        'ollama_api_key': settings.ollama_api_key,
        'openai_api_key': settings.openai_api_key,
        'mistral_api_key': settings.mistral_api_key,
        'metaapi_token': settings.metaapi_token,
        'metaapi_account_id': settings.metaapi_account_id,
        'newsapi_api_key': settings.newsapi_api_key,
        'tradingeconomics_api_key': settings.tradingeconomics_api_key,
        'finnhub_api_key': settings.finnhub_api_key,
        'alphavantage_api_key': settings.alphavantage_api_key,
    }
    try:
        settings.ollama_api_key = 'env-ollama'
        settings.openai_api_key = 'env-openai'
        settings.mistral_api_key = 'env-mistral'
        settings.metaapi_token = 'env-meta-token'
        settings.metaapi_account_id = 'env-meta-account'
        settings.newsapi_api_key = 'env-newsapi'
        settings.tradingeconomics_api_key = 'env-te'
        settings.finnhub_api_key = 'env-finnhub'
        settings.alphavantage_api_key = 'env-alpha'

        with Session(engine) as db:
            rows = list_connectors(db, _=None)

        by_name = {row.connector_name: row.settings for row in rows}
        assert by_name['ollama']['OLLAMA_API_KEY'] == 'env-ollama'
        assert by_name['ollama']['OPENAI_API_KEY'] == 'env-openai'
        assert by_name['ollama']['MISTRAL_API_KEY'] == 'env-mistral'
        assert by_name['metaapi']['METAAPI_TOKEN'] == 'env-meta-token'
        assert by_name['metaapi']['METAAPI_ACCOUNT_ID'] == 'env-meta-account'
        assert by_name['yfinance']['NEWSAPI_API_KEY'] == 'env-newsapi'
        assert by_name['yfinance']['TRADINGECONOMICS_API_KEY'] == 'env-te'
        assert by_name['yfinance']['FINNHUB_API_KEY'] == 'env-finnhub'
        assert by_name['yfinance']['ALPHAVANTAGE_API_KEY'] == 'env-alpha'
    finally:
        settings.ollama_api_key = previous_values['ollama_api_key']
        settings.openai_api_key = previous_values['openai_api_key']
        settings.mistral_api_key = previous_values['mistral_api_key']
        settings.metaapi_token = previous_values['metaapi_token']
        settings.metaapi_account_id = previous_values['metaapi_account_id']
        settings.newsapi_api_key = previous_values['newsapi_api_key']
        settings.tradingeconomics_api_key = previous_values['tradingeconomics_api_key']
        settings.finnhub_api_key = previous_values['finnhub_api_key']
        settings.alphavantage_api_key = previous_values['alphavantage_api_key']
