"""Factory for building AgentScope ChatModel instances per LLM provider."""
from __future__ import annotations

from urllib.parse import urlparse

from agentscope.model import OpenAIChatModel, OllamaChatModel


def _ensure_v1(url: str) -> str:
    """Ensure URL ends with /v1 for OpenAI-compatible endpoint."""
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def _is_local_ollama(base_url: str) -> bool:
    """Check if the Ollama URL points to a local instance (localhost/127.0.0.1)."""
    hostname = urlparse(base_url).hostname or ""
    return hostname in ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")


def build_model(
    provider: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float = 0.0,
    stream: bool = False,
) -> OllamaChatModel | OpenAIChatModel:
    """Build an AgentScope model instance for the given provider.

    For Ollama:
    - Local instances (localhost) use OllamaChatModel (native SDK)
    - Remote/cloud instances use OpenAIChatModel (OpenAI-compatible /v1 endpoint)
    """
    if provider == "ollama":
        if _is_local_ollama(base_url):
            return OllamaChatModel(
                model_name=model_name,
                host=base_url.rstrip("/"),
                stream=stream,
                options={"temperature": temperature},
            )
        # Remote Ollama (cloud) — use OpenAI-compatible endpoint
        return OpenAIChatModel(
            model_name=model_name,
            api_key=api_key or "ollama",
            client_kwargs={"base_url": _ensure_v1(base_url)},
            stream=stream,
            generate_kwargs={"temperature": temperature},
        )
    if provider in ("openai", "mistral"):
        return OpenAIChatModel(
            model_name=model_name,
            api_key=api_key,
            client_kwargs={"base_url": base_url},
            stream=stream,
            generate_kwargs={"temperature": temperature},
        )
    raise ValueError(f"Unknown provider: {provider}")
