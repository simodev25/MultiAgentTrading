import logging
import threading
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
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

logger = logging.getLogger(__name__)


def _is_retryable_ollama_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return False


class OllamaCloudClient:
    _shared_client: httpx.Client | None = None
    _shared_client_timeout_seconds: float | None = None
    _shared_client_lock = threading.Lock()

    def __init__(self) -> None:
        self.settings = get_settings()

    @classmethod
    def _get_http_client(cls, timeout_seconds: float) -> httpx.Client:
        safe_timeout = max(float(timeout_seconds), 1.0)
        with cls._shared_client_lock:
            if (
                cls._shared_client is not None
                and not cls._shared_client.is_closed
                and cls._shared_client_timeout_seconds == safe_timeout
            ):
                return cls._shared_client

            if cls._shared_client is not None and not cls._shared_client.is_closed:
                cls._shared_client.close()

            cls._shared_client = httpx.Client(
                timeout=safe_timeout,
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
            cls._shared_client_timeout_seconds = safe_timeout
            return cls._shared_client

    def _normalized_api_key(self) -> str:
        key = (self.settings.ollama_api_key or '').strip()
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

    def is_configured(self, base_url: str | None = None) -> bool:
        key = self._normalized_api_key()
        if not key:
            return False
        if key.lower() in {'replace_me', 'changeme', 'change-me', 'your_api_key'}:
            return False
        if base_url is None:
            base_url = self._normalized_base_url()
        return bool(base_url)

    def _estimate_cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        input_cost = (prompt_tokens / 1_000_000) * self.settings.ollama_input_cost_per_1m_tokens
        output_cost = (completion_tokens / 1_000_000) * self.settings.ollama_output_cost_per_1m_tokens
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
        retry=retry_if_exception(_is_retryable_ollama_error),
        reraise=True,
    )
    def _call_remote(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        client = self._get_http_client(self.settings.ollama_timeout_seconds)
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> tuple[str, int, int]:
        text = data.get('message', {}).get('content', '')
        prompt_tokens = int(data.get('prompt_eval_count') or data.get('prompt_tokens') or 0)
        completion_tokens = int(data.get('eval_count') or data.get('completion_tokens') or 0)
        return text, prompt_tokens, completion_tokens

    def _build_chat_payload(self, model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        return {
            'model': model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'stream': False,
        }

    def chat(self, system_prompt: str, user_prompt: str, model: str | None = None) -> dict[str, Any]:
        provider = 'ollama-cloud'
        selected_model = (model or self.settings.ollama_model or '').strip() or self.settings.ollama_model
        started = time.perf_counter()
        base_url = self._normalized_base_url()

        if not self.is_configured(base_url=base_url):
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
        payload = self._build_chat_payload(selected_model, system_prompt, user_prompt)
        headers = {
            'Authorization': f"Bearer {self._normalized_api_key()}",
            'Content-Type': 'application/json',
        }

        try:
            data = self._call_remote(url, payload, headers)
            text, prompt_tokens, completion_tokens = self._extract_usage(data)
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
                        self._build_chat_payload(fallback_model, system_prompt, user_prompt),
                        headers,
                    )
                    text, prompt_tokens, completion_tokens = self._extract_usage(fallback_data)
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
