"""Shared helpers extracted from OllamaCloudClient and OpenAICompatibleClient.

Consolidates identical logic that was previously duplicated across both
LLM provider implementations: message normalization, call-log persistence,
API-key validation, and tool-call argument parsing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.db.models.llm_call_log import LlmCallLog
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


def normalize_messages(
    system_prompt: str,
    user_prompt: str,
    messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Normalize a raw message list into a clean chat-API payload.

    Shared by both Ollama and OpenAI-compatible providers — logic is identical.
    """
    if isinstance(messages, list):
        normalized: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get('role') or '').strip().lower()
            if role not in {'system', 'user', 'assistant', 'tool'}:
                continue
            payload: dict[str, Any] = {'role': role}
            has_tool_calls = role == 'assistant' and isinstance(message.get('tool_calls'), list)
            if 'content' in message:
                content = message.get('content')
                # OpenAI rejects assistant messages with empty content + tool_calls;
                # normalise to None so the API receives a valid payload.
                if has_tool_calls and isinstance(content, str) and not content.strip():
                    content = None
                payload['content'] = content
            if has_tool_calls:
                payload['tool_calls'] = message.get('tool_calls')
            if role == 'tool':
                tool_call_id = message.get('tool_call_id')
                name = message.get('name')
                if isinstance(tool_call_id, str) and tool_call_id.strip():
                    payload['tool_call_id'] = tool_call_id.strip()
                if isinstance(name, str) and name.strip():
                    payload['name'] = name.strip()
            normalized.append(payload)
        if normalized:
            return normalized
    return [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt},
    ]


def persist_llm_call_log(
    *,
    provider: str,
    model: str,
    status: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    latency_ms: float,
    error: str | None = None,
) -> None:
    """Persist an LLM call record to the database.

    Shared by both Ollama and OpenAI-compatible providers — logic is identical.
    """
    db = None
    try:
        db = SessionLocal()
        db.add(
            LlmCallLog(
                provider=provider,
                model=model,
                status=status,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                error=error,
            )
        )
        db.commit()
    except Exception as exc:
        logger.warning("persist_llm_call_log failed: %s", exc)
        if db is not None:
            db.rollback()
    finally:
        if db is not None:
            db.close()


def is_api_key_valid(key: str | None) -> bool:
    """Check if an API key looks valid (not a placeholder)."""
    if not key or len(key.strip()) < 8:
        return False
    return key.lower() not in {'replace_me', 'changeme', 'change-me', 'your_api_key'}


def safe_parse_tool_arguments(raw_args: Any) -> dict[str, Any]:
    """Parse tool call arguments from a raw value (str or dict).

    Shared by both Ollama and OpenAI-compatible providers.
    """
    if isinstance(raw_args, dict):
        return dict(raw_args)
    if isinstance(raw_args, str):
        cleaned = raw_args.strip()
        if not cleaned:
            return {}
        try:
            parsed = json.loads(cleaned)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}
