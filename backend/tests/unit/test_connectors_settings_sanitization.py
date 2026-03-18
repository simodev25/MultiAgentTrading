from app.api.routes.connectors import _sanitize_ollama_settings


def test_sanitize_ollama_settings_forces_deterministic_agents_off() -> None:
    source = {
        'provider': 'ollama',
        'agent_llm_enabled': {
            'risk-manager': True,
            'execution-manager': True,
            'news-analyst': True,
        },
    }

    result = _sanitize_ollama_settings(source)

    assert result['agent_llm_enabled']['risk-manager'] is False
    assert result['agent_llm_enabled']['execution-manager'] is False
    assert result['agent_llm_enabled']['news-analyst'] is True


def test_sanitize_ollama_settings_normalizes_agent_skills() -> None:
    source = {
        'provider': 'ollama',
        'agent_skills': {
            'news-analyst': 'Prioriser impact macro\nciter incertitude, prioriser impact macro',
            'trader-agent': ['Décision claire', 'Décision claire', 'Respecter SL/TP'],
            '': ['ignore'],
            'macro-analyst': 123,
        },
    }

    result = _sanitize_ollama_settings(source)
    assert result['agent_skills']['news-analyst'] == ['Prioriser impact macro', 'citer incertitude']
    assert result['agent_skills']['trader-agent'] == ['Décision claire', 'Respecter SL/TP']
    assert '' not in result['agent_skills']
    assert 'macro-analyst' not in result['agent_skills']
