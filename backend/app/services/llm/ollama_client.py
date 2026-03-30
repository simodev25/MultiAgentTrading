import json
import logging
import threading
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.observability.metrics import (
    external_provider_failures_total,
    llm_calls_total,
    llm_completion_tokens_total,
    llm_cost_usd_total,
    llm_latency_seconds,
    llm_prompt_tokens_total,
)
from app.services.connectors.runtime_settings import RuntimeConnectorSettings
from app.services.llm.base_llm_helpers import (
    is_api_key_valid,
    normalize_messages,
    persist_llm_call_log,
    safe_parse_tool_arguments,
)

logger = logging.getLogger(__name__)


def _is_retryable_ollama_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.ReadTimeout):
        return False
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return False


class OllamaCloudClient:
    _shared_clients: dict[float, httpx.Client] = {}
    _shared_clients_lock = threading.Lock()
    _MAX_SHARED_CLIENTS = 5
    _MAX_READ_TIMEOUT_RETRY_SECONDS = 120.0

    def __init__(self) -> None:
        self.settings = get_settings()

    @classmethod
    def _get_http_client(cls, timeout_seconds: float) -> httpx.Client:
        safe_timeout = max(float(timeout_seconds), 1.0)
        timeout = httpx.Timeout(
            connect=min(safe_timeout, 10.0),
            read=safe_timeout,
            write=min(safe_timeout, 30.0),
            pool=min(safe_timeout, 5.0),
        )
        with cls._shared_clients_lock:
            client = cls._shared_clients.get(safe_timeout)
            if client is not None and not client.is_closed:
                return client
            if len(cls._shared_clients) >= cls._MAX_SHARED_CLIENTS:
                for old_key in list(cls._shared_clients.keys()):
                    old_client = cls._shared_clients.pop(old_key, None)
                    if old_client is not None:
                        try:
                            old_client.close()
                        except Exception:
                            pass
                    break
            cls._shared_clients[safe_timeout] = httpx.Client(
                timeout=timeout,
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
            return cls._shared_clients[safe_timeout]

    def _resolved_timeout_seconds(self, timeout_seconds: float | None) -> float:
        if timeout_seconds is None:
            return max(float(self.settings.ollama_timeout_seconds), 1.0)
        return max(float(timeout_seconds), 1.0)

    @classmethod
    def _extended_read_timeout_seconds(cls, timeout_seconds: float) -> float | None:
        current_timeout = max(float(timeout_seconds), 1.0)
        if current_timeout >= cls._MAX_READ_TIMEOUT_RETRY_SECONDS:
            return None
        return min(max(current_timeout * 2.0, current_timeout + 15.0), cls._MAX_READ_TIMEOUT_RETRY_SECONDS)

    def _normalized_api_key(self, db: Session | None = None) -> str:
        del db  # Runtime connector settings are resolved without DB session injection.
        runtime_key = RuntimeConnectorSettings.get_string(
            'ollama',
            ('OLLAMA_API_KEY', 'ollama_api_key'),
        )
        key = (runtime_key or self.settings.ollama_api_key or '').strip()
        if len(key) >= 2 and key[0] == key[-1] and key[0] in {'"', "'"}:
            key = key[1:-1].strip()
        return key

    def _normalized_base_url(self) -> str:
        base_url = (self.settings.ollama_base_url or '').strip().rstrip('/')
        if not base_url:
            return base_url

        # Canonicalize known Ollama cloud hosts to a single endpoint host.
        parsed = urlparse(base_url)
        hostname = (parsed.hostname or '').strip().lower()
        if hostname in {'api.ollama.com', 'www.ollama.com'}:
            netloc = 'ollama.com'
            if parsed.port:
                netloc = f'{netloc}:{parsed.port}'
            normalized = urlunparse(
                (
                    parsed.scheme or 'https',
                    netloc,
                    parsed.path.rstrip('/'),
                    '',
                    '',
                    '',
                )
            ).rstrip('/')
            if normalized != base_url:
                logger.warning('ollama_base_url normalized from %s to %s', base_url, normalized)
            return normalized

        return base_url

    def is_configured(self, base_url: str | None = None, *, db: Session | None = None) -> bool:
        key = self._normalized_api_key(db=db)
        if not is_api_key_valid(key):
            return False
        if base_url is None:
            base_url = self._normalized_base_url()
        return bool(base_url)

    def _estimate_cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        input_cost = (prompt_tokens / 1_000_000) * self.settings.ollama_input_cost_per_1m_tokens
        output_cost = (completion_tokens / 1_000_000) * self.settings.ollama_output_cost_per_1m_tokens
        return float(input_cost + output_cost)

    @staticmethod
    def _persist_log(
        provider: str,
        model: str,
        status: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_ms: float,
        error: str | None = None,
    ) -> None:
        persist_llm_call_log(
            provider=provider, model=model, status=status,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            cost_usd=cost_usd, latency_ms=latency_ms, error=error,
        )

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        retry=retry_if_exception(_is_retryable_ollama_error),
        reraise=True,
    )
    def _call_remote(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        effective_timeout = self._resolved_timeout_seconds(timeout_seconds)
        client = self._get_http_client(effective_timeout)
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> tuple[str, int, int]:
        text = data.get('message', {}).get('content', '')
        prompt_tokens = int(data.get('prompt_eval_count') or data.get('prompt_tokens') or 0)
        completion_tokens = int(data.get('eval_count') or data.get('completion_tokens') or 0)
        return text, prompt_tokens, completion_tokens

    @staticmethod
    def _normalize_messages(
        system_prompt: str,
        user_prompt: str,
        messages: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        normalized = normalize_messages(system_prompt, user_prompt, messages)
        def _sanitize_tool_calls(raw_calls: Any) -> list[dict[str, Any]]:
            if not isinstance(raw_calls, list):
                return []
            sanitized: list[dict[str, Any]] = []
            for index, raw_call in enumerate(raw_calls):
                if not isinstance(raw_call, dict):
                    continue
                function = raw_call.get('function')
                if isinstance(function, dict):
                    name = str(function.get('name') or '').strip()
                    raw_arguments = function.get('arguments')
                else:
                    name = str(raw_call.get('name') or '').strip()
                    raw_arguments = raw_call.get('arguments')
                if not name:
                    continue
                call_id = str(raw_call.get('id') or f'call_{index}').strip() or f'call_{index}'
                sanitized.append(
                    {
                        'id': call_id,
                        'type': 'function',
                        'function': {
                            'name': name,
                            # Ollama is stricter than OpenAI-compatible providers:
                            # keep arguments as a dict and drop malformed JSON strings.
                            'arguments': safe_parse_tool_arguments(raw_arguments),
                        },
                    }
                )
            return sanitized

        # Ollama /api/chat rejects assistant tool-call messages when
        # `content` is null. Keep provider-specific coercion here while
        # shared normalization remains OpenAI-compatible.
        for item in normalized:
            if not isinstance(item, dict):
                continue
            if str(item.get('role') or '').strip().lower() != 'assistant':
                continue
            if not isinstance(item.get('tool_calls'), list):
                continue
            if item.get('content') is None:
                item['content'] = ''
            sanitized_calls = _sanitize_tool_calls(item.get('tool_calls'))
            if sanitized_calls:
                item['tool_calls'] = sanitized_calls
            else:
                item.pop('tool_calls', None)
        return normalized

    def _build_chat_payload(
        self,
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
            'messages': self._normalize_messages(system_prompt, user_prompt, messages),
            'stream': False,
        }
        if isinstance(tools, list) and tools:
            payload['tools'] = tools
            if tool_choice is not None:
                payload['tool_choice'] = tool_choice
        options: dict[str, Any] = {}
        if max_tokens is not None:
            options['num_predict'] = int(max(max_tokens, 1))
        if temperature is not None:
            options['temperature'] = float(temperature)
        if options:
            payload['options'] = options
        return payload

    @staticmethod
    def _extract_tool_calls(data: dict[str, Any]) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        message = data.get('message')
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
            parsed_arguments = safe_parse_tool_arguments(raw_arguments)
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
        api_key = self._normalized_api_key(db=db)
        timeout = max(min(int(self.settings.ollama_timeout_seconds), 30), 5)

        candidate_urls: list[str] = []
        if base_url:
            candidate_urls.append(f'{base_url}/api/tags')
        candidate_urls.append('https://ollama.com/api/tags')

        unique_urls = list(dict.fromkeys(candidate_urls))
        headers = {'Accept': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        errors: list[str] = []
        with httpx.Client(timeout=timeout) as client:
            for url in unique_urls:
                try:
                    response = client.get(url, headers=headers)
                    response.raise_for_status()
                    payload = response.json() if response.content else {}
                    models = payload.get('models', [])
                    names: list[str] = []
                    if isinstance(models, list):
                        for item in models:
                            if not isinstance(item, dict):
                                continue
                            name = item.get('name') or item.get('model')
                            if isinstance(name, str) and name.strip():
                                names.append(name.strip())
                    return {'provider': 'ollama', 'models': sorted(set(names)), 'source': url}
                except Exception as exc:  # pragma: no cover - network failures are expected in local/offline runs
                    errors.append(f'{url}: {exc}')

        return {'provider': 'ollama', 'models': [], 'source': None, 'error': '; '.join(errors[:2])}

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        db: Session | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        provider = 'ollama-cloud'
        selected_model = (model or self.settings.ollama_model or '').strip() or self.settings.ollama_model
        started = time.perf_counter()
        base_url = self._normalized_base_url()
        max_tokens_raw = kwargs.get('max_tokens')
        temperature_raw = kwargs.get('temperature')
        request_timeout_raw = kwargs.get('request_timeout_seconds')
        messages_raw = kwargs.get('messages')
        tools_raw = kwargs.get('tools')
        tool_choice = kwargs.get('tool_choice')
        max_tokens: int | None = None
        temperature: float | None = None
        request_timeout_seconds: float | None = None
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
        try:
            if request_timeout_raw is not None:
                request_timeout_seconds = float(request_timeout_raw)
        except (TypeError, ValueError):
            request_timeout_seconds = None
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
                error='Missing OLLAMA_API_KEY or OLLAMA_BASE_URL',
            )
            return {
                'provider': 'fallback',
                'text': 'LLM unavailable: missing OLLAMA_API_KEY. Using deterministic fallback.',
                'degraded': True,
                'prompt_tokens': 0,
                'completion_tokens': 0,
                'cost_usd': 0.0,
                'latency_ms': round(latency * 1000, 3),
            }

        url = f"{base_url}/api/chat"
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
            'Authorization': f"Bearer {self._normalized_api_key(db=db)}",
            'Content-Type': 'application/json',
        }

        try:
            effective_timeout_seconds = self._resolved_timeout_seconds(request_timeout_seconds)
            try:
                data = self._call_remote(url, payload, headers, timeout_seconds=effective_timeout_seconds)
            except httpx.ReadTimeout:
                retry_timeout_seconds = self._extended_read_timeout_seconds(effective_timeout_seconds)
                if retry_timeout_seconds is None:
                    raise
                logger.warning(
                    'ollama_chat_read_timeout_retry model=%s timeout_seconds=%.1f retry_timeout_seconds=%.1f',
                    selected_model,
                    effective_timeout_seconds,
                    retry_timeout_seconds,
                )
                data = self._call_remote(url, payload, headers, timeout_seconds=retry_timeout_seconds)
            text, prompt_tokens, completion_tokens = self._extract_usage(data)
            tool_calls = self._extract_tool_calls(data)
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

            logger.info(
                'ollama_chat_call_success model=%s chars=%s prompt_tokens=%s completion_tokens=%s latency_ms=%.2f',
                selected_model,
                len(text),
                prompt_tokens,
                completion_tokens,
                latency * 1000,
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
            response_body_excerpt: str | None = None
            if isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code
                try:
                    response_body_excerpt = (exc.response.text or '').strip()[:1000] or None
                except Exception:
                    response_body_excerpt = None

            malformed_tool_args_error = bool(
                status_code == 400
                and response_body_excerpt
                and "can't find closing '}' symbol" in response_body_excerpt.lower()
            )

            # Ollama Cloud occasionally fails to parse model-generated tool
            # arguments and returns HTTP 400. Retry once without tool
            # injection to preserve continuity instead of hard-degrading.
            if malformed_tool_args_error and isinstance(tools, list) and tools:
                try:
                    logger.warning(
                        'ollama malformed tool-call arguments; retrying without tools model=%s',
                        selected_model,
                    )
                    fallback_data = self._call_remote(
                        url,
                        self._build_chat_payload(
                            selected_model,
                            system_prompt,
                            user_prompt,
                            messages=messages,
                            tools=None,
                            tool_choice=None,
                            max_tokens=max_tokens,
                            temperature=temperature,
                        ),
                        headers,
                        timeout_seconds=request_timeout_seconds,
                    )
                    text, prompt_tokens, completion_tokens = self._extract_usage(fallback_data)
                    tool_calls = self._extract_tool_calls(fallback_data)
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
                        'raw': fallback_data,
                        'degraded': False,
                        'prompt_tokens': prompt_tokens,
                        'completion_tokens': completion_tokens,
                        'cost_usd': round(cost_usd, 8),
                        'latency_ms': round(latency * 1000, 3),
                        'tool_injection_fallback': 'disabled_after_malformed_arguments',
                    }
                except Exception as fallback_exc:
                    exc = fallback_exc
                    if isinstance(fallback_exc, httpx.HTTPStatusError):
                        status_code = fallback_exc.response.status_code
                        try:
                            response_body_excerpt = (fallback_exc.response.text or '').strip()[:1000] or None
                        except Exception:
                            response_body_excerpt = None

            # If a model override is invalid on Ollama Cloud (404), retry once with env default model.
            fallback_model = (self.settings.ollama_model or '').strip()
            if status_code == 404 and fallback_model and fallback_model != selected_model:
                try:
                    logger.warning(
                        'ollama model fallback on 404 from %s to %s',
                        selected_model,
                        fallback_model,
                    )
                    fallback_data = self._call_remote(
                        url,
                        self._build_chat_payload(
                            fallback_model,
                            system_prompt,
                            user_prompt,
                            messages=messages,
                            tools=tools,
                            tool_choice=tool_choice,
                            max_tokens=max_tokens,
                            temperature=temperature,
                        ),
                        headers,
                        timeout_seconds=request_timeout_seconds,
                    )
                    text, prompt_tokens, completion_tokens = self._extract_usage(fallback_data)
                    tool_calls = self._extract_tool_calls(fallback_data)
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
            external_provider_failures_total.labels(provider='ollama').inc()

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
                logger.error('ollama_chat_auth_error model=%s status=%s', selected_model, status_code)
                message = f'Ollama authentication failed (HTTP {status_code}). Check OLLAMA_API_KEY.'
            else:
                if status_code is not None:
                    logger.error(
                        'ollama_chat_http_error model=%s status=%s response=%s',
                        selected_model,
                        status_code,
                        response_body_excerpt or '<empty>',
                    )
                logger.exception('ollama_chat_call_error model=%s', selected_model)
                message = f'Ollama call failed after retries: {exc}'
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
