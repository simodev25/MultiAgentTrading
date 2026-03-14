import logging
import inspect
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

    def _resolve_trade_symbol(self, symbol: str) -> str:
        base_symbol = (symbol or '').strip()
        suffix = (self.settings.metaapi_symbol_suffix or '').strip()
        if suffix and not suffix.startswith('.'):
            suffix = f'.{suffix}'
        if suffix and not base_symbol.endswith(suffix):
            return f'{base_symbol}{suffix}'
        return base_symbol

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

    async def get_account_information(self, account_id: str | None = None, region: str | None = None) -> dict[str, Any]:
        resolved_account_id = self._resolve_account_id(account_id)
        if not resolved_account_id:
            return {'degraded': True, 'reason': 'MetaApi account id not configured'}

        sdk = self._get_sdk(region)
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                if account.state != 'DEPLOYED':
                    await account.deploy()
                    await account.wait_connected()
                connection = account.get_rpc_connection()
                await connection.connect()
                await connection.wait_synchronized()
                return {'degraded': False, 'account_info': await connection.get_account_information(), 'provider': 'sdk'}
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
            return {'degraded': True, 'reason': result.get('reason', 'REST fallback failed'), 'errors': result.get('errors', [])}
        return {'degraded': False, 'account_info': result.get('payload', {}), 'provider': 'rest', 'endpoint': result.get('endpoint')}

    async def get_positions(self, account_id: str | None = None, region: str | None = None) -> dict[str, Any]:
        resolved_account_id = self._resolve_account_id(account_id)
        if not resolved_account_id:
            return {'degraded': True, 'positions': [], 'reason': 'MetaApi account id not configured'}

        sdk = self._get_sdk(region)
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                connection = account.get_rpc_connection()
                await connection.connect()
                await connection.wait_synchronized()
                return {'degraded': False, 'positions': await connection.get_positions(), 'provider': 'sdk'}
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
            return {'degraded': True, 'positions': [], 'reason': result.get('reason', 'REST fallback failed'), 'errors': result.get('errors', [])}

        payload = result.get('payload', [])
        if isinstance(payload, dict):
            payload = payload.get('positions', payload)
        return {'degraded': False, 'positions': payload if isinstance(payload, list) else [], 'provider': 'rest', 'endpoint': result.get('endpoint')}

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

        use_sdk = self._use_sdk_for_market_data()
        sdk = self._get_sdk(region) if use_sdk else None
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                if account.state != 'DEPLOYED':
                    await account.deploy()
                    await account.wait_connected()
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
                'reason': result.get('reason', 'REST fallback failed'),
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

        use_sdk = self._use_sdk_for_market_data()
        sdk = self._get_sdk(region) if use_sdk else None
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                if account.state != 'DEPLOYED':
                    await account.deploy()
                    await account.wait_connected()
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
                'reason': result.get('reason', 'REST fallback failed'),
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
        trade_symbol = self._resolve_trade_symbol(symbol)

        sdk = self._get_sdk(region)
        if sdk:
            connection = None
            try:
                account = await sdk.metatrader_account_api.get_account(resolved_account_id)
                connection = account.get_rpc_connection()
                await connection.connect()
                await connection.wait_synchronized()
                symbol_spec = await connection.get_symbol_specification(trade_symbol)
                tradable, reason = self._validate_symbol_for_market_order(trade_symbol, symbol_spec)
                if not tradable:
                    return {
                        'degraded': True,
                        'executed': False,
                        'reason': reason or f'Symbol {trade_symbol} not tradable',
                        'account_id': resolved_account_id,
                        'provider': 'sdk',
                        'symbol': trade_symbol,
                        'symbol_spec': symbol_spec,
                    }
                if side.upper() == 'BUY':
                    result = await connection.create_market_buy_order(trade_symbol, volume, stop_loss=stop_loss, take_profit=take_profit)
                else:
                    result = await connection.create_market_sell_order(trade_symbol, volume, stop_loss=stop_loss, take_profit=take_profit)
                ok, reason = self._trade_result_ok(result)
                if not ok:
                    return {
                        'degraded': True,
                        'executed': False,
                        'reason': reason or 'MetaApi SDK trade rejected',
                        'account_id': resolved_account_id,
                        'provider': 'sdk',
                        'symbol': trade_symbol,
                        'result': result,
                    }
                return {
                    'degraded': False,
                    'executed': True,
                    'result': result,
                    'account_id': resolved_account_id,
                    'provider': 'sdk',
                    'symbol': trade_symbol,
                }
            except Exception as exc:  # pragma: no cover
                logger.warning('metaapi sdk order failed, trying REST fallback: %s', exc)
            finally:
                await self._close_connection(connection)

        action_type = 'ORDER_TYPE_BUY' if side.upper() == 'BUY' else 'ORDER_TYPE_SELL'
        rest_payload = {
            'actionType': action_type,
            'symbol': trade_symbol,
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
            result['symbol'] = trade_symbol
            return result

        return {
            'degraded': True,
            'executed': False,
            'reason': result.get('reason', 'MetaApi execution failed'),
            'account_id': resolved_account_id,
            'symbol': trade_symbol,
            'endpoint': result.get('endpoint'),
            'raw': result.get('raw'),
        }
