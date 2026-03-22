from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.db.models.llm_call_log import LlmCallLog
from app.db.session import SessionLocal
from app.observability.metrics import (
    external_provider_failures_total,
    llm_calls_total,
    llm_completion_tokens_total,
    llm_cost_usd_total,
    llm_latency_seconds,
    llm_prompt_tokens_total,
)
from app.services.connectors.runtime_settings import RuntimeConnectorSettings

logger = logging.getLogger(__name__)


def _is_retryable_http_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return False


class OpenAICompatibleClient:
    _shared_clients: dict[float, httpx.Client] = {}
    _shared_clients_lock = threading.Lock()

    def __init__(self, provider: str) -> None:
        self.settings = get_settings()
        self.provider = str(provider or '').strip().lower() or 'openai'

    @classmethod
    def _get_http_client(cls, timeout_seconds: float) -> httpx.Client:
        safe_timeout = max(float(timeout_seconds), 1.0)
        with cls._shared_clients_lock:
            client = cls._shared_clients.get(safe_timeout)
            if client is not None and not client.is_closed:
                return client
            cls._shared_clients[safe_timeout] = httpx.Client(
                timeout=safe_timeout,
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
            return cls._shared_clients[safe_timeout]

    @property
    def _display_name(self) -> str:
        if self.provider == 'mistral':
            return 'Mistral'
        return 'OpenAI'

    def _normalized_api_key(self, db: Session | None = None) -> str:
        del db  # Runtime connector settings are resolved without DB session injection.
        if self.provider == 'mistral':
            runtime_key = RuntimeConnectorSettings.get_string(
                'ollama',
                ('MISTRAL_API_KEY', 'mistral_api_key'),
            )
            key = (runtime_key or self.settings.mistral_api_key or '').strip()
        else:
            runtime_key = RuntimeConnectorSettings.get_string(
                'ollama',
                ('OPENAI_API_KEY', 'openai_api_key'),
            )
            key = (runtime_key or self.settings.openai_api_key or '').strip()
        if len(key) >= 2 and key[0] == key[-1] and key[0] in {'"', "'"}:
            key = key[1:-1].strip()
        return key

    def _normalized_base_url(self) -> str:
        raw_base_url = self.settings.mistral_base_url if self.provider == 'mistral' else self.settings.openai_base_url
        base_url = str(raw_base_url or '').strip().rstrip('/')
        if not base_url:
            return base_url
        if not base_url.startswith(('http://', 'https://')):
            base_url = f'https://{base_url.lstrip("/")}'
        return base_url

    def _default_model(self) -> str:
        if self.provider == 'mistral':
            return str(self.settings.mistral_model or '').strip() or 'mistral-small-latest'
        return str(self.settings.openai_model or '').strip() or 'gpt-4o-mini'

    def _timeout_seconds(self) -> float:
        if self.provider == 'mistral':
            return float(self.settings.mistral_timeout_seconds)
        return float(self.settings.openai_timeout_seconds)

    def is_configured(self, base_url: str | None = None, *, db: Session | None = None) -> bool:
        key = self._normalized_api_key(db=db)
        if not key:
            return False
        if key.lower() in {'replace_me', 'changeme', 'change-me', 'your_api_key'}:
            return False
        if base_url is None:
            base_url = self._normalized_base_url()
        return bool(base_url)

    def _estimate_cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        if self.provider == 'mistral':
            input_rate = float(self.settings.mistral_input_cost_per_1m_tokens)
            output_rate = float(self.settings.mistral_output_cost_per_1m_tokens)
        else:
            input_rate = float(self.settings.openai_input_cost_per_1m_tokens)
            output_rate = float(self.settings.openai_output_cost_per_1m_tokens)
        input_cost = (prompt_tokens / 1_000_000) * input_rate
        output_cost = (completion_tokens / 1_000_000) * output_rate
        return float(input_cost + output_cost)

    def _persist_log(
        self,
        provider: str,
        model: str,
        status: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_ms: float,
        error: str | None = None,
    ) -> None:
        db = SessionLocal()
        try:
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
        except Exception:
            db.rollback()
        finally:
            db.close()

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        retry=retry_if_exception(_is_retryable_http_error),
        reraise=True,
    )
    def _request_json(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._get_http_client(self._timeout_seconds())
        response: httpx.Response
        if method.upper() == 'GET':
            response = client.get(url, headers=headers)
        else:
            response = client.post(url, json=payload or {}, headers=headers)
        response.raise_for_status()
        return response.json() if response.content else {}

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        choices = data.get('choices')
        if isinstance(choices, list) and choices:
            first_choice = choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get('message')
                if isinstance(message, dict):
                    content = message.get('content', '')
                    if isinstance(content, str):
                        return content
                    if isinstance(content, list):
                        parts: list[str] = []
                        for item in content:
                            if isinstance(item, dict):
                                text_part = item.get('text')
                                if isinstance(text_part, str) and text_part:
                                    parts.append(text_part)
                            elif isinstance(item, str) and item:
                                parts.append(item)
                        return ''.join(parts)
                text_field = first_choice.get('text')
                if isinstance(text_field, str):
                    return text_field
        return ''

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> tuple[int, int]:
        usage = data.get('usage', {})
        if not isinstance(usage, dict):
            return 0, 0
        prompt_tokens = int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0)
        completion_tokens = int(usage.get('completion_tokens') or usage.get('output_tokens') or 0)
        return prompt_tokens, completion_tokens

    @staticmethod
    def _normalize_messages(
        system_prompt: str,
        user_prompt: str,
        messages: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(messages, list):
            normalized_messages: list[dict[str, Any]] = []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                role = str(message.get('role') or '').strip().lower()
                if role not in {'system', 'user', 'assistant', 'tool'}:
                    continue
                payload: dict[str, Any] = {'role': role}
                if 'content' in message:
                    payload['content'] = message.get('content')
                if role == 'assistant' and isinstance(message.get('tool_calls'), list):
                    payload['tool_calls'] = message.get('tool_calls')
                if role == 'tool':
                    tool_call_id = message.get('tool_call_id')
                    name = message.get('name')
                    if isinstance(tool_call_id, str) and tool_call_id.strip():
                        payload['tool_call_id'] = tool_call_id.strip()
                    if isinstance(name, str) and name.strip():
                        payload['name'] = name.strip()
                normalized_messages.append(payload)
            if normalized_messages:
                return normalized_messages
        return [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ]

    @staticmethod
    def _build_chat_payload(
        model: str,
        system_prompt: str,
        user_prompt: str,
        *,
        messages: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'model': model,
            'messages': OpenAICompatibleClient._normalize_messages(system_prompt, user_prompt, messages),
            'stream': False,
        }
        if isinstance(tools, list) and tools:
            payload['tools'] = tools
            if tool_choice is not None:
                payload['tool_choice'] = tool_choice
        if max_tokens is not None:
            payload['max_tokens'] = int(max(max_tokens, 1))
        if temperature is not None:
            payload['temperature'] = float(temperature)
        return payload

    @staticmethod
    def _extract_tool_calls(data: dict[str, Any]) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        choices = data.get('choices')
        if not isinstance(choices, list) or not choices:
            return calls
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return calls
        message = first_choice.get('message')
        if not isinstance(message, dict):
            return calls
        raw_calls = message.get('tool_calls')
        if not isinstance(raw_calls, list):
            return calls
        for index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get('function')
            if not isinstance(function, dict):
                continue
            name = str(function.get('name') or '').strip()
            if not name:
                continue
            raw_arguments = function.get('arguments')
            parsed_arguments: dict[str, Any] = {}
            if isinstance(raw_arguments, dict):
                parsed_arguments = dict(raw_arguments)
            elif isinstance(raw_arguments, str):
                try:
                    candidate = json.loads(raw_arguments)
                    if isinstance(candidate, dict):
                        parsed_arguments = candidate
                except json.JSONDecodeError:
                    parsed_arguments = {}
            call_id = str(raw_call.get('id') or f'call_{index}').strip() or f'call_{index}'
            calls.append(
                {
                    'id': call_id,
                    'name': name,
                    'arguments': parsed_arguments,
                    'raw_arguments': raw_arguments,
                }
            )
        return calls

    def list_models(self, db: Session | None = None) -> dict[str, Any]:
        base_url = self._normalized_base_url()
        if not self.is_configured(base_url=base_url, db=db):
            return {
                'provider': self.provider,
                'models': [],
                'source': None,
                'error': f'Missing {self.provider.upper()}_API_KEY or {self.provider.upper()}_BASE_URL',
            }

        headers = {
            'Authorization': f'Bearer {self._normalized_api_key(db=db)}',
            'Accept': 'application/json',
        }
        url = f'{base_url}/models'
        try:
            payload = self._request_json('GET', url, headers)
            items = payload.get('data', [])
            models: list[str] = []
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    model_id = item.get('id')
                    if isinstance(model_id, str) and model_id.strip():
                        models.append(model_id.strip())
            return {
                'provider': self.provider,
                'models': sorted(set(models)),
                'source': url,
            }
        except Exception as exc:  # pragma: no cover - network failures are expected in local/offline runs
            return {
                'provider': self.provider,
                'models': [],
                'source': url,
                'error': str(exc),
            }

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        db: Session | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        provider = self.provider
        selected_model = (model or self._default_model() or '').strip() or self._default_model()
        started = time.perf_counter()
        base_url = self._normalized_base_url()
        max_tokens_raw = kwargs.get('max_tokens')
        temperature_raw = kwargs.get('temperature')
        messages_raw = kwargs.get('messages')
        tools_raw = kwargs.get('tools')
        tool_choice = kwargs.get('tool_choice')
        max_tokens: int | None = None
        temperature: float | None = None
        messages: list[dict[str, Any]] | None = None
        tools: list[dict[str, Any]] | None = None
        try:
            if max_tokens_raw is not None:
                max_tokens = int(max_tokens_raw)
        except (TypeError, ValueError):
            max_tokens = None
        try:
            if temperature_raw is not None:
                temperature = float(temperature_raw)
        except (TypeError, ValueError):
            temperature = None
        if isinstance(messages_raw, list):
            messages = [item for item in messages_raw if isinstance(item, dict)]
        if isinstance(tools_raw, list):
            tools = [item for item in tools_raw if isinstance(item, dict)]

        if not self.is_configured(base_url=base_url, db=db):
            latency = time.perf_counter() - started
            llm_calls_total.labels(provider='fallback', status='degraded').inc()
            llm_latency_seconds.labels(provider='fallback', model=selected_model, status='degraded').observe(latency)
            self._persist_log(
                provider='fallback',
                model=selected_model,
                status='degraded',
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                latency_ms=latency * 1000,
                error=f'Missing {provider.upper()}_API_KEY or {provider.upper()}_BASE_URL',
            )
            return {
                'provider': 'fallback',
                'text': f'LLM unavailable: missing {provider.upper()} API configuration. Using deterministic fallback.',
                'degraded': True,
                'prompt_tokens': 0,
                'completion_tokens': 0,
                'cost_usd': 0.0,
                'latency_ms': round(latency * 1000, 3),
            }

        url = f'{base_url}/chat/completions'
        payload = self._build_chat_payload(
            selected_model,
            system_prompt,
            user_prompt,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        headers = {
            'Authorization': f'Bearer {self._normalized_api_key(db=db)}',
            'Content-Type': 'application/json',
        }

        try:
            data = self._request_json('POST', url, headers, payload=payload)
            text = self._extract_text(data)
            tool_calls = self._extract_tool_calls(data)
            prompt_tokens, completion_tokens = self._extract_usage(data)
            cost_usd = self._estimate_cost_usd(prompt_tokens, completion_tokens)
            latency = time.perf_counter() - started

            llm_calls_total.labels(provider=provider, status='success').inc()
            llm_prompt_tokens_total.labels(provider=provider, model=selected_model).inc(prompt_tokens)
            llm_completion_tokens_total.labels(provider=provider, model=selected_model).inc(completion_tokens)
            llm_cost_usd_total.labels(provider=provider, model=selected_model).inc(cost_usd)
            llm_latency_seconds.labels(provider=provider, model=selected_model, status='success').observe(latency)

            self._persist_log(
                provider=provider,
                model=selected_model,
                status='success',
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
                latency_ms=latency * 1000,
            )
            return {
                'provider': provider,
                'text': text,
                'tool_calls': tool_calls,
                'raw': data,
                'degraded': False,
                'prompt_tokens': prompt_tokens,
                'completion_tokens': completion_tokens,
                'cost_usd': round(cost_usd, 8),
                'latency_ms': round(latency * 1000, 3),
            }
        except Exception as exc:  # pragma: no cover
            status_code: int | None = None
            if isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code

            fallback_model = self._default_model()
            if status_code == 404 and fallback_model and fallback_model != selected_model:
                try:
                    fallback_data = self._request_json(
                        'POST',
                        url,
                        headers,
                        payload=self._build_chat_payload(
                            fallback_model,
                            system_prompt,
                            user_prompt,
                            messages=messages,
                            tools=tools,
                            tool_choice=tool_choice,
                            max_tokens=max_tokens,
                            temperature=temperature,
                        ),
                    )
                    text = self._extract_text(fallback_data)
                    tool_calls = self._extract_tool_calls(fallback_data)
                    prompt_tokens, completion_tokens = self._extract_usage(fallback_data)
                    cost_usd = self._estimate_cost_usd(prompt_tokens, completion_tokens)
                    latency = time.perf_counter() - started

                    llm_calls_total.labels(provider=provider, status='success').inc()
                    llm_prompt_tokens_total.labels(provider=provider, model=fallback_model).inc(prompt_tokens)
                    llm_completion_tokens_total.labels(provider=provider, model=fallback_model).inc(completion_tokens)
                    llm_cost_usd_total.labels(provider=provider, model=fallback_model).inc(cost_usd)
                    llm_latency_seconds.labels(provider=provider, model=fallback_model, status='success').observe(latency)

                    self._persist_log(
                        provider=provider,
                        model=fallback_model,
                        status='success',
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        cost_usd=cost_usd,
                        latency_ms=latency * 1000,
                    )
                    return {
                        'provider': provider,
                        'text': text,
                        'tool_calls': tool_calls,
                        'raw': fallback_data,
                        'degraded': False,
                        'prompt_tokens': prompt_tokens,
                        'completion_tokens': completion_tokens,
                        'cost_usd': round(cost_usd, 8),
                        'latency_ms': round(latency * 1000, 3),
                        'effective_model': fallback_model,
                        'model_fallback_from': selected_model,
                    }
                except Exception as fallback_exc:
                    exc = fallback_exc
                    if isinstance(fallback_exc, httpx.HTTPStatusError):
                        status_code = fallback_exc.response.status_code

            latency = time.perf_counter() - started
            llm_calls_total.labels(provider=provider, status='error').inc()
            llm_latency_seconds.labels(provider=provider, model=selected_model, status='error').observe(latency)
            external_provider_failures_total.labels(provider=provider).inc()

            self._persist_log(
                provider=provider,
                model=selected_model,
                status='error',
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                latency_ms=latency * 1000,
                error=str(exc),
            )
            if status_code in {401, 403}:
                message = f'{self._display_name} authentication failed (HTTP {status_code}). Check API key.'
            else:
                logger.exception('%s_chat_call_error model=%s', provider, selected_model)
                message = f'{self._display_name} call failed after retries: {exc}'
            return {
                'provider': 'fallback',
                'text': message,
                'degraded': True,
                'prompt_tokens': 0,
                'completion_tokens': 0,
                'cost_usd': 0.0,
                'latency_ms': round(latency * 1000, 3),
                'error': str(exc),
            }
