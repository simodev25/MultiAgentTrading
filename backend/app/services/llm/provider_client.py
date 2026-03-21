from __future__ import annotations

import inspect
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.services.llm.model_selector import AgentModelSelector, normalize_llm_provider
from app.services.llm.ollama_client import OllamaCloudClient
from app.services.llm.openai_compatible_client import OpenAICompatibleClient


class LlmClient:
    def __init__(self) -> None:
        self.model_selector = AgentModelSelector()
        self.ollama = OllamaCloudClient()
        self.openai = OpenAICompatibleClient('openai')
        self.mistral = OpenAICompatibleClient('mistral')

    def _resolve_provider(self, db: Session | None) -> str:
        return normalize_llm_provider(self.model_selector.resolve_provider(db), fallback='ollama')

    def _provider_client(self, provider: str) -> Any:
        if provider == 'openai':
            return self.openai
        if provider == 'mistral':
            return self.mistral
        return self.ollama

    @staticmethod
    def _invoke_client_method(method: Any, *args: Any, db: Session | None = None, **kwargs: Any) -> Any:
        if db is None:
            return method(*args, **kwargs)

        accepts_kwargs = False
        accepts_db = False
        try:
            signature = inspect.signature(method)
            for parameter in signature.parameters.values():
                if parameter.kind is inspect.Parameter.VAR_KEYWORD:
                    accepts_kwargs = True
                if parameter.name == 'db':
                    accepts_db = True
        except (TypeError, ValueError):
            accepts_kwargs = True

        if accepts_db or accepts_kwargs:
            return method(*args, db=db, **kwargs)
        return method(*args, **kwargs)

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        db: Session | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        provider = self._resolve_provider(db)
        client = self._provider_client(provider)
        return self._invoke_client_method(
            client.chat,
            system_prompt,
            user_prompt,
            model=model,
            db=db,
            **kwargs,
        )

    @staticmethod
    def _extract_json_payload(text: object) -> tuple[Any | None, str | None]:
        raw = str(text or '').strip()
        if not raw:
            return None, 'Empty LLM response.'

        candidates: list[str] = [raw]
        fenced_blocks = re.findall(r'```(?:json)?\s*(.*?)```', raw, flags=re.IGNORECASE | re.DOTALL)
        for block in fenced_blocks:
            normalized = str(block or '').strip()
            if normalized:
                candidates.append(normalized)

        first_object = raw.find('{')
        last_object = raw.rfind('}')
        if first_object >= 0 and last_object > first_object:
            candidates.append(raw[first_object : last_object + 1].strip())

        decoder = json.JSONDecoder()
        seen: set[str] = set()
        for candidate in candidates:
            normalized = str(candidate or '').strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            try:
                parsed, end_index = decoder.raw_decode(normalized)
                if normalized[end_index:].strip():
                    continue
                return parsed, None
            except json.JSONDecodeError:
                continue

        return None, 'Unable to parse JSON payload from LLM response.'

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        db: Session | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raw = self.chat(
            system_prompt,
            user_prompt,
            model=model,
            db=db,
            **kwargs,
        )
        parsed, parse_error = self._extract_json_payload(raw.get('text') if isinstance(raw, dict) else None)
        if not isinstance(raw, dict):
            return {
                'provider': 'fallback',
                'text': '',
                'json': parsed,
                'json_error': parse_error or 'Invalid LLM response payload.',
                'degraded': True,
            }
        return {
            **raw,
            'json': parsed,
            'json_error': parse_error,
        }

    def list_models(self, db: Session | None = None) -> dict[str, Any]:
        provider = self._resolve_provider(db)
        client = self._provider_client(provider)
        payload = self._invoke_client_method(client.list_models, db=db)
        if not isinstance(payload, dict):
            return {'provider': provider, 'models': [], 'source': None, 'error': 'Invalid provider response'}
        if 'provider' not in payload:
            payload = {**payload, 'provider': provider}
        return payload
