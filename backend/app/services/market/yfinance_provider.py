import logging
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Any

import httpx
import pandas as pd
import redis
import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange

from app.core.config import get_settings
from app.observability.metrics import yfinance_cache_hits_total, yfinance_cache_misses_total
from app.services.connectors.runtime_settings import RuntimeConnectorSettings

logger = logging.getLogger(__name__)


class YFinanceMarketProvider:
    _CACHE_PREFIX = 'yfinance:v1'
    interval_map = {
        'M5': ('5m', '7d'),
        'M15': ('15m', '30d'),
        'H1': ('60m', '90d'),
        'H4': ('60m', '180d'),
        'D1': ('1d', '365d'),
    }
    timeframe_seconds_map = {
        'M5': 300,
        'M15': 900,
        'H1': 3600,
        'H4': 14400,
        'D1': 86400,
    }
    index_alias_map = {
        'SPX500': '^GSPC',
        'US500': '^GSPC',
        'NSDQ100': '^NDX',
        'NAS100': '^NDX',
        'US30': '^DJI',
        'DJI30': '^DJI',
        'GER40': '^GDAXI',
        'DE40': '^GDAXI',
        'UK100': '^FTSE',
        'FRA40': '^FCHI',
        'JP225': '^N225',
        'NIKKEI225': '^N225',
    }
    fx_news_fallback_by_currency = {
        'USD': ['DX-Y.NYB', '^DXY', 'UUP'],
        'EUR': ['FXE'],
        'GBP': ['FXB'],
        'JPY': ['FXY'],
        'CHF': ['FXF'],
        'CAD': ['FXC'],
        'AUD': ['FXA'],
        'NZD': ['BNZL'],
    }
    macro_news_fallback_symbols = ['^GSPC', '^VIX', 'GC=F', 'CL=F']
    news_provider_defaults: dict[str, dict[str, Any]] = {
        'yahoo_finance': {
            'enabled': True,
            'priority': 100,
            'timeout_ms': 4000,
            'lookback_hours': 72,
        },
        'newsapi': {
            'enabled': True,
            'priority': 90,
            'timeout_ms': 5000,
            'api_key_env': 'NEWSAPI_API_KEY',
            'languages': ['en'],
            'lookback_hours': 48,
        },
        'tradingeconomics': {
            'enabled': True,
            'priority': 95,
            'timeout_ms': 5000,
            'api_key_env': 'TRADINGECONOMICS_API_KEY',
            'lookback_hours': 72,
            'importance_threshold': 2,
        },
        'finnhub': {
            'enabled': False,
            'priority': 70,
            'timeout_ms': 4000,
            'api_key_env': 'FINNHUB_API_KEY',
            'lookback_hours': 48,
        },
        'alphavantage': {
            'enabled': False,
            'priority': 60,
            'timeout_ms': 5000,
            'api_key_env': 'ALPHAVANTAGE_API_KEY',
            'lookback_hours': 48,
        },
    }
    news_analysis_defaults: dict[str, Any] = {
        'max_items_total': 25,
        'max_items_per_provider': 10,
        'deduplicate': True,
        'deduplicate_on': ['title', 'url', 'published_at'],
        'minimum_relevance_score': 0.35,
        'fallback_to_memory_when_no_fresh_news': True,
        'memory_is_secondary_evidence_only': True,
        'treat_no_news_as_no_evidence': True,
    }
    currency_aliases: dict[str, tuple[str, ...]] = {
        'USD': ('usd', 'dollar', 'federal reserve', 'fed'),
        'EUR': ('eur', 'euro', 'ecb'),
        'GBP': ('gbp', 'sterling', 'pound', 'boe'),
        'JPY': ('jpy', 'yen', 'boj'),
        'CHF': ('chf', 'swiss franc', 'snb'),
        'CAD': ('cad', 'canadian dollar', 'loonie', 'boc'),
        'AUD': ('aud', 'aussie', 'rba'),
        'NZD': ('nzd', 'kiwi', 'rbnz'),
    }
    macro_keywords: tuple[str, ...] = (
        'inflation',
        'cpi',
        'ppi',
        'rates',
        'rate decision',
        'employment',
        'payroll',
        'gdp',
        'growth',
        'energy',
        'oil',
        'gas',
        'central bank',
        'geopolitics',
        'war',
    )

    def __init__(self) -> None:
        self.settings = get_settings()
        self._redis = None
        self._redis_unavailable_until = 0.0
        if self.settings.yfinance_cache_enabled:
            try:
                self._redis = redis.from_url(
                    self.settings.redis_url,
                    encoding='utf-8',
                    decode_responses=True,
                    socket_connect_timeout=self.settings.yfinance_cache_connect_timeout_seconds,
                    socket_timeout=self.settings.yfinance_cache_connect_timeout_seconds,
                )
            except Exception as exc:  # pragma: no cover
                logger.warning('yfinance redis cache unavailable: %s', exc)
                self._redis = None

    def _cache_enabled(self) -> bool:
        if not self.settings.yfinance_cache_enabled or self._redis is None:
            return False
        return time.monotonic() >= self._redis_unavailable_until

    def _cache_degrade(self, exc: Exception) -> None:
        self._redis_unavailable_until = time.monotonic() + 15.0
        logger.debug('yfinance redis cache degraded temporarily: %s', exc)

    @classmethod
    def _cache_key(cls, *parts: Any) -> str:
        normalized = [str(part).strip().replace(' ', '_') for part in parts]
        return ':'.join([cls._CACHE_PREFIX, *normalized])

    def _cache_get_json(self, key: str, resource: str = 'unknown') -> dict[str, Any] | None:
        if not self._cache_enabled():
            return None
        try:
            raw = self._redis.get(key)
            if not raw:
                yfinance_cache_misses_total.labels(resource=resource).inc()
                return None
            payload = json.loads(raw)
            if isinstance(payload, dict):
                yfinance_cache_hits_total.labels(resource=resource).inc()
                return payload
            yfinance_cache_misses_total.labels(resource=resource).inc()
            return None
        except Exception as exc:  # pragma: no cover
            self._cache_degrade(exc)
            return None

    def _cache_set_json(self, key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        if not self._cache_enabled():
            return
        safe_ttl = max(int(ttl_seconds or 0), 1)
        try:
            self._redis.set(
                key,
                json.dumps(payload, default=str, ensure_ascii=True, separators=(',', ':')),
                ex=safe_ttl,
            )
        except Exception as exc:  # pragma: no cover
            self._cache_degrade(exc)

    @classmethod
    def _cache_lock_key(cls, base_key: str) -> str:
        return f'{base_key}:lock'

    def _cache_acquire_lock(self, key: str, ttl_seconds: float) -> str | None:
        if not self._cache_enabled():
            return None
        token = uuid.uuid4().hex
        lock_key = self._cache_lock_key(key)
        safe_ttl = max(int(round(float(ttl_seconds or 0.0))), 1)
        try:
            acquired = self._redis.set(lock_key, token, nx=True, ex=safe_ttl)
            return token if acquired else None
        except Exception as exc:  # pragma: no cover
            self._cache_degrade(exc)
            return None

    def _cache_release_lock(self, key: str, token: str | None) -> None:
        if not token or not self._cache_enabled():
            return
        lock_key = self._cache_lock_key(key)
        try:
            current = self._redis.get(lock_key)
            if current == token:
                self._redis.delete(lock_key)
        except Exception as exc:  # pragma: no cover
            self._cache_degrade(exc)

    def _cache_wait_for_json(self, key: str, wait_seconds: float) -> dict[str, Any] | None:
        if not self._cache_enabled():
            return None
        deadline = time.monotonic() + max(float(wait_seconds or 0.0), 0.0)
        while time.monotonic() < deadline:
            try:
                raw = self._redis.get(key)
                if raw:
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        return payload
            except Exception as exc:  # pragma: no cover
                self._cache_degrade(exc)
                return None
            time.sleep(0.05)
        return None

    def _cache_wait_for_frame(self, key: str, wait_seconds: float) -> pd.DataFrame | None:
        payload = self._cache_wait_for_json(key, wait_seconds)
        if payload is None:
            return None
        frame_payload = payload.get('frame')
        if not isinstance(frame_payload, str) or not frame_payload:
            return None
        try:
            frame = pd.read_json(StringIO(frame_payload), orient='split')
            return frame if isinstance(frame, pd.DataFrame) else None
        except Exception as exc:  # pragma: no cover
            self._cache_degrade(exc)
            return None

    def clear_news_cache(self) -> int:
        if not self._cache_enabled():
            return 0
        pattern = self._cache_key('news', '*')
        deleted = 0
        try:
            cursor: int | str = 0
            while True:
                cursor, keys = self._redis.scan(cursor=cursor, match=pattern, count=200)
                if keys:
                    deleted += int(self._redis.delete(*keys))
                if str(cursor) == '0':
                    break
            if deleted > 0:
                logger.info('yfinance news cache invalidated keys=%s', deleted)
        except Exception as exc:  # pragma: no cover
            self._cache_degrade(exc)
            return 0
        return deleted

    def _cache_get_frame(self, key: str, resource: str = 'historical') -> pd.DataFrame | None:
        payload = self._cache_get_json(key, resource=resource)
        if payload is None:
            return None
        frame_payload = payload.get('frame')
        if not isinstance(frame_payload, str) or not frame_payload:
            return None
        try:
            frame = pd.read_json(StringIO(frame_payload), orient='split')
            if not isinstance(frame, pd.DataFrame):
                return None
            return frame
        except Exception as exc:  # pragma: no cover
            self._cache_degrade(exc)
            return None

    def _cache_set_frame(self, key: str, frame: pd.DataFrame, ttl_seconds: int) -> None:
        if frame.empty:
            return
        if len(frame) > max(int(self.settings.yfinance_cache_frame_max_rows), 100):
            return
        try:
            frame_json = frame.to_json(orient='split', date_format='iso')
        except Exception:  # pragma: no cover
            return
        self._cache_set_json(key, {'frame': frame_json}, ttl_seconds)

    def _timeframe_seconds(self, timeframe: str) -> int:
        return self.timeframe_seconds_map.get(str(timeframe or '').strip().upper(), 3600)

    def _snapshot_ttl_seconds(self, timeframe: str) -> int:
        min_ttl = max(int(self.settings.yfinance_snapshot_cache_min_ttl_seconds), 1)
        max_ttl = max(int(self.settings.yfinance_snapshot_cache_max_ttl_seconds), min_ttl)
        adaptive = max(2, self._timeframe_seconds(timeframe) // 120)
        return max(min_ttl, min(max_ttl, adaptive))

    def _timeframe_cache_bucket(self, timeframe: str, now: datetime | None = None) -> int:
        timeframe_seconds = max(self._timeframe_seconds(timeframe), 1)
        ts = (now or datetime.now(timezone.utc)).timestamp()
        return int(ts // timeframe_seconds)

    @staticmethod
    def _normalize_pair(pair: str) -> str:
        normalized = (pair or '').strip().upper()
        if not normalized:
            return normalized
        without_suffix = re.sub(r'\.[A-Z0-9_]+$', '', normalized)
        compact = without_suffix.replace('/', '').replace('-', '')
        match = re.search(r'[A-Z]{6}', compact)
        return match.group(0) if match else without_suffix

    @classmethod
    def _ticker_candidates(cls, pair: str) -> list[str]:
        cleaned = (pair or '').strip()
        if not cleaned:
            return []

        candidates: list[str] = []

        def add_candidate(value: str) -> None:
            item = (value or '').strip()
            if item and item not in candidates:
                candidates.append(item)

        upper_pair = cleaned.upper()
        without_suffix = re.sub(r'\.[A-Z0-9_]+$', '', upper_pair)
        base_pair = without_suffix or upper_pair
        # Strip broker suffixes for Yahoo lookups as well.
        add_candidate(base_pair)

        forex_match = re.search(r'[A-Z]{6}', base_pair)
        if forex_match:
            add_candidate(forex_match.group(0))

        index_alias = cls.index_alias_map.get(base_pair)
        if index_alias:
            add_candidate(index_alias)

        yfinance_symbols: list[str] = []
        for symbol in candidates:
            if symbol.startswith('^') or '=' in symbol:
                add_to = symbol
                if add_to not in yfinance_symbols:
                    yfinance_symbols.append(add_to)
                continue
            fx_like = re.fullmatch(r'[A-Z]{6}', symbol) is not None
            if fx_like:
                with_suffix = f'{symbol}=X'
                if with_suffix not in yfinance_symbols:
                    yfinance_symbols.append(with_suffix)
            if symbol not in yfinance_symbols:
                yfinance_symbols.append(symbol)

        return yfinance_symbols

    def _resample_if_needed(self, frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        if timeframe.upper() == 'H4' and not frame.empty:
            return (
                frame.resample('4h')
                .agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'})
                .dropna()
            )
        return frame

    @staticmethod
    def _extract_news_entry(item: dict[str, Any], symbol: str) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None

        content = item.get('content') if isinstance(item.get('content'), dict) else {}
        title = str(item.get('title') or content.get('title') or '').strip()
        if not title:
            return None

        provider = content.get('provider') if isinstance(content.get('provider'), dict) else {}
        canonical = content.get('canonicalUrl') if isinstance(content.get('canonicalUrl'), dict) else {}
        finance = content.get('finance') if isinstance(content.get('finance'), dict) else {}
        click_through = content.get('clickThroughUrl')

        def _to_url(value: Any) -> str:
            if isinstance(value, dict):
                return str(value.get('url') or '').strip()
            return str(value or '').strip()

        publisher = str(
            item.get('publisher')
            or provider.get('displayName')
            or content.get('provider')
            or ''
        ).strip()
        # Prefer Yahoo click/preview links over canonical when available, because
        # some canonical URLs can point to unrelated/paywalled redirects.
        link = (
            _to_url(item.get('link'))
            or _to_url(click_through)
            or _to_url(content.get('previewUrl'))
            or _to_url(canonical.get('url'))
        )
        published = (
            item.get('providerPublishTime')
            or content.get('pubDate')
            or content.get('displayTime')
            or finance.get('premiumFinance')
        )
        summary = str(content.get('summary') or item.get('summary') or '').strip() or None
        description = str(content.get('description') or item.get('description') or '').strip() or None

        return {
            'title': title,
            'publisher': publisher,
            'link': link,
            'published': published,
            'summary': summary,
            'description': description,
            'source_symbol': symbol,
        }

    @staticmethod
    def _split_fx_pair(pair: str) -> tuple[str | None, str | None]:
        normalized = YFinanceMarketProvider._normalize_pair(pair)
        if len(normalized) == 6 and normalized.isalpha():
            return normalized[:3], normalized[3:]
        return None, None

    @classmethod
    def _news_symbol_candidates(cls, pair: str) -> list[str]:
        candidates: list[str] = []

        def add(symbol: str | None) -> None:
            value = str(symbol or '').strip()
            if value and value not in candidates:
                candidates.append(value)

        for symbol in cls._ticker_candidates(pair):
            add(symbol)

        base_ccy, quote_ccy = cls._split_fx_pair(pair)
        for ccy in (base_ccy, quote_ccy):
            for symbol in cls.fx_news_fallback_by_currency.get(str(ccy or ''), []):
                add(symbol)

        for symbol in cls.macro_news_fallback_symbols:
            add(symbol)

        return candidates

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return min(max(float(value), low), high)

    @staticmethod
    def _safe_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        # Epoch timestamps from providers.
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
            except Exception:
                return None

        text = str(value).strip()
        if not text:
            return None
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @classmethod
    def _to_iso_datetime(cls, value: Any) -> str | None:
        dt = cls._safe_datetime(value)
        return dt.isoformat() if dt is not None else None

    @classmethod
    def _freshness_score(cls, published_at: Any) -> float:
        dt = cls._safe_datetime(published_at)
        if dt is None:
            return 0.2
        age_hours = max((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 0.0)
        if age_hours <= 6:
            return 1.0
        if age_hours <= 24:
            return 0.8
        if age_hours <= 48:
            return 0.65
        if age_hours <= 72:
            return 0.5
        if age_hours <= 168:
            return 0.3
        return 0.2

    @staticmethod
    def _credibility_score(provider: str, source_name: str | None = None) -> float:
        source = str(source_name or '').lower()
        provider_key = str(provider or '').lower()
        if provider_key == 'tradingeconomics':
            return 0.9
        if provider_key in {'reuters', 'wsj', 'bloomberg'}:
            return 0.86
        if 'reuters' in source or 'wall street journal' in source or 'bloomberg' in source:
            return 0.86
        if 'yahoo' in source:
            return 0.72
        if provider_key in {'newsapi', 'yahoo_finance', 'finnhub', 'alphavantage'}:
            return 0.7
        return 0.65

    @classmethod
    def _pair_terms(cls, pair: str) -> tuple[str | None, str | None, list[str]]:
        base, quote = cls._split_fx_pair(pair)
        terms: list[str] = []
        if base:
            terms.extend(cls.currency_aliases.get(base, tuple()))
            terms.append(base.lower())
        if quote:
            terms.extend(cls.currency_aliases.get(quote, tuple()))
            terms.append(quote.lower())
        compact = cls._normalize_pair(pair).lower()
        if compact:
            terms.append(compact)
        deduped: list[str] = []
        for item in terms:
            if item and item not in deduped:
                deduped.append(item)
        return base, quote, deduped

    @classmethod
    def _headline_sentiment_hint(cls, text: str) -> str:
        lowered = str(text or '').lower()
        positive = ('rally', 'rebound', 'gain', 'gains', 'rise', 'rises', 'hawkish', 'strong')
        negative = ('selloff', 'sell-off', 'drop', 'drops', 'fall', 'falls', 'dovish', 'weak', 'recession')
        pos_count = sum(1 for item in positive if item in lowered)
        neg_count = sum(1 for item in negative if item in lowered)
        if pos_count > neg_count:
            return 'bullish'
        if neg_count > pos_count:
            return 'bearish'
        if pos_count == 0 and neg_count == 0:
            return 'unknown'
        return 'neutral'

    @classmethod
    def _compute_pair_relevance(
        cls,
        pair: str,
        title: str,
        summary: str | None = None,
    ) -> tuple[float, float, float, float]:
        base, quote, pair_terms = cls._pair_terms(pair)
        text = f"{title or ''} {summary or ''}".lower()

        def contains_any(items: tuple[str, ...]) -> bool:
            return any(str(item).lower() in text for item in items if item)

        base_rel = 0.0
        quote_rel = 0.0
        pair_rel = 0.0
        macro_rel = 0.0

        if base:
            aliases = cls.currency_aliases.get(base, tuple())
            base_hits = sum(1 for alias in aliases if alias in text)
            if base_hits:
                base_rel = cls._clamp(0.35 + base_hits * 0.2, 0.0, 1.0)
        if quote:
            aliases = cls.currency_aliases.get(quote, tuple())
            quote_hits = sum(1 for alias in aliases if alias in text)
            if quote_hits:
                quote_rel = cls._clamp(0.35 + quote_hits * 0.2, 0.0, 1.0)

        compact_pair = cls._normalize_pair(pair).lower()
        if compact_pair and compact_pair in text:
            pair_rel = 1.0
        elif compact_pair and '/' in compact_pair and compact_pair.replace('/', '') in text:
            pair_rel = 1.0
        elif pair_terms and sum(1 for term in pair_terms if term in text) >= 2:
            pair_rel = 0.65
        elif base_rel > 0.0 or quote_rel > 0.0:
            pair_rel = cls._clamp(max(base_rel, quote_rel) * 0.8, 0.0, 1.0)

        if contains_any(cls.macro_keywords):
            macro_rel = 0.7
        elif any(item in text for item in ('central bank', 'rates', 'inflation', 'employment', 'gdp')):
            macro_rel = 0.6

        return (
            round(base_rel, 3),
            round(quote_rel, 3),
            round(pair_rel, 3),
            round(macro_rel, 3),
        )

    @classmethod
    def _normalize_article_item(
        cls,
        *,
        provider: str,
        pair: str,
        title: str,
        summary: str | None,
        url: str | None,
        published_at: Any,
        source_name: str | None,
        language: str | None = None,
        source_symbol: str | None = None,
    ) -> dict[str, Any] | None:
        clean_title = str(title or '').strip()
        if not clean_title:
            return None
        clean_summary = str(summary or '').strip() or None
        clean_url = str(url or '').strip() or None
        published_iso = cls._to_iso_datetime(published_at)
        base_rel, quote_rel, pair_rel, macro_rel = cls._compute_pair_relevance(pair, clean_title, clean_summary)
        freshness = round(cls._freshness_score(published_iso), 3)
        credibility = round(cls._credibility_score(provider, source_name), 3)
        hint = cls._headline_sentiment_hint(f"{clean_title} {clean_summary or ''}")

        return {
            'provider': provider,
            'type': 'article',
            'title': clean_title,
            'summary': clean_summary,
            'url': clean_url,
            'published_at': published_iso,
            'source_name': str(source_name or '').strip() or None,
            'language': str(language or '').strip() or None,
            'base_currency_relevance': base_rel,
            'quote_currency_relevance': quote_rel,
            'pair_relevance': pair_rel,
            'macro_relevance': macro_rel,
            'freshness_score': freshness,
            'credibility_score': credibility,
            'sentiment_hint': hint,
            # Backward-compatible aliases consumed by existing payloads.
            'publisher': str(source_name or '').strip() or None,
            'link': clean_url,
            'published': published_iso,
            'source_symbol': source_symbol,
        }

    @classmethod
    def _normalize_macro_event_item(
        cls,
        *,
        provider: str,
        pair: str,
        event_name: str,
        country: str | None,
        currency: str | None,
        published_at: Any,
        importance: Any,
        actual: Any = None,
        forecast: Any = None,
        previous: Any = None,
        event_category: str = 'other',
        directional_hint: str = 'unknown',
    ) -> dict[str, Any] | None:
        name = str(event_name or '').strip()
        if not name:
            return None

        base_rel, quote_rel, pair_rel, _ = cls._compute_pair_relevance(pair, name, str(country or ''))
        if str(currency or '').strip():
            ccy = str(currency).strip().upper()
            base, quote = cls._split_fx_pair(pair)
            if ccy and base and ccy == base:
                base_rel = max(base_rel, 0.8)
                pair_rel = max(pair_rel, 0.75)
            elif ccy and quote and ccy == quote:
                quote_rel = max(quote_rel, 0.8)
                pair_rel = max(pair_rel, 0.75)

        published_iso = cls._to_iso_datetime(published_at)
        freshness = round(cls._freshness_score(published_iso), 3)
        credibility = round(cls._credibility_score(provider, provider), 3)
        importance_value = int(max(min(cls._safe_float(importance, 0.0), 3.0), 0.0))
        normalized_hint = str(directional_hint or 'unknown').strip().lower()
        if normalized_hint not in {'bullish', 'bearish', 'neutral', 'unknown'}:
            normalized_hint = 'unknown'

        return {
            'provider': provider,
            'type': 'macro_event',
            'event_name': name,
            'country': str(country or '').strip() or None,
            'currency': str(currency or '').strip() or None,
            'published_at': published_iso,
            'importance': importance_value,
            'actual': actual,
            'forecast': forecast,
            'previous': previous,
            'event_category': event_category,
            'base_currency_relevance': round(base_rel, 3),
            'quote_currency_relevance': round(quote_rel, 3),
            'pair_relevance': round(pair_rel, 3),
            'freshness_score': freshness,
            'credibility_score': credibility,
            'directional_hint': normalized_hint,
        }

    @staticmethod
    def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = YFinanceMarketProvider._merge_dict(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _news_providers_config(self) -> dict[str, dict[str, Any]]:
        configured = self.settings.news_providers if isinstance(self.settings.news_providers, dict) else {}
        runtime_settings = RuntimeConnectorSettings.settings('yfinance')
        runtime_configured = runtime_settings.get('news_providers') if isinstance(runtime_settings, dict) else {}
        if not isinstance(runtime_configured, dict):
            runtime_configured = {}
        combined_overrides = self._merge_dict(configured, runtime_configured)
        merged = self._merge_dict(self.news_provider_defaults, combined_overrides)
        output: dict[str, dict[str, Any]] = {}
        for name, cfg in merged.items():
            if not isinstance(cfg, dict):
                continue
            output[str(name)] = dict(cfg)
        return output

    def _news_analysis_config(self) -> dict[str, Any]:
        configured = self.settings.news_analysis if isinstance(self.settings.news_analysis, dict) else {}
        return self._merge_dict(self.news_analysis_defaults, configured)

    def _provider_api_key(self, provider_name: str, provider_cfg: dict[str, Any]) -> str:
        env_name = str(provider_cfg.get('api_key_env') or '').strip()
        if env_name == 'NEWSAPI_API_KEY':
            return RuntimeConnectorSettings.get_string(
                'yfinance',
                ('NEWSAPI_API_KEY', 'newsapi_api_key'),
                default=str(self.settings.newsapi_api_key or '').strip(),
            )
        if env_name == 'TRADINGECONOMICS_API_KEY':
            return RuntimeConnectorSettings.get_string(
                'yfinance',
                ('TRADINGECONOMICS_API_KEY', 'tradingeconomics_api_key'),
                default=str(self.settings.tradingeconomics_api_key or '').strip(),
            )
        if env_name == 'FINNHUB_API_KEY':
            return RuntimeConnectorSettings.get_string(
                'yfinance',
                ('FINNHUB_API_KEY', 'finnhub_api_key'),
                default=str(self.settings.finnhub_api_key or '').strip(),
            )
        if env_name == 'ALPHAVANTAGE_API_KEY':
            return RuntimeConnectorSettings.get_string(
                'yfinance',
                ('ALPHAVANTAGE_API_KEY', 'alphavantage_api_key'),
                default=str(self.settings.alphavantage_api_key or '').strip(),
            )
        # Backward-compatible fallback for custom env names.
        if env_name:
            return RuntimeConnectorSettings.get_string(
                'yfinance',
                (env_name, env_name.lower()),
                default=str(provider_cfg.get('api_key') or '').strip(),
            )
        if provider_name == 'newsapi':
            return RuntimeConnectorSettings.get_string(
                'yfinance',
                ('NEWSAPI_API_KEY', 'newsapi_api_key'),
                default=str(self.settings.newsapi_api_key or '').strip(),
            )
        if provider_name == 'tradingeconomics':
            return RuntimeConnectorSettings.get_string(
                'yfinance',
                ('TRADINGECONOMICS_API_KEY', 'tradingeconomics_api_key'),
                default=str(self.settings.tradingeconomics_api_key or '').strip(),
            )
        if provider_name == 'finnhub':
            return RuntimeConnectorSettings.get_string(
                'yfinance',
                ('FINNHUB_API_KEY', 'finnhub_api_key'),
                default=str(self.settings.finnhub_api_key or '').strip(),
            )
        if provider_name == 'alphavantage':
            return RuntimeConnectorSettings.get_string(
                'yfinance',
                ('ALPHAVANTAGE_API_KEY', 'alphavantage_api_key'),
                default=str(self.settings.alphavantage_api_key or '').strip(),
            )
        return ''

    def _provider_timeout_seconds(self, provider_cfg: dict[str, Any], default_ms: int = 4000) -> float:
        timeout_ms = int(max(self._safe_float(provider_cfg.get('timeout_ms'), float(default_ms)), 500.0))
        return max(timeout_ms / 1000.0, 0.5)

    @staticmethod
    def _is_rate_limited_error(error: Any) -> bool:
        text = str(error or '').strip().lower()
        if not text:
            return False
        tokens = (
            'rate limit',
            'requests per minute',
            'requests per day',
            'too many requests',
            '429',
            'throttl',
            'free api requests',
        )
        return any(token in text for token in tokens)

    @staticmethod
    def _event_category_from_name(name: str) -> str:
        lowered = str(name or '').lower()
        if any(item in lowered for item in ('inflation', 'cpi', 'ppi')):
            return 'inflation'
        if any(item in lowered for item in ('rate', 'central bank', 'interest')):
            return 'rates'
        if any(item in lowered for item in ('employment', 'payroll', 'unemployment')):
            return 'employment'
        if any(item in lowered for item in ('gdp', 'growth', 'pmi')):
            return 'growth'
        if any(item in lowered for item in ('oil', 'gas', 'energy')):
            return 'energy'
        if any(item in lowered for item in ('war', 'sanction', 'geopolitic')):
            return 'geopolitics'
        return 'other'

    @classmethod
    def _deduplicate_items(cls, items: list[dict[str, Any]], on_fields: list[str]) -> list[dict[str, Any]]:
        seen: set[tuple[str, ...]] = set()
        output: list[dict[str, Any]] = []
        normalized_fields = [str(field or '').strip() for field in on_fields if str(field or '').strip()]
        if not normalized_fields:
            normalized_fields = ['title', 'url', 'published_at']

        for item in items:
            if not isinstance(item, dict):
                continue
            key_values: list[str] = []
            for field in normalized_fields:
                value = item.get(field)
                key_values.append(str(value or '').strip().lower())
            dedupe_key = tuple(key_values)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            output.append(item)
        return output

    def _fetch_yahoo_news_items(
        self,
        pair: str,
        *,
        max_items: int,
        timeout_seconds: float,
        provider_cfg: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        symbols_scanned: list[str] = []
        last_symbol: str | None = None
        cfg = provider_cfg if isinstance(provider_cfg, dict) else {}
        lookback_hours = int(max(self._safe_float(cfg.get('lookback_hours'), 72.0), 1.0))
        min_dt = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

        for symbol in self._news_symbol_candidates(pair):
            try:
                last_symbol = symbol
                symbols_scanned.append(symbol)
                ticker = yf.Ticker(symbol)
                news_items = ticker.news or []
            except Exception:  # pragma: no cover
                logger.debug('yfinance news candidate failed pair=%s symbol=%s', pair, symbol, exc_info=True)
                continue

            symbol_selected: list[dict[str, Any]] = []
            for item in news_items:
                parsed = self._extract_news_entry(item, symbol)
                if not parsed:
                    continue
                normalized = self._normalize_article_item(
                    provider='yahoo_finance',
                    pair=pair,
                    title=str(parsed.get('title') or ''),
                    summary=parsed.get('summary'),
                    url=parsed.get('link'),
                    published_at=parsed.get('published'),
                    source_name=parsed.get('publisher'),
                    source_symbol=symbol,
                )
                if normalized is None:
                    continue
                published_dt = self._safe_datetime(normalized.get('published_at') or normalized.get('published'))
                if published_dt is None or published_dt < min_dt:
                    continue
                symbol_selected.append(normalized)
                if len(symbol_selected) >= max_items:
                    break

            if symbol_selected:
                selected = symbol_selected[:max_items]
                break

        return selected, {
            'symbol': str(selected[0].get('source_symbol') or last_symbol or '') if selected else last_symbol,
            'symbols_scanned': symbols_scanned,
            'timeout_seconds': timeout_seconds,
            'lookback_hours': lookback_hours,
        }

    def _keywords_for_pair(self, pair: str) -> list[str]:
        base, quote = self._split_fx_pair(pair)
        keywords: list[str] = []
        compact = self._normalize_pair(pair)
        if compact:
            keywords.append(compact)
        if base:
            keywords.extend([base, *self.currency_aliases.get(base, tuple())[:2]])
        if quote:
            keywords.extend([quote, *self.currency_aliases.get(quote, tuple())[:2]])
        keywords.extend(['central bank', 'inflation', 'rates'])
        deduped: list[str] = []
        for item in keywords:
            value = str(item or '').strip()
            if value and value not in deduped:
                deduped.append(value)
        return deduped

    def _fetch_newsapi_items(
        self,
        pair: str,
        *,
        max_items: int,
        timeout_seconds: float,
        provider_cfg: dict[str, Any],
        api_key: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        lookback_hours = int(max(self._safe_float(provider_cfg.get('lookback_hours'), 48), 1.0))
        languages = provider_cfg.get('languages') if isinstance(provider_cfg.get('languages'), list) else ['en']
        query = ' OR '.join(self._keywords_for_pair(pair)[:8])
        from_dt = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
        params = {
            'q': query,
            'from': from_dt,
            'sortBy': 'publishedAt',
            'pageSize': max_items,
            'language': str(languages[0] if languages else 'en'),
        }
        endpoint = 'https://newsapi.org/v2/everything'
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(endpoint, params=params, headers={'X-Api-Key': api_key})
            response.raise_for_status()
            payload = response.json() if response.content else {}
        articles = payload.get('articles', []) if isinstance(payload, dict) else []
        output: list[dict[str, Any]] = []
        for item in articles if isinstance(articles, list) else []:
            if not isinstance(item, dict):
                continue
            source = item.get('source') if isinstance(item.get('source'), dict) else {}
            normalized = self._normalize_article_item(
                provider='newsapi',
                pair=pair,
                title=str(item.get('title') or ''),
                summary=str(item.get('description') or item.get('content') or ''),
                url=item.get('url'),
                published_at=item.get('publishedAt'),
                source_name=str(source.get('name') or ''),
                language=item.get('language') or params['language'],
            )
            if normalized is None:
                continue
            output.append(normalized)
            if len(output) >= max_items:
                break
        return output, {'query': query}

    def _fetch_tradingeconomics_items(
        self,
        pair: str,
        *,
        max_items: int,
        timeout_seconds: float,
        provider_cfg: dict[str, Any],
        api_key: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        lookback_hours = int(max(self._safe_float(provider_cfg.get('lookback_hours'), 72), 1.0))
        importance_threshold = int(max(min(self._safe_float(provider_cfg.get('importance_threshold'), 2.0), 3.0), 0.0))
        start_date = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).date().isoformat()
        end_date = datetime.now(timezone.utc).date().isoformat()
        endpoint = 'https://api.tradingeconomics.com/calendar'
        params = {
            'c': api_key,
            'f': 'json',
            'd1': start_date,
            'd2': end_date,
        }
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(endpoint, params=params)
            response.raise_for_status()
            payload = response.json() if response.content else []

        events = payload if isinstance(payload, list) else []
        output: list[dict[str, Any]] = []
        for item in events:
            if not isinstance(item, dict):
                continue
            importance = int(max(min(self._safe_float(item.get('Importance'), 0.0), 3.0), 0.0))
            if importance < importance_threshold:
                continue
            name = str(item.get('Event') or item.get('Category') or '').strip()
            category = self._event_category_from_name(name)
            normalized = self._normalize_macro_event_item(
                provider='tradingeconomics',
                pair=pair,
                event_name=name,
                country=item.get('Country'),
                currency=item.get('Currency'),
                published_at=item.get('Date') or item.get('LastUpdate'),
                importance=importance,
                actual=item.get('Actual'),
                forecast=item.get('Forecast'),
                previous=item.get('Previous'),
                event_category=category,
                directional_hint='unknown',
            )
            if normalized is None:
                continue
            output.append(normalized)
            if len(output) >= max_items:
                break
        return output, {'window': {'start': start_date, 'end': end_date}}

    def _fetch_finnhub_items(
        self,
        pair: str,
        *,
        max_items: int,
        timeout_seconds: float,
        provider_cfg: dict[str, Any],
        api_key: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        lookback_hours = int(max(self._safe_float(provider_cfg.get('lookback_hours'), 48), 1.0))
        date_from = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).date().isoformat()
        date_to = datetime.now(timezone.utc).date().isoformat()
        endpoint = 'https://finnhub.io/api/v1/news'
        params = {
            'category': 'general',
            'minId': 0,
            'token': api_key,
        }
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(endpoint, params=params)
            response.raise_for_status()
            payload = response.json() if response.content else []

        articles = payload if isinstance(payload, list) else []
        output: list[dict[str, Any]] = []
        for item in articles:
            if not isinstance(item, dict):
                continue
            published_ts = item.get('datetime')
            dt = self._safe_datetime(published_ts)
            if dt is not None:
                if dt < datetime.now(timezone.utc) - timedelta(hours=lookback_hours):
                    continue
            normalized = self._normalize_article_item(
                provider='finnhub',
                pair=pair,
                title=str(item.get('headline') or ''),
                summary=item.get('summary'),
                url=item.get('url'),
                published_at=published_ts,
                source_name=item.get('source'),
                language='en',
            )
            if normalized is None:
                continue
            output.append(normalized)
            if len(output) >= max_items:
                break
        return output, {'window': {'from': date_from, 'to': date_to}}

    @classmethod
    def _alphavantage_ticker_candidates(cls, pair: str) -> list[str]:
        candidates: list[str] = []

        def add(symbol: str | None) -> None:
            value = str(symbol or '').strip().upper()
            if not value:
                return
            if value.endswith('=X'):
                value = value[:-2]
            value = value.lstrip('^')
            if not value:
                return
            # AlphaVantage NEWS_SENTIMENT supports only alnum, colon, underscore and hyphen.
            if '.' in value:
                # Some Yahoo fallback symbols are exchange-qualified (for example DX-Y.NYB).
                # Keep only the base token when possible to avoid provider hard-fail.
                value = value.split('.', 1)[0].strip()
            if re.fullmatch(r'[A-Z0-9:_-]{1,24}', value) is None:
                return
            if value not in candidates:
                candidates.append(value)

        add(cls._normalize_pair(pair))
        for symbol in cls._ticker_candidates(pair):
            add(symbol)

        base_ccy, quote_ccy = cls._split_fx_pair(pair)
        for ccy in (base_ccy, quote_ccy):
            add(ccy)
            for symbol in cls.fx_news_fallback_by_currency.get(str(ccy or '').upper(), []):
                add(symbol)

        return candidates

    @staticmethod
    def _alphavantage_api_message(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ('Error Message', 'Information', 'Note'):
            value = str(payload.get(key) or '').strip()
            if value:
                return value
        return None

    def _fetch_alphavantage_items(
        self,
        pair: str,
        *,
        max_items: int,
        timeout_seconds: float,
        provider_cfg: dict[str, Any],
        api_key: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        lookback_hours = int(max(self._safe_float(provider_cfg.get('lookback_hours'), 48), 1.0))
        compact = self._normalize_pair(pair)
        ticker_candidates = self._alphavantage_ticker_candidates(pair)
        tickers_primary = ','.join(ticker_candidates[:8]) if ticker_candidates else compact
        fallback_tickers: str | None = None
        base_ccy, quote_ccy = self._split_fx_pair(pair)
        if base_ccy and quote_ccy:
            compact_upper = compact.upper()
            narrowed = [item for item in ticker_candidates if item.upper() != compact_upper]
            if narrowed:
                fallback_tickers = ','.join(narrowed[:8])

        endpoint = 'https://www.alphavantage.co/query'
        request_sets: list[tuple[str | None, str]] = []
        if tickers_primary:
            request_sets.append((tickers_primary, 'primary'))
        if fallback_tickers and fallback_tickers != tickers_primary:
            request_sets.append((fallback_tickers, 'fx_fallback'))
        if not request_sets:
            request_sets.append((None, 'global'))

        feed: list[dict[str, Any]] = []
        api_message: str | None = None
        attempts_meta: list[dict[str, Any]] = []
        with httpx.Client(timeout=timeout_seconds) as client:
            for tickers_value, mode in request_sets:
                params = {
                    'function': 'NEWS_SENTIMENT',
                    'apikey': api_key,
                    'limit': max_items,
                    'sort': 'LATEST',
                    'time_from': (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).strftime('%Y%m%dT%H%M'),
                }
                if tickers_value:
                    params['tickers'] = tickers_value

                response = client.get(endpoint, params=params)
                response.raise_for_status()
                payload = response.json() if response.content else {}
                message = self._alphavantage_api_message(payload)
                if message and api_message is None:
                    api_message = message

                current_feed = payload.get('feed', []) if isinstance(payload, dict) else []
                if not isinstance(current_feed, list):
                    current_feed = []
                attempts_meta.append(
                    {
                        'mode': mode,
                        'tickers': params.get('tickers'),
                        'feed_count': len(current_feed),
                    }
                )
                if current_feed:
                    feed = current_feed
                    break

        if not feed and api_message:
            raise RuntimeError(api_message)

        output: list[dict[str, Any]] = []
        for item in feed if isinstance(feed, list) else []:
            if not isinstance(item, dict):
                continue
            hint = str(item.get('overall_sentiment_label') or '').lower()
            if 'bullish' in hint:
                sentiment_hint = 'bullish'
            elif 'bearish' in hint:
                sentiment_hint = 'bearish'
            elif hint:
                sentiment_hint = 'neutral'
            else:
                sentiment_hint = 'unknown'

            normalized = self._normalize_article_item(
                provider='alphavantage',
                pair=pair,
                title=str(item.get('title') or ''),
                summary=item.get('summary'),
                url=item.get('url'),
                published_at=item.get('time_published'),
                source_name=item.get('source'),
                language='en',
            )
            if normalized is None:
                continue
            normalized['sentiment_hint'] = sentiment_hint
            output.append(normalized)
            if len(output) >= max_items:
                break
        return output, {
            'requested_tickers': [tickers for tickers, _mode in request_sets if tickers],
            'attempts': attempts_meta,
            'lookback_hours': lookback_hours,
        }

    def _fetch_history_with_fallback(
        self,
        pair: str,
        timeframe: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> tuple[pd.DataFrame, str | None]:
        interval, period = self.interval_map.get(timeframe.upper(), ('60m', '90d'))
        symbols = self._ticker_candidates(pair)
        if not symbols:
            return pd.DataFrame(), None

        for symbol in symbols:
            try:
                ticker = yf.Ticker(symbol)
                if start_date is not None and end_date is not None:
                    frame = ticker.history(start=start_date, end=end_date, interval=interval)
                else:
                    frame = ticker.history(period=period, interval=interval)
                frame = self._resample_if_needed(frame, timeframe)
                if not frame.empty:
                    return frame, symbol
            except Exception:  # pragma: no cover
                logger.debug('yfinance history candidate failed pair=%s symbol=%s', pair, symbol, exc_info=True)
        return pd.DataFrame(), symbols[0]

    def _prepare_frame(self, pair: str, timeframe: str) -> pd.DataFrame:
        frame, _ = self._fetch_history_with_fallback(pair, timeframe)
        return frame

    def get_market_snapshot(self, pair: str, timeframe: str) -> dict[str, Any]:
        pair_key = self._normalize_pair(pair) or str(pair or '').strip().upper()
        timeframe_key = str(timeframe or '').strip().upper() or 'H1'
        snapshot_cache_key = self._cache_key(
            'snapshot',
            pair_key,
            timeframe_key,
            self._timeframe_cache_bucket(timeframe_key),
        )
        cached_snapshot = self._cache_get_json(snapshot_cache_key, resource='snapshot')
        if cached_snapshot is not None:
            return cached_snapshot
        cache_lock_token = self._cache_acquire_lock(snapshot_cache_key, self.settings.yfinance_cache_lock_ttl_seconds)
        if cache_lock_token is None:
            waited_snapshot = self._cache_wait_for_json(snapshot_cache_key, self.settings.yfinance_cache_wait_timeout_seconds)
            if waited_snapshot is not None:
                return waited_snapshot

        try:
            try:
                frame, used_symbol = self._fetch_history_with_fallback(pair, timeframe)
                if frame.empty:
                    return {'degraded': True, 'error': 'No market data available', 'pair': pair, 'timeframe': timeframe}

                close = frame['Close']
                high = frame['High']
                low = frame['Low']

                rsi = RSIIndicator(close=close, window=14).rsi().iloc[-1]
                ema_fast = EMAIndicator(close=close, window=20).ema_indicator().iloc[-1]
                ema_slow = EMAIndicator(close=close, window=50).ema_indicator().iloc[-1]
                macd_diff = MACD(close=close).macd_diff().iloc[-1]
                atr = AverageTrueRange(high=high, low=low, close=close).average_true_range().iloc[-1]

                latest = float(close.iloc[-1])
                prev = float(close.iloc[-2]) if len(close) > 1 else latest
                pct_change = ((latest - prev) / prev) * 100 if prev else 0.0

                trend = 'bullish' if ema_fast > ema_slow else 'bearish'
                if abs(ema_fast - ema_slow) < latest * 0.0003:
                    trend = 'neutral'

                resolved = {
                    'degraded': False,
                    'pair': pair,
                    'timeframe': timeframe,
                    'symbol': used_symbol,
                    'last_price': latest,
                    'change_pct': round(float(pct_change), 5),
                    'rsi': round(float(rsi), 3),
                    'ema_fast': round(float(ema_fast), 6),
                    'ema_slow': round(float(ema_slow), 6),
                    'macd_diff': round(float(macd_diff), 6),
                    'atr': round(float(atr), 6),
                    'trend': trend,
                }
                self._cache_set_json(
                    snapshot_cache_key,
                    resolved,
                    self._snapshot_ttl_seconds(timeframe_key),
                )
                return resolved
            except Exception as exc:  # pragma: no cover - third-party failures are expected in degraded mode
                logger.exception('yfinance market snapshot failure pair=%s timeframe=%s', pair, timeframe)
                return {'degraded': True, 'error': str(exc), 'pair': pair, 'timeframe': timeframe}
        finally:
            self._cache_release_lock(snapshot_cache_key, cache_lock_token)

    def get_historical_candles(self, pair: str, timeframe: str, start_date: str, end_date: str) -> pd.DataFrame:
        pair_key = self._normalize_pair(pair) or str(pair or '').strip().upper()
        timeframe_key = str(timeframe or '').strip().upper() or 'H1'
        history_cache_key = self._cache_key(
            'historical',
            pair_key,
            timeframe_key,
            str(start_date or ''),
            str(end_date or ''),
        )
        cached_frame = self._cache_get_frame(history_cache_key, resource='historical')
        if cached_frame is not None:
            return cached_frame
        cache_lock_token = self._cache_acquire_lock(history_cache_key, self.settings.yfinance_cache_lock_ttl_seconds)
        if cache_lock_token is None:
            waited_frame = self._cache_wait_for_frame(history_cache_key, self.settings.yfinance_cache_wait_timeout_seconds)
            if waited_frame is not None:
                return waited_frame

        try:
            try:
                frame, _ = self._fetch_history_with_fallback(
                    pair,
                    timeframe,
                    start_date=start_date,
                    end_date=end_date,
                )
                self._cache_set_frame(
                    history_cache_key,
                    frame,
                    self.settings.yfinance_historical_cache_ttl_seconds,
                )
                return frame
            except Exception as exc:  # pragma: no cover
                logger.exception('yfinance historical retrieval failure pair=%s timeframe=%s', pair, timeframe)
                return pd.DataFrame()
        finally:
            self._cache_release_lock(history_cache_key, cache_lock_token)

    def get_recent_candles(self, pair: str, timeframe: str, limit: int = 200) -> list[dict[str, Any]]:
        pair_key = self._normalize_pair(pair) or str(pair or '').strip().upper()
        timeframe_key = str(timeframe or '').strip().upper() or 'H1'
        safe_limit = max(int(limit or 1), 1)
        cache_key = self._cache_key(
            'recent',
            pair_key,
            timeframe_key,
            self._timeframe_cache_bucket(timeframe_key),
            safe_limit,
        )
        cached_payload = self._cache_get_json(cache_key, resource='snapshot')
        if cached_payload is not None and isinstance(cached_payload.get('candles'), list):
            return list(cached_payload.get('candles') or [])

        def as_float(value: Any) -> float | None:
            try:
                if pd.isna(value):
                    return None
                return float(value)
            except Exception:
                return None

        frame = self._prepare_frame(pair, timeframe)
        if frame.empty:
            self._cache_set_json(
                cache_key,
                {'pair': pair, 'timeframe': timeframe, 'candles': []},
                self._snapshot_ttl_seconds(timeframe_key),
            )
            return []

        candles: list[dict[str, Any]] = []
        for index, row in frame.tail(safe_limit).iterrows():
            ts = index.isoformat() if hasattr(index, 'isoformat') else str(index)
            candles.append(
                {
                    'ts': ts,
                    'open': as_float(row.get('Open')),
                    'high': as_float(row.get('High')),
                    'low': as_float(row.get('Low')),
                    'close': as_float(row.get('Close')),
                    'volume': as_float(row.get('Volume')),
                }
            )

        self._cache_set_json(
            cache_key,
            {'pair': pair, 'timeframe': timeframe, 'candles': candles},
            self._snapshot_ttl_seconds(timeframe_key),
        )
        return candles

    def get_news_context(self, pair: str, limit: int = 5) -> dict[str, Any]:
        pair_key = self._normalize_pair(pair) or str(pair or '').strip().upper()
        safe_limit = max(int(limit or 1), 1)
        news_cache_key = self._cache_key('news', pair_key, safe_limit)
        cached_news = self._cache_get_json(news_cache_key, resource='news')
        if cached_news is not None:
            return cached_news
        cache_lock_token = self._cache_acquire_lock(news_cache_key, self.settings.yfinance_cache_lock_ttl_seconds)
        if cache_lock_token is None:
            waited_news = self._cache_wait_for_json(news_cache_key, self.settings.yfinance_cache_wait_timeout_seconds)
            if waited_news is not None:
                return waited_news

        try:
            try:
                provider_cfg_map = self._news_providers_config()
                analysis_cfg = self._news_analysis_config()
                max_items_total = max(int(self._safe_float(analysis_cfg.get('max_items_total'), 25.0)), safe_limit)
                max_items_per_provider = max(int(self._safe_float(analysis_cfg.get('max_items_per_provider'), 10.0)), 1)
                deduplicate = bool(analysis_cfg.get('deduplicate', True))
                dedupe_on = analysis_cfg.get('deduplicate_on') if isinstance(analysis_cfg.get('deduplicate_on'), list) else ['title', 'url', 'published_at']

                providers_ordered = sorted(
                    provider_cfg_map.items(),
                    key=lambda item: float(item[1].get('priority') or 0.0),
                    reverse=True,
                )

                provider_status: dict[str, dict[str, Any]] = {}
                aggregated_articles: list[dict[str, Any]] = []
                aggregated_events: list[dict[str, Any]] = []
                symbols_scanned: list[str] = []
                primary_symbol: str | None = None
                provider_errors = 0
                callable_providers = 0

                for provider_name, cfg in providers_ordered:
                    enabled = bool(cfg.get('enabled', False))
                    status_payload: dict[str, Any] = {'enabled': enabled, 'status': 'disabled', 'count': 0, 'error': None}
                    provider_status[provider_name] = status_payload
                    if not enabled:
                        continue

                    timeout_seconds = self._provider_timeout_seconds(cfg)
                    if provider_name in {'newsapi', 'tradingeconomics', 'finnhub', 'alphavantage'}:
                        api_key = self._provider_api_key(provider_name, cfg)
                        if not api_key:
                            status_payload['status'] = 'unavailable'
                            status_payload['error'] = 'missing_credentials'
                            continue
                    else:
                        api_key = ''

                    callable_providers += 1
                    try:
                        fetched_count = 0
                        if provider_name == 'yahoo_finance':
                            items, meta = self._fetch_yahoo_news_items(
                                pair,
                                max_items=max_items_per_provider,
                                timeout_seconds=timeout_seconds,
                                provider_cfg=cfg,
                            )
                            if meta.get('symbol'):
                                primary_symbol = str(meta.get('symbol') or primary_symbol or '')
                            scanned = meta.get('symbols_scanned') if isinstance(meta.get('symbols_scanned'), list) else []
                            for item in scanned:
                                value = str(item or '').strip()
                                if value and value not in symbols_scanned:
                                    symbols_scanned.append(value)
                            aggregated_articles.extend(items)
                            fetched_count = len(items)
                        elif provider_name == 'newsapi':
                            items, _ = self._fetch_newsapi_items(
                                pair,
                                max_items=max_items_per_provider,
                                timeout_seconds=timeout_seconds,
                                provider_cfg=cfg,
                                api_key=api_key,
                            )
                            aggregated_articles.extend(items)
                            fetched_count = len(items)
                        elif provider_name == 'tradingeconomics':
                            events, _ = self._fetch_tradingeconomics_items(
                                pair,
                                max_items=max_items_per_provider,
                                timeout_seconds=timeout_seconds,
                                provider_cfg=cfg,
                                api_key=api_key,
                            )
                            aggregated_events.extend(events)
                            fetched_count = len(events)
                        elif provider_name == 'finnhub':
                            items, _ = self._fetch_finnhub_items(
                                pair,
                                max_items=max_items_per_provider,
                                timeout_seconds=timeout_seconds,
                                provider_cfg=cfg,
                                api_key=api_key,
                            )
                            aggregated_articles.extend(items)
                            fetched_count = len(items)
                        elif provider_name == 'alphavantage':
                            items, _ = self._fetch_alphavantage_items(
                                pair,
                                max_items=max_items_per_provider,
                                timeout_seconds=timeout_seconds,
                                provider_cfg=cfg,
                                api_key=api_key,
                            )
                            aggregated_articles.extend(items)
                            fetched_count = len(items)
                        else:
                            status_payload['status'] = 'disabled'
                            status_payload['error'] = 'unsupported_provider'
                            continue

                        status_payload['status'] = 'ok' if fetched_count > 0 else 'empty'
                        status_payload['count'] = fetched_count
                    except Exception as exc:  # pragma: no cover - external APIs are unstable by nature
                        message = str(exc)
                        if self._is_rate_limited_error(message):
                            status_payload['status'] = 'unavailable'
                            status_payload['error'] = 'rate_limited'
                            status_payload['message'] = message
                            continue
                        provider_errors += 1
                        status_payload['status'] = 'error'
                        status_payload['error'] = message
                        logger.debug('news provider failed provider=%s pair=%s', provider_name, pair, exc_info=True)

                if deduplicate:
                    aggregated_articles = self._deduplicate_items(aggregated_articles, [str(item) for item in dedupe_on])
                    aggregated_events = self._deduplicate_items(aggregated_events, ['event_name', 'country', 'currency', 'published_at'])

                aggregated_articles = aggregated_articles[:max_items_total]
                aggregated_events = aggregated_events[:max_items_total]

                if primary_symbol is None:
                    primary_symbol = self._news_symbol_candidates(pair)[0] if self._news_symbol_candidates(pair) else None
                if not symbols_scanned:
                    symbols_scanned = self._news_symbol_candidates(pair)[:1]

                total_items = len(aggregated_articles) + len(aggregated_events)
                if total_items > 0:
                    fetch_status = 'partial' if provider_errors > 0 else 'ok'
                    degraded = False
                else:
                    if callable_providers > 0 and provider_errors >= callable_providers:
                        fetch_status = 'error'
                        degraded = True
                    elif provider_errors > 0:
                        fetch_status = 'partial'
                        degraded = False
                    else:
                        fetch_status = 'empty'
                        degraded = False

                status_compact = {name: str((payload.get('status') or 'disabled')) for name, payload in provider_status.items()}
                reason: str | None = None
                if total_items == 0:
                    if fetch_status == 'error':
                        reason = 'All enabled news providers failed'
                    else:
                        reason = 'No recent relevant news or macro events were available from enabled providers'

                resolved = {
                    'degraded': degraded,
                    'pair': pair,
                    'symbol': primary_symbol,
                    'symbols_scanned': symbols_scanned,
                    'news': aggregated_articles,
                    'macro_events': aggregated_events,
                    'provider_status': provider_status,
                    'provider_status_compact': status_compact,
                    'fetch_status': fetch_status,
                }
                if reason:
                    resolved['reason'] = reason
                self._cache_set_json(news_cache_key, resolved, self.settings.yfinance_news_cache_ttl_seconds)
                return resolved
            except Exception as exc:  # pragma: no cover
                logger.exception('yfinance news retrieval failure pair=%s', pair)
                return {
                    'degraded': True,
                    'pair': pair,
                    'news': [],
                    'macro_events': [],
                    'fetch_status': 'error',
                    'provider_status': {},
                    'provider_status_compact': {},
                    'error': str(exc),
                }
        finally:
            self._cache_release_lock(news_cache_key, cache_lock_token)

    def test_news_provider(self, provider_name: str, *, pair: str, max_items: int = 5) -> dict[str, Any]:
        name = str(provider_name or '').strip().lower()
        provider_cfg_map = self._news_providers_config()
        cfg = provider_cfg_map.get(name)
        if not isinstance(cfg, dict):
            return {
                'provider': name,
                'enabled': False,
                'status': 'unsupported',
                'count': 0,
                'items': [],
                'error': 'unsupported_provider',
            }

        enabled = bool(cfg.get('enabled', False))
        if not enabled:
            return {
                'provider': name,
                'enabled': False,
                'status': 'disabled',
                'count': 0,
                'items': [],
                'error': None,
            }

        timeout_seconds = self._provider_timeout_seconds(cfg)
        api_key = ''
        if name in {'newsapi', 'tradingeconomics', 'finnhub', 'alphavantage'}:
            api_key = self._provider_api_key(name, cfg)
            if not api_key:
                return {
                    'provider': name,
                    'enabled': True,
                    'status': 'unavailable',
                    'count': 0,
                    'items': [],
                    'error': 'missing_credentials',
                }

        try:
            if name == 'yahoo_finance':
                items, meta = self._fetch_yahoo_news_items(
                    pair,
                    max_items=max(max_items, 1),
                    timeout_seconds=timeout_seconds,
                    provider_cfg=cfg,
                )
            elif name == 'newsapi':
                items, meta = self._fetch_newsapi_items(
                    pair,
                    max_items=max(max_items, 1),
                    timeout_seconds=timeout_seconds,
                    provider_cfg=cfg,
                    api_key=api_key,
                )
            elif name == 'tradingeconomics':
                items, meta = self._fetch_tradingeconomics_items(
                    pair,
                    max_items=max(max_items, 1),
                    timeout_seconds=timeout_seconds,
                    provider_cfg=cfg,
                    api_key=api_key,
                )
            elif name == 'finnhub':
                items, meta = self._fetch_finnhub_items(
                    pair,
                    max_items=max(max_items, 1),
                    timeout_seconds=timeout_seconds,
                    provider_cfg=cfg,
                    api_key=api_key,
                )
            elif name == 'alphavantage':
                items, meta = self._fetch_alphavantage_items(
                    pair,
                    max_items=max(max_items, 1),
                    timeout_seconds=timeout_seconds,
                    provider_cfg=cfg,
                    api_key=api_key,
                )
            else:
                return {
                    'provider': name,
                    'enabled': enabled,
                    'status': 'unsupported',
                    'count': 0,
                    'items': [],
                    'error': 'unsupported_provider',
                }
        except Exception as exc:  # pragma: no cover - external providers can fail at runtime
            message = str(exc)
            if self._is_rate_limited_error(message):
                return {
                    'provider': name,
                    'enabled': enabled,
                    'status': 'unavailable',
                    'count': 0,
                    'items': [],
                    'error': 'rate_limited',
                    'meta': {'message': message},
                }
            return {
                'provider': name,
                'enabled': enabled,
                'status': 'error',
                'count': 0,
                'items': [],
                'error': message,
            }

        trimmed_items = items[: max(min(max_items, 5), 1)] if isinstance(items, list) else []
        return {
            'provider': name,
            'enabled': enabled,
            'status': 'ok' if len(trimmed_items) > 0 else 'empty',
            'count': len(trimmed_items),
            'items': trimmed_items,
            'error': None,
            'meta': meta if isinstance(meta, dict) else {},
        }
