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


def test_http_client_reuses_existing_client_for_same_timeout() -> None:
    first = OllamaCloudClient._get_http_client(30.0)
    second = OllamaCloudClient._get_http_client(45.0)
    third = OllamaCloudClient._get_http_client(30.0)

    assert first is not second
    assert third is first
    assert first.is_closed is False
    assert second.is_closed is False


def test_chat_falls_back_to_default_model_on_404(monkeypatch) -> None:
    client = OllamaCloudClient()
    client.settings.ollama_base_url = 'https://ollama.com'
    client.settings.ollama_api_key = 'test-key'
    client.settings.ollama_model = 'gpt-oss:120b-cloud'

    monkeypatch.setattr(client, 'is_configured', lambda **_kwargs: True)
    monkeypatch.setattr(client, '_persist_log', lambda *args, **kwargs: None)

    def fake_call_remote(url: str, payload: dict, headers: dict, **_kwargs):
        if payload.get('model') == 'deepseek-v3.2':
            raise _http_404_error(url)
        return {
            'message': {'content': 'OK'},
            'prompt_eval_count': 12,
            'eval_count': 3,
        }

    monkeypatch.setattr(client, '_call_remote', fake_call_remote)

    result = client.chat('system', 'user', model='deepseek-v3.2')

    assert result['degraded'] is False
    assert result['text'] == 'OK'
    assert result['effective_model'] == 'gpt-oss:120b-cloud'
    assert result['model_fallback_from'] == 'deepseek-v3.2'


def test_chat_normalizes_base_url_once_per_request(monkeypatch) -> None:
    client = OllamaCloudClient()
    client.settings.ollama_base_url = 'https://ollama.com'
    client.settings.ollama_api_key = 'test-key'
    client.settings.ollama_model = 'deepseek-v3.2'

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


def test_chat_retries_read_timeout_once_with_extended_timeout(monkeypatch) -> None:
    client = OllamaCloudClient()
    client.settings.ollama_base_url = 'https://ollama.com'
    client.settings.ollama_api_key = 'test-key'
    client.settings.ollama_model = 'minimax-m2.7'

    monkeypatch.setattr(client, 'is_configured', lambda **_kwargs: True)
    monkeypatch.setattr(client, '_persist_log', lambda *args, **kwargs: None)

    seen_timeouts: list[float | None] = []

    def fake_call_remote(url: str, payload: dict, headers: dict, **kwargs):
        seen_timeouts.append(kwargs.get('timeout_seconds'))
        if len(seen_timeouts) == 1:
            raise httpx.ReadTimeout('The read operation timed out')
        return {
            'message': {'content': 'OK'},
            'prompt_eval_count': 9,
            'eval_count': 4,
        }

    monkeypatch.setattr(client, '_call_remote', fake_call_remote)

    result = client.chat('system', 'user', request_timeout_seconds=45.0)

    assert result['degraded'] is False
    assert result['text'] == 'OK'
    assert seen_timeouts == [45.0, 90.0]


def test_build_chat_payload_applies_generation_options() -> None:
    client = OllamaCloudClient()
    payload = client._build_chat_payload(
        'deepseek-v3.2',
        'system',
        'user',
        max_tokens=64,
        temperature=0.2,
    )

    assert payload['model'] == 'deepseek-v3.2'
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


def test_normalize_messages_sanitizes_assistant_tool_calls_for_ollama() -> None:
    client = OllamaCloudClient()
    normalized = client._normalize_messages(
        'system',
        'user',
        messages=[
            {
                'role': 'assistant',
                'content': None,
                'tool_calls': [
                    {
                        'id': 'call_1',
                        'type': 'function',
                        'function': {
                            'name': 'get_price',
                            'arguments': '{"symbol":"EURUSD"',
                        },
                    }
                ],
            }
        ],
    )

    assert normalized[0]['role'] == 'assistant'
    assert normalized[0]['content'] == ''
    assert normalized[0]['tool_calls'][0]['function']['name'] == 'get_price'
    assert normalized[0]['tool_calls'][0]['function']['arguments'] == {}
