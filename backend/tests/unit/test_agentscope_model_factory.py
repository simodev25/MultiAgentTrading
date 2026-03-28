from unittest.mock import patch, MagicMock
import pytest
from app.services.agentscope.model_factory import build_model, _ensure_v1


@patch("app.services.agentscope.model_factory.OllamaChatModel")
def test_build_ollama_local_model(mock_cls):
    mock_cls.return_value = MagicMock()
    build_model(provider="ollama", model_name="llama3.1", base_url="http://localhost:11434", api_key="")
    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert call_kwargs["model_name"] == "llama3.1"
    assert call_kwargs["host"] == "http://localhost:11434"
    assert call_kwargs["stream"] is False
    assert call_kwargs["options"]["temperature"] == 0.0


@patch("app.services.agentscope.model_factory.OpenAIChatModel")
def test_build_ollama_cloud_model(mock_cls):
    mock_cls.return_value = MagicMock()
    build_model(provider="ollama", model_name="gpt-oss:120b", base_url="https://ollama.com", api_key="key123")
    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert call_kwargs["model_name"] == "gpt-oss:120b"
    assert call_kwargs["client_kwargs"]["base_url"].endswith("/v1")
    assert call_kwargs["api_key"] == "key123"


@patch("app.services.agentscope.model_factory.OpenAIChatModel")
def test_build_openai_model(mock_cls):
    mock_cls.return_value = MagicMock()
    build_model(provider="openai", model_name="gpt-4o-mini", base_url="https://api.openai.com/v1", api_key="sk-test")
    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert call_kwargs["model_name"] == "gpt-4o-mini"
    assert call_kwargs["api_key"] == "sk-test"


@patch("app.services.agentscope.model_factory.OpenAIChatModel")
def test_build_mistral_uses_openai_class(mock_cls):
    mock_cls.return_value = MagicMock()
    build_model(provider="mistral", model_name="mistral-small-latest", base_url="https://api.mistral.ai/v1", api_key="key")
    mock_cls.assert_called_once()


def test_build_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        build_model(provider="unknown", model_name="x", base_url="http://x", api_key="")


def test_ollama_url_gets_v1_suffix():
    assert _ensure_v1("http://localhost:11434").endswith("/v1")
    assert _ensure_v1("http://localhost:11434/v1").endswith("/v1")
    assert not _ensure_v1("http://localhost:11434/v1").endswith("/v1/v1")


from app.services.agentscope.formatter_factory import build_formatter


def test_ollama_chat_formatter():
    f = build_formatter("ollama", multi_agent=False)
    assert f.__class__.__name__ == "OllamaChatFormatter"


def test_ollama_multi_agent_formatter():
    f = build_formatter("ollama", multi_agent=True)
    assert f.__class__.__name__ == "OllamaMultiAgentFormatter"


def test_openai_chat_formatter():
    f = build_formatter("openai", multi_agent=False)
    assert f.__class__.__name__ == "OpenAIChatFormatter"


def test_mistral_uses_openai_formatter():
    f = build_formatter("mistral", multi_agent=True)
    assert f.__class__.__name__ == "OpenAIMultiAgentFormatter"


def test_formatter_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        build_formatter("unknown")
