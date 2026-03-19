import logging
import json
import re
import time
import uuid
from datetime import datetime, timezone
from io import StringIO
from typing import Any

import pandas as pd
import redis
import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange

from app.core.config import get_settings
from app.observability.metrics import yfinance_cache_hits_total, yfinance_cache_misses_total

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
        match = re.search(r'[A-Z]{6}', normalized)
        return match.group(0) if match else normalized

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
                last_symbol: str | None = None
                selected: list[dict[str, Any]] = []
                seen_keys: set[tuple[str, str]] = set()
                symbols_scanned: list[str] = []

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
                        title = str(item.get('title', '') or '').strip()
                        if not title:
                            continue
                        link = str(item.get('link', '') or '').strip()
                        dedupe_key = (title.lower(), link)
                        if dedupe_key in seen_keys:
                            continue
                        seen_keys.add(dedupe_key)

                        symbol_selected.append(
                            {
                                'title': title,
                                'publisher': str(item.get('publisher', '') or '').strip(),
                                'link': link,
                                'published': item.get('providerPublishTime'),
                                'source_symbol': symbol,
                            }
                        )
                        if len(symbol_selected) >= safe_limit:
                            break

                    if symbol_selected:
                        selected = symbol_selected
                        break

                if selected:
                    primary_symbol = str(selected[0].get('source_symbol') or last_symbol or '')
                    resolved = {
                        'degraded': False,
                        'pair': pair,
                        'symbol': primary_symbol,
                        'symbols_scanned': symbols_scanned,
                        'news': selected,
                    }
                    self._cache_set_json(news_cache_key, resolved, self.settings.yfinance_news_cache_ttl_seconds)
                    return resolved

                resolved = {
                    'degraded': False,
                    'pair': pair,
                    'symbol': last_symbol,
                    'symbols_scanned': symbols_scanned,
                    'news': [],
                    'reason': 'No Yahoo Finance news across pair, currency and macro fallback symbols',
                }
                self._cache_set_json(news_cache_key, resolved, self.settings.yfinance_news_cache_ttl_seconds)
                return resolved
            except Exception as exc:  # pragma: no cover
                logger.exception('yfinance news retrieval failure pair=%s', pair)
                return {'degraded': True, 'pair': pair, 'news': [], 'error': str(exc)}
        finally:
            self._cache_release_lock(news_cache_key, cache_lock_token)
