from app.services.llm.openai_compatible_client import OpenAICompatibleClient


def test_openai_client_prefers_runtime_connector_api_key(monkeypatch) -> None:
    client = OpenAICompatibleClient('openai')
    client.settings.openai_api_key = 'env-openai'

    monkeypatch.setattr(
        'app.services.llm.openai_compatible_client.RuntimeConnectorSettings.get_string',
        lambda _connector_name, keys, **_kwargs: 'runtime-openai' if 'OPENAI_API_KEY' in keys else '',
    )

    assert client._normalized_api_key() == 'runtime-openai'


def test_mistral_client_prefers_runtime_connector_api_key(monkeypatch) -> None:
    client = OpenAICompatibleClient('mistral')
    client.settings.mistral_api_key = 'env-mistral'

    monkeypatch.setattr(
        'app.services.llm.openai_compatible_client.RuntimeConnectorSettings.get_string',
        lambda _connector_name, keys, **_kwargs: 'runtime-mistral' if 'MISTRAL_API_KEY' in keys else '',
    )

    assert client._normalized_api_key() == 'runtime-mistral'
