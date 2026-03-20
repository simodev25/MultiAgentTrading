from __future__ import annotations

import threading
import time
from typing import Any

from app.db.models.connector_config import ConnectorConfig
from app.db.session import SessionLocal


class RuntimeConnectorSettings:
    """Read connector settings at runtime with a short in-process cache."""

    _cache_ttl_seconds = 5.0
    _cache: dict[str, tuple[float, dict[str, Any]]] = {}
    _lock = threading.Lock()

    @classmethod
    def clear_cache(cls, connector_name: str | None = None) -> None:
        with cls._lock:
            if connector_name is None:
                cls._cache.clear()
                return
            key = str(connector_name or '').strip().lower()
            if key:
                cls._cache.pop(key, None)

    @classmethod
    def _load_from_db(cls, connector_name: str) -> dict[str, Any]:
        db = SessionLocal()
        try:
            row = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == connector_name).first()
            if row is None or not isinstance(row.settings, dict):
                return {}
            return dict(row.settings)
        except Exception:
            return {}
        finally:
            db.close()

    @classmethod
    def settings(cls, connector_name: str) -> dict[str, Any]:
        key = str(connector_name or '').strip().lower()
        if not key:
            return {}

        now = time.monotonic()
        with cls._lock:
            cached = cls._cache.get(key)
            if cached and now - cached[0] <= cls._cache_ttl_seconds:
                return dict(cached[1])

        loaded = cls._load_from_db(key)

        with cls._lock:
            cls._cache[key] = (now, loaded)
            if len(cls._cache) > 64:
                fresh: dict[str, tuple[float, dict[str, Any]]] = {}
                for cache_key, cache_value in cls._cache.items():
                    if now - cache_value[0] <= cls._cache_ttl_seconds:
                        fresh[cache_key] = cache_value
                cls._cache = fresh

        return dict(loaded)

    @classmethod
    def get_string(
        cls,
        connector_name: str,
        keys: list[str] | tuple[str, ...],
        *,
        default: str = '',
    ) -> str:
        settings = cls.settings(connector_name)
        for key in keys:
            normalized_key = str(key or '').strip()
            if not normalized_key:
                continue
            if normalized_key not in settings:
                continue
            value = settings.get(normalized_key)
            if value is None:
                continue
            text = str(value).strip()
            if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
                text = text[1:-1].strip()
            if text:
                return text
        return str(default or '').strip()
