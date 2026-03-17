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
