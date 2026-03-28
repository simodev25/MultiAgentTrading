"""Factory for building AgentScope ChatModel instances per LLM provider."""
from __future__ import annotations

from agentscope.model import OpenAIChatModel, OllamaChatModel


def _ensure_v1(url: str) -> str:
    """Ensure Ollama URL ends with /v1 for OpenAI-compatible endpoint."""
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def build_model(
    provider: str,
    model_name: str,
    base_url: str,
    api_key: str,
    temperature: float = 0.0,
    stream: bool = False,
) -> OllamaChatModel | OpenAIChatModel:
    """Build an AgentScope model instance for the given provider."""
    if provider == "ollama":
        return OllamaChatModel(
            model_name=model_name,
            api_key=api_key or None,
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
