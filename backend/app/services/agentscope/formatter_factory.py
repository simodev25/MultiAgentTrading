"""Factory for building AgentScope formatters matching the LLM provider."""
from __future__ import annotations

from agentscope.formatter import (
    OllamaChatFormatter,
    OllamaMultiAgentFormatter,
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
)

from app.services.agentscope.model_factory import _is_local_ollama


def build_formatter(
    provider: str,
    multi_agent: bool = False,
    base_url: str = "",
) -> OllamaChatFormatter | OpenAIChatFormatter | OllamaMultiAgentFormatter | OpenAIMultiAgentFormatter:
    """Build a formatter matching the provider and conversation mode.

    For Ollama cloud (remote), use OpenAI formatters since we use
    OpenAIChatModel for remote Ollama instances.
    """
    use_ollama_native = provider == "ollama" and _is_local_ollama(base_url)

    if use_ollama_native:
        return OllamaMultiAgentFormatter() if multi_agent else OllamaChatFormatter()
    # OpenAI, Mistral, and remote Ollama all use OpenAI-compatible API
    return OpenAIMultiAgentFormatter() if multi_agent else OpenAIChatFormatter()
