import logging
import inspect
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class MetaApiClient:
    _SUCCESS_TRADE_STRING_CODES = {
        'ERR_NO_ERROR',
        'TRADE_RETCODE_PLACED',
        'TRADE_RETCODE_DONE',
        'TRADE_RETCODE_DONE_PARTIAL',
        'TRADE_RETCODE_NO_CHANGES',
    }
    _SUCCESS_TRADE_NUMERIC_CODES = {0, 10008, 10009, 10010, 10025}
    _FAILURE_MARKERS = ('UNKNOWN', 'ERROR', 'INVALID', 'REJECT', 'DENIED', 'DISABLED', 'TIMEOUT', 'NO_MONEY')
    _MARKET_TIMEFRAME_MAP = {
        'M1': '1m',
        'M2': '2m',
        'M3': '3m',
        'M4': '4m',
        'M5': '5m',
        'M6': '6m',
        'M10': '10m',
        'M12': '12m',
        'M15': '15m',
        'M20': '20m',
        'M30': '30m',
        'H1': '1h',
        'H2': '2h',
        'H3': '3h',
        'H4': '4h',
        'H6': '6h',
        'H8': '8h',
        'H12': '12h',
        'D1': '1d',
        'W1': '1w',
        'MN1': '1mn',
    }

    def __init__(self) -> None:
        self.settings = get_settings()
        self._metaapi_cls = None
        self._sdk_by_region: dict[str, Any] = {}

        try:
            from metaapi_cloud_sdk import MetaApi  # type: ignore

            self._metaapi_cls = MetaApi
        except Exception as exc:  # pragma: no cover
            logger.warning('metaapi sdk unavailable, using REST fallback: %s', exc)

    def _resolve_token(self) -> str:
        return (self.settings.metaapi_token or '').strip()

    def _resolve_account_id(self, account_id: str | None) -> str:
        return (account_id or self.settings.metaapi_account_id or '').strip()

    def _resolve_base_url(self) -> str:
        return self.settings.metaapi_base_url.rstrip('/')

    @staticmethod
    def _normalize_time_range(
        start_time: datetime | None,
        end_time: datetime | None,
        days: int,
    ) -> tuple[datetime, datetime]:
        if days is None:
            safe_days = 1
        else:
            safe_days = min(max(int(days), 0), 365)
        end = end_time or datetime.now(timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        end = end.astimezone(timezone.utc)

        if start_time is None:
            if safe_days == 0:
                start = end.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                start = end - timedelta(days=safe_days)
        else:
            start = start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        start = start.astimezone(timezone.utc)

        if start >= end:
            if safe_days == 0:
                start = end.replace(hour=0, minute=0, second=0, microsecond=0)
                if start >= end:
                    start = end - timedelta(minutes=1)
            else:
                start = end - timedelta(days=1)
        return start, end

    @staticmethod
    def _iso_utc(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')

    @staticmethod
    def _to_utc_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            dt = value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        if isinstance(value, (int, float)):
            raw = float(value)
            # Heuristic: values above 10^10 are usually milliseconds.
            if abs(raw) >= 10_000_000_000:
                raw = raw / 1000.0
            try:
                return datetime.fromtimestamp(raw, tz=timezone.utc)
            except Exception:
                return None

        if not isinstance(value, str):
            return None

        text = value.strip()
        if not text:
            return None

        candidates = [text]
        # Some MetaApi/MT5 payloads append a trailing timezone label (e.g. "... GMT+0200").
        if ' GMT' in text:
            candidates.append(text.split(' GMT', 1)[0].strip())
        if text.upper().endswith(' UTC'):
            candidates.append(text[:-4].strip())

        for candidate in candidates:
            if candidate.isdigit():
                parsed = MetaApiClient._to_utc_datetime(int(candidate))
                if parsed is not None:
                    return parsed

            normalized = candidate.replace('Z', '+00:00')
            try:
                dt = datetime.fromisoformat(normalized)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                pass

            for fmt in (
                '%Y-%m-%d %H:%M:%S.%f',
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S.%f',
                '%Y-%m-%dT%H:%M:%S',
                '%Y.%m.%d %H:%M:%S.%f',
                '%Y.%m.%d %H:%M:%S',
                '%Y/%m/%d %H:%M:%S.%f',
                '%Y/%m/%d %H:%M:%S',
            ):
                try:
                    return datetime.strptime(candidate, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

        return None

    def _extract_item_timestamp(self, item: dict[str, Any], candidate_keys: tuple[str, ...]) -> datetime | None:
        for key in candidate_keys:
            ts = self._to_utc_datetime(item.get(key))
            if ts is not None:
                return ts
        return None

    def _filter_items_by_time_range(
        self,
        items: list[Any],
        start_time: datetime,
        end_time: datetime,
        *,
        candidate_keys: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        start = self._to_utc_datetime(start_time) or start_time.replace(tzinfo=timezone.utc)
        end = self._to_utc_datetime(end_time) or end_time.replace(tzinfo=timezone.utc)

        selected: list[tuple[datetime, dict[str, Any]]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            ts = self._extract_item_timestamp(raw, candidate_keys)
            if ts is None:
                continue
            if start <= ts <= end:
                selected.append((ts, raw))

        selected.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in selected]

    @staticmethod
    def _strip_trailing_pro_suffix(symbol: str) -> str:
        cleaned = (symbol or '').strip().upper()
        if not cleaned:
            return ''
        # Some brokers expose symbols with one or several trailing ".PRO" suffixes.
        return re.sub(r'(?:\.PRO)+$', '', cleaned)

    def _resolve_trade_symbol(self, symbol: str) -> str:
        return (symbol or '').strip().upper()

    @classmethod
    def _trade_symbol_candidates(cls, symbol: str) -> list[str]:
        cleaned = (symbol or '').strip()
        if not cleaned:
            return []

        candidates: list[str] = []

        def add_candidate(value: str) -> None:
            item = (value or '').strip()
            if item and item not in candidates:
                candidates.append(item)

        upper_symbol = cleaned.upper()
        if '.' in upper_symbol:
            base, suffix = upper_symbol.rsplit('.', 1)
            if base and suffix:
                # Prefer broker suffix variant with lowercase (e.g. EURUSD.pro).
                add_candidate(f'{base}.{suffix.lower()}')
                add_candidate(cleaned)
                add_candidate(f'{base}.{suffix}')
                add_candidate(base)
            else:
                add_candidate(cleaned)
                add_candidate(upper_symbol)
        else:
            add_candidate(cleaned)
            add_candidate(upper_symbol)

        stripped = cls._strip_trailing_pro_suffix(upper_symbol)
        if stripped and stripped != upper_symbol:
            add_candidate(stripped)

        forex_match = re.search(r'[A-Z]{6}', upper_symbol)
        if forex_match:
            base_symbol = forex_match.group(0)
            add_candidate(base_symbol)
            add_candidate(f'{base_symbol}.pro')
            add_candidate(f'{base_symbol}.PRO')

        return candidates

    @staticmethod
    def _market_symbol_candidates(symbol: str) -> list[str]:
        cleaned = (symbol or '').strip()
        if not cleaned:
            return []

        candidates: list[str] = []

        def add_candidate(value: str) -> None:
            item = (value or '').strip()
            if item and item not in candidates:
                candidates.append(item)

        upper_symbol = cleaned.upper()
        base_symbol = upper_symbol
        if '.' in upper_symbol:
            base_part, suffix = upper_symbol.rsplit('.', 1)
            if base_part and suffix:
                # Prefer broker suffix in lower-case first (e.g. EURUSD.pro).
                add_candidate(f'{base_part}.{suffix.lower()}')
                add_candidate(cleaned)
                add_candidate(f'{base_part}.{suffix}')
                base_symbol = base_part

        add_candidate(base_symbol)

        forex_match = re.search(r'[A-Z]{6}', base_symbol)
        if forex_match:
            add_candidate(forex_match.group(0))

        return candidates

    @classmethod
    def _normalize_market_timeframe(cls, timeframe: str) -> str:
        raw = (timeframe or '').strip().upper()
        if raw in cls._MARKET_TIMEFRAME_MAP:
            return cls._MARKET_TIMEFRAME_MAP[raw]
        # Already in SDK format (e.g. 1h, 15m, 1d)
        return raw.lower() or '1h'

    def _normalize_market_candle(self, candle: Any) -> dict[str, Any] | None:
        if not isinstance(candle, dict):
            return None

        ts = self._to_utc_datetime(candle.get('time'))
        if ts is None:
            return None

        def as_number(value: Any) -> float | None:
            if isinstance(value, (int, float)):
                num = float(value)
                return num if num == num else None
            if isinstance(value, str):
                try:
                    num = float(value)
                    return num if num == num else None
                except ValueError:
                    return None
            return None

        open_price = as_number(candle.get('open'))
        high = as_number(candle.get('high'))
        low = as_number(candle.get('low'))
        close = as_number(candle.get('close'))
        if open_price is None or high is None or low is None or close is None:
            return None

        normalized: dict[str, Any] = {
            'time': self._iso_utc(ts),
            'open': round(open_price, 8),
            'high': round(high, 8),
            'low': round(low, 8),
            'close': round(close, 8),
        }
        volume = as_number(candle.get('volume'))
        if volume is not None:
            normalized['volume'] = round(volume, 8)
        return normalized

    def _auth_headers(self) -> dict[str, str]:
        return {
            self.settings.metaapi_auth_header: self._resolve_token(),
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }

    def _get_sdk(self, region: str | None = None):
        region = region or self.settings.metaapi_region
        if not self._metaapi_cls or not self._resolve_token():
            return None
        if region not in self._sdk_by_region:
            self._sdk_by_region[region] = self._metaapi_cls(self._resolve_token(), {'region': region})
        return self._sdk_by_region[region]

    def _use_sdk_for_market_data(self) -> bool:
        return bool(self.settings.metaapi_use_sdk_for_market_data)

    @staticmethod
    async def _close_connection(connection: Any) -> None:
        if connection is None:
            return
        close = getattr(connection, 'close', None)
        if not callable(close):
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # pragma: no cover
            logger.debug('metaapi connection close ignored: %s', exc)

    async def _invoke_connection_candidates(
        self,
        connection: Any,
        candidates: list[tuple[tuple[str, ...], tuple[Any, ...], dict[str, Any]]],
    ) -> tuple[bool, Any, str | None]:
        last_exception: Exception | None = None
        for method_names, args, kwargs in candidates:
            for method_name in method_names:
                method = getattr(connection, method_name, None)
                if not callable(method):
                    continue
                try:
                    result = method(*args, **kwargs)
                    if inspect.isawaitable(result):
                        result = await result
                    return True, result, None
                except TypeError as exc:
                    # Signature mismatch: keep trying the next candidate.
                    last_exception = exc
                    continue
                except Exception as exc:  # pragma: no cover
                    return True, None, str(exc)

        if last_exception is not None:
            return False, None, str(last_exception)
        return False, None, 'No compatible SDK method found'

    def is_configured(self, account_id: str | None = None) -> bool:
        resolved = self._resolve_account_id(account_id)
        return bool(self._resolve_token() and resolved)

    async def _rest_get(self, account_id: str, candidate_paths: list[str]) -> dict[str, Any]:
        if not self.is_configured(account_id):
            return {'degraded': True, 'reason': 'MetaApi token/account not configured'}

        headers = self._auth_headers()
        base_url = self._resolve_base_url()
        timeout = max(self.settings.ollama_timeout_seconds, 30)
        errors: list[str] = []

        async with httpx.AsyncClient(timeout=timeout) as client:
            for path in candidate_paths:
                url = f'{base_url}{path}'
                try:
                    response = await client.get(url, headers=headers)
                    if response.status_code == 200:
                        return {'degraded': False, 'payload': response.json(), 'endpoint': url}
                    errors.append(f'{url} -> {response.status_code}')
                except Exception as exc:  # pragma: no cover
                    errors.append(f'{url} -> {exc}')

        return {'degraded': True, 'reason': 'REST fallback failed', 'errors': errors}

    async def _rest_post(self, account_id: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured(account_id):
            return {'degraded': True, 'executed': False, 'reason': 'MetaApi token/account not configured'}

        headers = self._auth_headers()
        base_url = self._resolve_base_url()
        url = f'{base_url}{path}'

        try:
            async with httpx.AsyncClient(timeout=max(self.settings.ollama_timeout_seconds, 30)) as client:
                response = await client.post(url, headers=headers, json=payload)
                if 200 <= response.status_code < 300:
                    result_payload: Any = response.json()
                    if path.endswith('/trade'):
                        ok, reason = self._trade_result_ok(result_payload)
                        if not ok:
                            return {
                                'degraded': True,
                                'executed': False,
                                'reason': reason or 'MetaApi trade rejected',
                                'endpoint': url,
                                'result': result_payload,
                            }
                    return {'degraded': False, 'executed': True, 'result': result_payload, 'endpoint': url}
                return {
                    'degraded': True,
                    'executed': False,
                    'reason': f'HTTP {response.status_code}',
                    'endpoint': url,
                    'raw': response.text,
                }
        except Exception as exc:  # pragma: no cover
            logger.exception('metaapi rest post failure account_id=%s path=%s', account_id, path)
            return {'degraded': True, 'executed': False, 'reason': str(exc), 'endpoint': url}

    async def _rest_get_history(
        self,
        account_id: str,
        *,
        kind: str,
        start_time: datetime,
        end_time: datetime,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        start_iso = quote(self._iso_utc(start_time), safe='')
        end_iso = quote(self._iso_utc(end_time), safe='')

        if kind == 'deals':
            paths = [
                # Official REST route family for historical deals.
                f'/users/current/accounts/{account_id}/history-deals/time/{start_iso}/{end_iso}?offset={offset}&limit={limit}',
                f'/users/current/accounts/{account_id}/history-deals/by-time-range?startTime={start_iso}&endTime={end_iso}&offset={offset}&limit={limit}',
                f'/users/current/accounts/{account_id}/history-deals/time-range?startTime={start_iso}&endTime={end_iso}&offset={offset}&limit={limit}',
                # Backward-compatible aliases used by some gateways.
                f'/users/current/accounts/{account_id}/historyDeals/time/{start_iso}/{end_iso}?offset={offset}&limit={limit}',
                f'/users/current/accounts/{account_id}/historyDeals/by-time-range?startTime={start_iso}&endTime={end_iso}&offset={offset}&limit={limit}',
                f'/users/current/accounts/{account_id}/historyDeals?startTime={start_iso}&endTime={end_iso}&offset={offset}&limit={limit}',
                f'/users/current/accounts/{account_id}/history-deals?startTime={start_iso}&endTime={end_iso}&offset={offset}&limit={limit}',
                # Legacy fallbacks kept as a last resort.
                f'/users/current/accounts/{account_id}/deals/time/{start_iso}/{end_iso}?offset={offset}&limit={limit}',
                f'/users/current/accounts/{account_id}/deals/by-time-range?startTime={start_iso}&endTime={end_iso}&offset={offset}&limit={limit}',
                f'/users/current/accounts/{account_id}/deals/time-range?startTime={start_iso}&endTime={end_iso}&offset={offset}&limit={limit}',
                f'/users/current/accounts/{account_id}/deals?startTime={start_iso}&endTime={end_iso}&offset={offset}&limit={limit}',
            ]
        else:
            paths = [
                f'/users/current/accounts/{account_id}/history-orders/by-time-range?startTime={start_iso}&endTime={end_iso}&offset={offset}&limit={limit}',
                f'/users/current/accounts/{account_id}/history-orders/time-range?startTime={start_iso}&endTime={end_iso}&offset={offset}&limit={limit}',
                f'/users/current/accounts/{account_id}/history-orders/time/{start_iso}/{end_iso}?offset={offset}&limit={limit}',
                f'/users/current/accounts/{account_id}/historyOrders/by-time-range?startTime={start_iso}&endTime={end_iso}&offset={offset}&limit={limit}',
                f'/users/current/accounts/{account_id}/historyOrders?startTime={start_iso}&endTime={end_iso}&offset={offset}&limit={limit}',
            ]

        return await self._rest_get(account_id, paths)

    @classmethod
    def _trade_result_ok(cls, payload: Any) -> tuple[bool, str | None]:
        if not isinstance(payload, dict):
            return False, 'Unexpected MetaApi trade response format'

        raw_string_code = payload.get('stringCode') or payload.get('code') or ''
        string_code = str(raw_string_code).upper().strip()
        raw_numeric_code = payload.get('numericCode')
        numeric_code: int | None = None
        if raw_numeric_code is not None:
            try:
                numeric_code = int(raw_numeric_code)
            except (TypeError, ValueError):
                numeric_code = None

        message = str(payload.get('message') or payload.get('error') or '').strip()
        message_lower = message.lower()

        if string_code in cls._SUCCESS_TRADE_STRING_CODES or numeric_code in cls._SUCCESS_TRADE_NUMERIC_CODES:
            return True, None

        if numeric_code is not None and numeric_code < 0:
            return False, message or f'MetaApi trade failed (numericCode={numeric_code})'

        if string_code:
            if any(marker in string_code for marker in cls._FAILURE_MARKERS):
                return False, message or f'MetaApi trade failed ({string_code})'
            if string_code.startswith('TRADE_RETCODE_'):
                return False, message or f'MetaApi trade not accepted ({string_code})'

        if 'unknown trade return code' in message_lower:
            return False, message

        if payload.get('success') is True:
            return True, None

        # If MetaApi does not provide explicit retcode but returns identifiers, consider it accepted.
        if any(key in payload for key in ('orderId', 'positionId', 'tradeId')):
            return True, None

        return False, message or 'MetaApi trade response did not confirm execution'

    @staticmethod
    def _validate_symbol_for_market_order(symbol: str, spec: Any) -> tuple[bool, str | None]:
        if not isinstance(spec, dict):
            return False, f'No symbol specification available for {symbol}'

        trade_mode = str(spec.get('tradeMode') or '').upper()
        if trade_mode == 'SYMBOL_TRADE_MODE_DISABLED':
            return False, f'Symbol {symbol} trading is disabled on this account (tradeMode={trade_mode})'

        allowed_types = spec.get('allowedOrderTypes')
        if isinstance(allowed_types, list) and 'SYMBOL_ORDER_MARKET' not in allowed_types:
            return False, f'Symbol {symbol} does not allow market orders (allowedOrderTypes={allowed_types})'

        return True, None

    @staticmethod
    def _is_symbol_candidate_failure(reason: str | None) -> bool:
        message = (reason or '').strip().lower()
        if not message:
            return False
        markers = (
            'unknown symbol',
            'specified symbol not found',
            'invalid symbol',
            'no symbol specification',
            'not tradable',
            'trading is disabled on this account',
            'does not allow market orders',
        )
        return any(marker in message for marker in markers)

    @staticmethod
    def _account_connection_status(account: Any) -> str:
        for attr in ('connection_status', 'connectionStatus'):
            value = getattr(account, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ''

    def _account_rpc_unavailable_reason(self, account: Any) -> str | None:
        state = str(getattr(account, 'state', '') or '').strip().upper()
        if state and state != 'DEPLOYED':
            return f'MetaApi account is not deployed (state={state}).'

        status = self._account_connection_status(account)
        normalized_status = status.upper()
        if normalized_status and (
            'DISCONNECT' in normalized_status
            or 'NOT_CONNECTED' in normalized_status
            or normalized_status in {'UNKNOWN', 'BROKER_CONNECTION_DOWN'}
        ):
            return f'MetaApi account is not connected to broker (connection_status={status}).'
        return None

    async def get_account_information(self, account_id: str | None = None, region: str | None = None) -> dict[str, Any]:
        resolved_account_id = self._resolve_account_id(account_id)
        if not resolved_account_id:
            return {'degraded': True, 'reason': 'MetaApi account id not configured'}

        sdk = self._get_sdk(region)
        sdk_skip_reason: str | None = None
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                if account.state != 'DEPLOYED':
                    await account.deploy()
                    await account.wait_connected()
                sdk_skip_reason = self._account_rpc_unavailable_reason(account)
                if sdk_skip_reason is None:
                    connection = account.get_rpc_connection()
                    await connection.connect()
                    await connection.wait_synchronized()
                    return {'degraded': False, 'account_info': await connection.get_account_information(), 'provider': 'sdk'}
                logger.info('metaapi sdk account info skipped account_id=%s reason=%s', resolved_account_id, sdk_skip_reason)
            except Exception as exc:  # pragma: no cover
                logger.warning('metaapi sdk account info failed, trying REST fallback: %s', exc)
            finally:
                await self._close_connection(connection)

        result = await self._rest_get(
            resolved_account_id,
            [
                f'/users/current/accounts/{resolved_account_id}/account-information',
                f'/users/current/accounts/{resolved_account_id}/accountInformation',
            ],
        )
        if result.get('degraded'):
            return {
                'degraded': True,
                'reason': sdk_skip_reason or result.get('reason', 'REST fallback failed'),
                'errors': result.get('errors', []),
            }
        return {'degraded': False, 'account_info': result.get('payload', {}), 'provider': 'rest', 'endpoint': result.get('endpoint')}

    async def get_positions(self, account_id: str | None = None, region: str | None = None) -> dict[str, Any]:
        resolved_account_id = self._resolve_account_id(account_id)
        if not resolved_account_id:
            return {'degraded': True, 'positions': [], 'reason': 'MetaApi account id not configured'}

        sdk = self._get_sdk(region)
        sdk_skip_reason: str | None = None
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                sdk_skip_reason = self._account_rpc_unavailable_reason(account)
                if sdk_skip_reason is None:
                    connection = account.get_rpc_connection()
                    await connection.connect()
                    await connection.wait_synchronized()
                    return {'degraded': False, 'positions': await connection.get_positions(), 'provider': 'sdk'}
                logger.info('metaapi sdk positions skipped account_id=%s reason=%s', resolved_account_id, sdk_skip_reason)
            except Exception as exc:  # pragma: no cover
                logger.warning('metaapi sdk positions failed, trying REST fallback: %s', exc)
            finally:
                await self._close_connection(connection)

        result = await self._rest_get(
            resolved_account_id,
            [
                f'/users/current/accounts/{resolved_account_id}/positions',
                f'/users/current/accounts/{resolved_account_id}/open-positions',
            ],
        )
        if result.get('degraded'):
            return {
                'degraded': True,
                'positions': [],
                'reason': sdk_skip_reason or result.get('reason', 'REST fallback failed'),
                'errors': result.get('errors', []),
            }

        payload = result.get('payload', [])
        if isinstance(payload, dict):
            payload = payload.get('positions', payload)
        return {'degraded': False, 'positions': payload if isinstance(payload, list) else [], 'provider': 'rest', 'endpoint': result.get('endpoint')}

    async def get_open_orders(self, account_id: str | None = None, region: str | None = None) -> dict[str, Any]:
        resolved_account_id = self._resolve_account_id(account_id)
        if not resolved_account_id:
            return {'degraded': True, 'open_orders': [], 'reason': 'MetaApi account id not configured'}

        sdk = self._get_sdk(region)
        sdk_skip_reason: str | None = None
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                sdk_skip_reason = self._account_rpc_unavailable_reason(account)
                if sdk_skip_reason is None:
                    connection = account.get_rpc_connection()
                    await connection.connect()
                    await connection.wait_synchronized()
                    return {'degraded': False, 'open_orders': await connection.get_orders(), 'provider': 'sdk'}
                logger.info('metaapi sdk open orders skipped account_id=%s reason=%s', resolved_account_id, sdk_skip_reason)
            except Exception as exc:  # pragma: no cover
                logger.warning('metaapi sdk open orders failed, trying REST fallback: %s', exc)
            finally:
                await self._close_connection(connection)

        result = await self._rest_get(
            resolved_account_id,
            [
                f'/users/current/accounts/{resolved_account_id}/orders',
                f'/users/current/accounts/{resolved_account_id}/open-orders',
                f'/users/current/accounts/{resolved_account_id}/openOrders',
                f'/users/current/accounts/{resolved_account_id}/pending-orders',
                f'/users/current/accounts/{resolved_account_id}/pendingOrders',
            ],
        )
        if result.get('degraded'):
            return {
                'degraded': True,
                'open_orders': [],
                'reason': sdk_skip_reason or result.get('reason', 'REST fallback failed'),
                'errors': result.get('errors', []),
            }

        payload = result.get('payload', [])
        if isinstance(payload, dict):
            if isinstance(payload.get('orders'), list):
                payload = payload.get('orders', [])
            elif isinstance(payload.get('openOrders'), list):
                payload = payload.get('openOrders', [])
            elif isinstance(payload.get('pendingOrders'), list):
                payload = payload.get('pendingOrders', [])
            elif isinstance(payload.get('items'), list):
                payload = payload.get('items', [])
            else:
                payload = []

        return {
            'degraded': False,
            'open_orders': payload if isinstance(payload, list) else [],
            'provider': 'rest',
            'endpoint': result.get('endpoint'),
        }

    async def get_deals(
        self,
        account_id: str | None = None,
        region: str | None = None,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        days: int = 30,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        resolved_account_id = self._resolve_account_id(account_id)
        if not resolved_account_id:
            return {'degraded': True, 'deals': [], 'reason': 'MetaApi account id not configured'}

        start, end = self._normalize_time_range(start_time, end_time, days)
        safe_offset = max(int(offset or 0), 0)
        safe_limit = min(max(int(limit or 1), 1), 1000)

        sdk = self._get_sdk(region)
        sdk_skip_reason: str | None = None
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                if account.state != 'DEPLOYED':
                    await account.deploy()
                    await account.wait_connected()
                sdk_skip_reason = self._account_rpc_unavailable_reason(account)
                if sdk_skip_reason is None:
                    connection = account.get_rpc_connection()
                    await connection.connect()
                    await connection.wait_synchronized()
                    payload = await connection.get_deals_by_time_range(start, end, safe_offset, safe_limit)
                    deals_payload = payload.get('deals', []) if isinstance(payload, dict) else []
                    if not isinstance(deals_payload, list):
                        deals_payload = []
                    deals_payload = self._filter_items_by_time_range(
                        deals_payload,
                        start,
                        end,
                        candidate_keys=(
                            'time',
                            'brokerTime',
                            'doneTime',
                            'updateTime',
                            'openTime',
                            'closeTime',
                        ),
                    )
                    return {
                        'degraded': False,
                        'deals': deals_payload,
                        'synchronizing': bool(payload.get('synchronizing', False)) if isinstance(payload, dict) else False,
                        'provider': 'sdk',
                        'account_id': resolved_account_id,
                        'start_time': self._iso_utc(start),
                        'end_time': self._iso_utc(end),
                        'offset': safe_offset,
                        'limit': safe_limit,
                    }
                logger.info('metaapi sdk deals skipped account_id=%s reason=%s', resolved_account_id, sdk_skip_reason)
            except Exception as exc:  # pragma: no cover
                logger.warning('metaapi sdk deals failed, trying REST fallback: %s', exc)
            finally:
                await self._close_connection(connection)

        result = await self._rest_get_history(
            resolved_account_id,
            kind='deals',
            start_time=start,
            end_time=end,
            offset=safe_offset,
            limit=safe_limit,
        )
        if result.get('degraded'):
            return {
                'degraded': True,
                'deals': [],
                'reason': sdk_skip_reason or result.get('reason', 'REST fallback failed'),
                'errors': result.get('errors', []),
                'account_id': resolved_account_id,
                'start_time': self._iso_utc(start),
                'end_time': self._iso_utc(end),
                'offset': safe_offset,
                'limit': safe_limit,
            }

        payload = result.get('payload', [])
        if isinstance(payload, dict):
            if isinstance(payload.get('deals'), list):
                payload = payload.get('deals', [])
            elif isinstance(payload.get('items'), list):
                payload = payload.get('items', [])
            else:
                payload = []

        normalized_deals = self._filter_items_by_time_range(
            payload if isinstance(payload, list) else [],
            start,
            end,
            candidate_keys=(
                'time',
                'brokerTime',
                'doneTime',
                'updateTime',
                'openTime',
                'closeTime',
            ),
        )

        return {
            'degraded': False,
            'deals': normalized_deals,
            'provider': 'rest',
            'endpoint': result.get('endpoint'),
            'account_id': resolved_account_id,
            'start_time': self._iso_utc(start),
            'end_time': self._iso_utc(end),
            'offset': safe_offset,
            'limit': safe_limit,
        }

    async def get_history_orders(
        self,
        account_id: str | None = None,
        region: str | None = None,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        days: int = 30,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        resolved_account_id = self._resolve_account_id(account_id)
        if not resolved_account_id:
            return {'degraded': True, 'history_orders': [], 'reason': 'MetaApi account id not configured'}

        start, end = self._normalize_time_range(start_time, end_time, days)
        safe_offset = max(int(offset or 0), 0)
        safe_limit = min(max(int(limit or 1), 1), 1000)

        sdk = self._get_sdk(region)
        sdk_skip_reason: str | None = None
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                if account.state != 'DEPLOYED':
                    await account.deploy()
                    await account.wait_connected()
                sdk_skip_reason = self._account_rpc_unavailable_reason(account)
                if sdk_skip_reason is None:
                    connection = account.get_rpc_connection()
                    await connection.connect()
                    await connection.wait_synchronized()
                    payload = await connection.get_history_orders_by_time_range(start, end, safe_offset, safe_limit)
                    history_orders_payload = payload.get('historyOrders', []) if isinstance(payload, dict) else []
                    if not isinstance(history_orders_payload, list):
                        history_orders_payload = []
                    history_orders_payload = self._filter_items_by_time_range(
                        history_orders_payload,
                        start,
                        end,
                        candidate_keys=(
                            'doneTime',
                            'time',
                            'brokerTime',
                            'updateTime',
                            'openTime',
                            'closeTime',
                        ),
                    )
                    return {
                        'degraded': False,
                        'history_orders': history_orders_payload,
                        'synchronizing': bool(payload.get('synchronizing', False)) if isinstance(payload, dict) else False,
                        'provider': 'sdk',
                        'account_id': resolved_account_id,
                        'start_time': self._iso_utc(start),
                        'end_time': self._iso_utc(end),
                        'offset': safe_offset,
                        'limit': safe_limit,
                    }
                logger.info('metaapi sdk history orders skipped account_id=%s reason=%s', resolved_account_id, sdk_skip_reason)
            except Exception as exc:  # pragma: no cover
                logger.warning('metaapi sdk history orders failed, trying REST fallback: %s', exc)
            finally:
                await self._close_connection(connection)

        result = await self._rest_get_history(
            resolved_account_id,
            kind='history-orders',
            start_time=start,
            end_time=end,
            offset=safe_offset,
            limit=safe_limit,
        )
        if result.get('degraded'):
            return {
                'degraded': True,
                'history_orders': [],
                'reason': sdk_skip_reason or result.get('reason', 'REST fallback failed'),
                'errors': result.get('errors', []),
                'account_id': resolved_account_id,
                'start_time': self._iso_utc(start),
                'end_time': self._iso_utc(end),
                'offset': safe_offset,
                'limit': safe_limit,
            }

        payload = result.get('payload', [])
        if isinstance(payload, dict):
            if isinstance(payload.get('historyOrders'), list):
                payload = payload.get('historyOrders', [])
            elif isinstance(payload.get('history_orders'), list):
                payload = payload.get('history_orders', [])
            elif isinstance(payload.get('items'), list):
                payload = payload.get('items', [])
            else:
                payload = []

        normalized_orders = self._filter_items_by_time_range(
            payload if isinstance(payload, list) else [],
            start,
            end,
            candidate_keys=(
                'doneTime',
                'time',
                'brokerTime',
                'updateTime',
                'openTime',
                'closeTime',
            ),
        )

        return {
            'degraded': False,
            'history_orders': normalized_orders,
            'provider': 'rest',
            'endpoint': result.get('endpoint'),
            'account_id': resolved_account_id,
            'start_time': self._iso_utc(start),
            'end_time': self._iso_utc(end),
            'offset': safe_offset,
            'limit': safe_limit,
        }

    async def get_market_candles(
        self,
        *,
        pair: str,
        timeframe: str,
        limit: int = 300,
        account_id: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        resolved_account_id = self._resolve_account_id(account_id)
        if not resolved_account_id:
            return {
                'degraded': True,
                'pair': pair,
                'timeframe': timeframe,
                'candles': [],
                'reason': 'MetaApi account id not configured',
            }

        symbol = self._resolve_trade_symbol(pair)
        normalized_timeframe = self._normalize_market_timeframe(timeframe)
        safe_limit = min(max(int(limit or 1), 1), 1000)
        symbol_candidates = self._market_symbol_candidates(symbol)
        if not symbol_candidates:
            return {
                'degraded': True,
                'pair': pair,
                'timeframe': timeframe,
                'candles': [],
                'reason': 'Invalid symbol',
            }

        sdk = self._get_sdk(region)
        if sdk:
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                if account.state != 'DEPLOYED':
                    await account.deploy()
                    await account.wait_connected()
                last_sdk_error: str | None = None
                for candidate in symbol_candidates:
                    try:
                        candles = await account.get_historical_candles(candidate, normalized_timeframe, None, safe_limit)
                        normalized_candles = [
                            normalized
                            for item in (candles or [])
                            for normalized in [self._normalize_market_candle(item)]
                            if normalized is not None
                        ]
                        if not normalized_candles:
                            # Continue trying the next candidate symbol instead of
                            # returning an empty success payload on the first 200 response.
                            last_sdk_error = f'No candles returned for symbol {candidate}'
                            continue
                        return {
                            'degraded': False,
                            'pair': pair,
                            'symbol': candidate,
                            'timeframe': timeframe,
                            'candles': normalized_candles,
                            'provider': 'sdk',
                            'account_id': resolved_account_id,
                            'requested_symbol': symbol,
                            'tried_symbols': symbol_candidates,
                        }
                    except Exception as exc:  # pragma: no cover
                        last_sdk_error = str(exc)
                if last_sdk_error:
                    logger.warning(
                        'metaapi sdk market candles failed for all symbols account_id=%s symbols=%s error=%s; trying REST fallback',
                        resolved_account_id,
                        symbol_candidates,
                        last_sdk_error,
                    )
            except Exception as exc:  # pragma: no cover
                logger.warning('metaapi sdk market candles failed, trying REST fallback: %s', exc)

        if not self._resolve_token():
            return {
                'degraded': True,
                'pair': pair,
                'timeframe': timeframe,
                'candles': [],
                'reason': 'MetaApi token not configured',
            }

        market_base_url = self.settings.metaapi_market_base_url.rstrip('/')
        headers = self._auth_headers()
        try:
            async with httpx.AsyncClient(timeout=max(self.settings.ollama_timeout_seconds, 30)) as client:
                last_response: httpx.Response | None = None
                last_url: str | None = None
                had_success_without_candles = False
                for candidate in symbol_candidates:
                    symbol_encoded = quote(candidate, safe='')
                    url = (
                        f'{market_base_url}/users/current/accounts/{resolved_account_id}/historical-market-data/symbols/'
                        f'{symbol_encoded}/timeframes/{normalized_timeframe}/candles'
                    )
                    response = await client.get(url, headers=headers, params={'limit': safe_limit})
                    last_response = response
                    last_url = url
                    if response.status_code == 200:
                        payload = response.json()
                        raw_candles = payload if isinstance(payload, list) else []
                        normalized_candles = [
                            normalized
                            for item in raw_candles
                            for normalized in [self._normalize_market_candle(item)]
                            if normalized is not None
                        ]
                        if not normalized_candles:
                            # A 200 response with no candles can happen for an invalid
                            # broker symbol variant. Keep trying fallback candidates.
                            had_success_without_candles = True
                            continue
                        return {
                            'degraded': False,
                            'pair': pair,
                            'symbol': candidate,
                            'timeframe': timeframe,
                            'candles': normalized_candles,
                            'provider': 'rest',
                            'account_id': resolved_account_id,
                            'endpoint': url,
                            'requested_symbol': symbol,
                            'tried_symbols': symbol_candidates,
                        }
                if last_response is None:
                    return {
                        'degraded': True,
                        'pair': pair,
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'candles': [],
                        'provider': 'rest',
                        'reason': 'No symbol candidate available',
                    }
                if had_success_without_candles:
                    return {
                        'degraded': True,
                        'pair': pair,
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'candles': [],
                        'provider': 'rest',
                        'reason': 'No market candles returned for symbol candidates',
                        'endpoint': last_url,
                        'tried_symbols': symbol_candidates,
                    }
                return {
                    'degraded': True,
                    'pair': pair,
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'candles': [],
                    'provider': 'rest',
                    'reason': f'HTTP {last_response.status_code}',
                    'endpoint': last_url,
                    'tried_symbols': symbol_candidates,
                }
        except Exception as exc:  # pragma: no cover
            logger.exception('metaapi rest market candles failure account_id=%s symbol=%s', resolved_account_id, symbol)
            return {
                'degraded': True,
                'pair': pair,
                'symbol': symbol,
                'timeframe': timeframe,
                'candles': [],
                'provider': 'rest',
                'reason': str(exc),
                'tried_symbols': symbol_candidates,
            }

    async def place_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        account_id: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        resolved_account_id = self._resolve_account_id(account_id)
        if not resolved_account_id:
            return {'degraded': True, 'executed': False, 'reason': 'MetaApi account id not configured'}
        requested_symbol = self._resolve_trade_symbol(symbol)
        symbol_candidates = self._trade_symbol_candidates(requested_symbol)
        if not symbol_candidates:
            return {
                'degraded': True,
                'executed': False,
                'reason': 'Invalid symbol',
                'account_id': resolved_account_id,
                'symbol': requested_symbol,
            }
        trade_symbol = symbol_candidates[0]

        sdk = self._get_sdk(region)
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                rpc_unavailable_reason = self._account_rpc_unavailable_reason(account)
                if rpc_unavailable_reason:
                    return {
                        'degraded': True,
                        'executed': False,
                        'reason': rpc_unavailable_reason,
                        'account_id': resolved_account_id,
                        'provider': 'sdk',
                        'symbol': requested_symbol,
                        'requested_symbol': requested_symbol,
                        'tried_symbols': symbol_candidates,
                    }
                connection = account.get_rpc_connection()
                await connection.connect()
                await connection.wait_synchronized()
                failed_reasons: list[str] = []
                normalized_side = side.upper()
                for candidate in symbol_candidates:
                    try:
                        symbol_spec = await connection.get_symbol_specification(candidate)
                    except Exception as exc:
                        failed_reasons.append(f'{candidate}: {exc}')
                        continue

                    tradable, reason = self._validate_symbol_for_market_order(candidate, symbol_spec)
                    if not tradable:
                        failed_reasons.append(f'{candidate}: {reason or "not tradable"}')
                        continue

                    try:
                        if normalized_side == 'BUY':
                            result = await connection.create_market_buy_order(candidate, volume, stop_loss=stop_loss, take_profit=take_profit)
                        else:
                            result = await connection.create_market_sell_order(candidate, volume, stop_loss=stop_loss, take_profit=take_profit)
                    except Exception as exc:
                        failed_reasons.append(f'{candidate}: {exc}')
                        continue

                    ok, reason = self._trade_result_ok(result)
                    if not ok:
                        failure_reason = reason or 'trade rejected'
                        failed_reasons.append(f'{candidate}: {failure_reason}')
                        if not self._is_symbol_candidate_failure(failure_reason):
                            return {
                                'degraded': True,
                                'executed': False,
                                'reason': failure_reason,
                                'account_id': resolved_account_id,
                                'provider': 'sdk',
                                'symbol': candidate,
                                'requested_symbol': requested_symbol,
                                'tried_symbols': symbol_candidates,
                                'result': result,
                            }
                        continue

                    return {
                        'degraded': False,
                        'executed': True,
                        'result': result,
                        'account_id': resolved_account_id,
                        'provider': 'sdk',
                        'symbol': candidate,
                        'requested_symbol': requested_symbol,
                        'tried_symbols': symbol_candidates,
                    }

                if failed_reasons:
                    logger.warning(
                        'metaapi sdk order rejected for all symbols account_id=%s requested=%s symbols=%s reasons=%s',
                        resolved_account_id,
                        requested_symbol,
                        symbol_candidates,
                        failed_reasons[:5],
                    )
            except Exception as exc:  # pragma: no cover
                logger.warning('metaapi sdk order failed, trying REST fallback: %s', exc)
            finally:
                await self._close_connection(connection)

        action_type = 'ORDER_TYPE_BUY' if side.upper() == 'BUY' else 'ORDER_TYPE_SELL'
        last_result: dict[str, Any] | None = None
        for candidate in symbol_candidates:
            rest_payload = {
                'actionType': action_type,
                'symbol': candidate,
                'volume': volume,
            }
            if stop_loss is not None:
                rest_payload['stopLoss'] = stop_loss
            if take_profit is not None:
                rest_payload['takeProfit'] = take_profit

            result = await self._rest_post(
                resolved_account_id,
                f'/users/current/accounts/{resolved_account_id}/trade',
                rest_payload,
            )
            if result.get('executed'):
                result['account_id'] = resolved_account_id
                result['provider'] = 'rest'
                result['symbol'] = candidate
                result['requested_symbol'] = requested_symbol
                result['tried_symbols'] = symbol_candidates
                return result

            reason = str(result.get('reason') or '').strip()
            if reason and not self._is_symbol_candidate_failure(reason):
                result['account_id'] = resolved_account_id
                result['provider'] = result.get('provider', 'rest')
                result['symbol'] = candidate
                result['requested_symbol'] = requested_symbol
                result['tried_symbols'] = symbol_candidates
                return result
            last_result = result

        return {
            'degraded': True,
            'executed': False,
            'reason': (last_result or {}).get('reason', 'MetaApi execution failed'),
            'account_id': resolved_account_id,
            'symbol': trade_symbol,
            'requested_symbol': requested_symbol,
            'tried_symbols': symbol_candidates,
            'result': (last_result or {}).get('result'),
            'endpoint': (last_result or {}).get('endpoint'),
            'raw': (last_result or {}).get('raw'),
        }

    async def modify_position(
        self,
        *,
        position_id: str,
        stop_loss: float | None,
        take_profit: float | None,
        account_id: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        resolved_account_id = self._resolve_account_id(account_id)
        if not resolved_account_id:
            return {'degraded': True, 'executed': False, 'reason': 'MetaApi account id not configured'}
        if not str(position_id or '').strip():
            return {'degraded': True, 'executed': False, 'reason': 'Position id is required'}
        if stop_loss is None and take_profit is None:
            return {'degraded': False, 'executed': False, 'reason': 'No SL/TP change requested'}

        sdk = self._get_sdk(region)
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                rpc_unavailable_reason = self._account_rpc_unavailable_reason(account)
                if rpc_unavailable_reason:
                    return {
                        'degraded': True,
                        'executed': False,
                        'reason': rpc_unavailable_reason,
                        'provider': 'sdk',
                        'account_id': resolved_account_id,
                        'position_id': str(position_id),
                    }
                connection = account.get_rpc_connection()
                await connection.connect()
                await connection.wait_synchronized()

                candidate_calls: list[tuple[tuple[str, ...], tuple[Any, ...], dict[str, Any]]] = [
                    (
                        ('modify_position', 'modifyPosition'),
                        (str(position_id),),
                        {'stop_loss': stop_loss, 'take_profit': take_profit},
                    ),
                    (
                        ('modify_position', 'modifyPosition'),
                        (str(position_id),),
                        {'stopLoss': stop_loss, 'takeProfit': take_profit},
                    ),
                    (
                        ('modify_position', 'modifyPosition'),
                        (str(position_id), stop_loss, take_profit),
                        {},
                    ),
                ]
                called, result, error = await self._invoke_connection_candidates(connection, candidate_calls)
                if called:
                    if error:
                        logger.warning('metaapi sdk modify position failed: %s', error)
                    else:
                        if isinstance(result, dict):
                            ok, reason = self._trade_result_ok(result)
                            if not ok:
                                return {
                                    'degraded': True,
                                    'executed': False,
                                    'reason': reason or 'MetaApi position modify rejected',
                                    'provider': 'sdk',
                                    'account_id': resolved_account_id,
                                    'position_id': str(position_id),
                                    'result': result,
                                }
                        return {
                            'degraded': False,
                            'executed': True,
                            'provider': 'sdk',
                            'account_id': resolved_account_id,
                            'position_id': str(position_id),
                            'result': result if isinstance(result, dict) else {'value': result},
                        }
            except Exception as exc:  # pragma: no cover
                logger.warning('metaapi sdk modify position failed, trying REST fallback: %s', exc)
            finally:
                await self._close_connection(connection)

        payloads: list[dict[str, Any]] = []
        base_payload = {
            'positionId': str(position_id),
            'position_id': str(position_id),
        }
        if stop_loss is not None:
            base_payload['stopLoss'] = stop_loss
            base_payload['stop_loss'] = stop_loss
        if take_profit is not None:
            base_payload['takeProfit'] = take_profit
            base_payload['take_profit'] = take_profit

        for action_type in ('POSITION_MODIFY', 'POSITION_MODIFY_ID', 'POSITION_MODIFY_BY_ID'):
            payloads.append({'actionType': action_type, **base_payload})

        last_result: dict[str, Any] | None = None
        for payload in payloads:
            result = await self._rest_post(
                resolved_account_id,
                f'/users/current/accounts/{resolved_account_id}/trade',
                payload,
            )
            if result.get('executed'):
                result['provider'] = 'rest'
                result['account_id'] = resolved_account_id
                result['position_id'] = str(position_id)
                return result
            last_result = result

        return {
            'degraded': True,
            'executed': False,
            'reason': (last_result or {}).get('reason', 'MetaApi position modify failed'),
            'provider': 'rest',
            'account_id': resolved_account_id,
            'position_id': str(position_id),
            'result': (last_result or {}).get('result'),
            'endpoint': (last_result or {}).get('endpoint'),
            'raw': (last_result or {}).get('raw'),
        }

    async def close_position(
        self,
        *,
        position_id: str,
        volume: float | None = None,
        side: str | None = None,
        symbol: str | None = None,
        account_id: str | None = None,
        region: str | None = None,
        allow_opposite_fallback: bool = True,
    ) -> dict[str, Any]:
        resolved_account_id = self._resolve_account_id(account_id)
        if not resolved_account_id:
            return {'degraded': True, 'executed': False, 'reason': 'MetaApi account id not configured'}
        if not str(position_id or '').strip():
            return {'degraded': True, 'executed': False, 'reason': 'Position id is required'}

        safe_volume: float | None = None
        if isinstance(volume, (int, float)):
            parsed_volume = float(volume)
            if parsed_volume > 0:
                safe_volume = parsed_volume

        sdk = self._get_sdk(region)
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                rpc_unavailable_reason = self._account_rpc_unavailable_reason(account)
                if rpc_unavailable_reason:
                    return {
                        'degraded': True,
                        'executed': False,
                        'reason': rpc_unavailable_reason,
                        'provider': 'sdk',
                        'account_id': resolved_account_id,
                        'position_id': str(position_id),
                    }
                connection = account.get_rpc_connection()
                await connection.connect()
                await connection.wait_synchronized()

                candidate_calls: list[tuple[tuple[str, ...], tuple[Any, ...], dict[str, Any]]] = [
                    (
                        ('close_position', 'closePosition'),
                        (str(position_id),),
                        {'volume': safe_volume} if safe_volume is not None else {},
                    ),
                    (
                        ('close_position', 'closePosition'),
                        (str(position_id), safe_volume),
                        {},
                    ),
                ]
                called, result, error = await self._invoke_connection_candidates(connection, candidate_calls)
                if called:
                    if error:
                        logger.warning('metaapi sdk close position failed: %s', error)
                    else:
                        if isinstance(result, dict):
                            ok, reason = self._trade_result_ok(result)
                            if not ok:
                                return {
                                    'degraded': True,
                                    'executed': False,
                                    'reason': reason or 'MetaApi position close rejected',
                                    'provider': 'sdk',
                                    'account_id': resolved_account_id,
                                    'position_id': str(position_id),
                                    'result': result,
                                }
                        return {
                            'degraded': False,
                            'executed': True,
                            'provider': 'sdk',
                            'account_id': resolved_account_id,
                            'position_id': str(position_id),
                            'result': result if isinstance(result, dict) else {'value': result},
                        }
            except Exception as exc:  # pragma: no cover
                logger.warning('metaapi sdk close position failed, trying REST fallback: %s', exc)
            finally:
                await self._close_connection(connection)

        payloads: list[dict[str, Any]] = []
        base_payload = {
            'positionId': str(position_id),
            'position_id': str(position_id),
        }
        if safe_volume is not None:
            base_payload['volume'] = safe_volume

        for action_type in ('POSITION_CLOSE_ID', 'POSITION_CLOSE_BY_ID', 'POSITION_CLOSE'):
            payloads.append({'actionType': action_type, **base_payload})

        last_result: dict[str, Any] | None = None
        for payload in payloads:
            result = await self._rest_post(
                resolved_account_id,
                f'/users/current/accounts/{resolved_account_id}/trade',
                payload,
            )
            if result.get('executed'):
                result['provider'] = 'rest'
                result['account_id'] = resolved_account_id
                result['position_id'] = str(position_id)
                return result
            last_result = result

        if allow_opposite_fallback:
            # Last fallback: submit an opposite market order for the same symbol/volume.
            resolved_side = str(side or '').strip().upper()
            resolved_symbol = str(symbol or '').strip()
            if resolved_side in {'BUY', 'SELL'} and resolved_symbol and safe_volume is not None:
                fallback_side = 'SELL' if resolved_side == 'BUY' else 'BUY'
                opposite_trade = await self.place_order(
                    symbol=resolved_symbol,
                    side=fallback_side,
                    volume=safe_volume,
                    account_id=resolved_account_id,
                    region=region,
                )
                opposite_trade['fallback_action'] = 'opposite-market-order'
                opposite_trade['position_id'] = str(position_id)
                return opposite_trade

        return {
            'degraded': True,
            'executed': False,
            'reason': (
                (last_result or {}).get('reason', 'MetaApi position close failed')
                if allow_opposite_fallback
                else (last_result or {}).get('reason', 'MetaApi position close failed (opposite fallback disabled)')
            ),
            'provider': 'rest',
            'account_id': resolved_account_id,
            'position_id': str(position_id),
            'result': (last_result or {}).get('result'),
            'endpoint': (last_result or {}).get('endpoint'),
            'raw': (last_result or {}).get('raw'),
        }
