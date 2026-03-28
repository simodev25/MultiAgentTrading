"""Factory for building AgentScope formatters matching the LLM provider."""
from __future__ import annotations

from agentscope.formatter import (
    OllamaChatFormatter,
    OllamaMultiAgentFormatter,
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
)


def build_formatter(
    provider: str,
    multi_agent: bool = False,
) -> OllamaChatFormatter | OpenAIChatFormatter | OllamaMultiAgentFormatter | OpenAIMultiAgentFormatter:
    """Build a formatter matching the provider and conversation mode."""
    if provider == "ollama":
        return OllamaMultiAgentFormatter() if multi_agent else OllamaChatFormatter()
    if provider in ("openai", "mistral"):
        return OpenAIMultiAgentFormatter() if multi_agent else OpenAIChatFormatter()
    raise ValueError(f"Unknown provider: {provider}")
