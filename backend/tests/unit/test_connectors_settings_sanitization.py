import pytest
from fastapi import HTTPException

from app.api.routes.connectors import _sanitize_ollama_settings, _validate_decision_mode_value


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


def test_validate_decision_mode_value_rejects_invalid_values() -> None:
    _validate_decision_mode_value({'decision_mode': 'balanced'})
    with pytest.raises(HTTPException):
        _validate_decision_mode_value({'decision_mode': 'too-risky'})
