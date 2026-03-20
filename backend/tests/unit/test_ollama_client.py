import httpx

from app.services.llm.ollama_client import OllamaCloudClient


def _http_404_error(url: str) -> httpx.HTTPStatusError:
    request = httpx.Request('POST', url)
    response = httpx.Response(404, request=request)
    return httpx.HTTPStatusError("Client error '404 Not Found'", request=request, response=response)


def test_normalized_base_url_keeps_cloud_host() -> None:
    client = OllamaCloudClient()
    client.settings.ollama_base_url = 'https://ollama.com'
    assert client._normalized_base_url() == 'https://ollama.com'


def test_normalized_base_url_rewrites_legacy_api_host() -> None:
    client = OllamaCloudClient()
    client.settings.ollama_base_url = 'https://api.ollama.com'
    assert client._normalized_base_url() == 'https://ollama.com'


def test_chat_falls_back_to_default_model_on_404(monkeypatch) -> None:
    client = OllamaCloudClient()
    client.settings.ollama_base_url = 'https://ollama.com'
    client.settings.ollama_api_key = 'test-key'
    client.settings.ollama_model = 'gpt-oss:120b-cloud'

    monkeypatch.setattr(client, 'is_configured', lambda **_kwargs: True)
    monkeypatch.setattr(client, '_persist_log', lambda *args, **kwargs: None)

    def fake_call_remote(url: str, payload: dict, headers: dict, **_kwargs):
        if payload.get('model') == 'llama3.1':
            raise _http_404_error(url)
        return {
            'message': {'content': 'OK'},
            'prompt_eval_count': 12,
            'eval_count': 3,
        }

    monkeypatch.setattr(client, '_call_remote', fake_call_remote)

    result = client.chat('system', 'user', model='llama3.1')

    assert result['degraded'] is False
    assert result['text'] == 'OK'
    assert result['effective_model'] == 'gpt-oss:120b-cloud'
    assert result['model_fallback_from'] == 'llama3.1'


def test_chat_normalizes_base_url_once_per_request(monkeypatch) -> None:
    client = OllamaCloudClient()
    client.settings.ollama_base_url = 'https://ollama.com'
    client.settings.ollama_api_key = 'test-key'
    client.settings.ollama_model = 'llama3.1'

    monkeypatch.setattr(client, '_persist_log', lambda *args, **kwargs: None)

    call_count = 0
    original_normalizer = client._normalized_base_url

    def counted_normalizer() -> str:
        nonlocal call_count
        call_count += 1
        return original_normalizer()

    monkeypatch.setattr(client, '_normalized_base_url', counted_normalizer)
    monkeypatch.setattr(
        client,
        '_call_remote',
        lambda *_args, **_kwargs: {
            'message': {'content': 'OK'},
            'prompt_eval_count': 1,
            'eval_count': 1,
        },
    )

    result = client.chat('system', 'user')

    assert result['degraded'] is False
    assert call_count == 1


def test_build_chat_payload_applies_generation_options() -> None:
    client = OllamaCloudClient()
    payload = client._build_chat_payload(
        'llama3.1',
        'system',
        'user',
        max_tokens=64,
        temperature=0.2,
    )

    assert payload['model'] == 'llama3.1'
    assert payload['options']['num_predict'] == 64
    assert payload['options']['temperature'] == 0.2


def test_normalized_api_key_prefers_runtime_connector_settings(monkeypatch) -> None:
    client = OllamaCloudClient()
    client.settings.ollama_api_key = 'env-key'
    monkeypatch.setattr(
        'app.services.llm.ollama_client.RuntimeConnectorSettings.get_string',
        lambda *_args, **_kwargs: 'runtime-key',
    )
    assert client._normalized_api_key() == 'runtime-key'
