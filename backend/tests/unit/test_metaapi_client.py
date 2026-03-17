import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.trading.metaapi_client import MetaApiClient


def test_resolve_trade_symbol_normalizes_to_upper_without_stripping_suffix() -> None:
    client = MetaApiClient()
    assert client._resolve_trade_symbol('EURUSD.PRO') == 'EURUSD.PRO'
    assert client._resolve_trade_symbol('eurusd.pro') == 'EURUSD.PRO'
    assert client._resolve_trade_symbol('BTCUSD') == 'BTCUSD'


def test_market_symbol_candidates_include_suffixless_variant_for_forex() -> None:
    candidates = MetaApiClient._market_symbol_candidates('EURUSD.PRO')
    assert candidates[0] == 'EURUSD.pro'
    assert 'EURUSD' in candidates
    assert 'EURUSD.PRO' in candidates


def test_market_symbol_candidates_keep_crypto_without_suffix() -> None:
    candidates = MetaApiClient._market_symbol_candidates('BTCUSD')
    assert candidates == ['BTCUSD']


def test_trade_symbol_candidates_prioritize_broker_suffix_and_base_variants() -> None:
    candidates = MetaApiClient._trade_symbol_candidates('EURUSD.PRO')
    assert candidates[0] == 'EURUSD.pro'
    assert 'EURUSD.PRO' in candidates
    assert 'EURUSD' in candidates


def test_is_symbol_candidate_failure_detects_symbol_related_errors() -> None:
    assert MetaApiClient._is_symbol_candidate_failure('Unknown symbol') is True
    assert MetaApiClient._is_symbol_candidate_failure('Specified symbol not found') is True
    assert MetaApiClient._is_symbol_candidate_failure('Symbol EURUSD trading is disabled on this account') is True
    assert MetaApiClient._is_symbol_candidate_failure('Market is closed') is False


def test_trade_result_ok_accepts_success_codes() -> None:
    ok, reason = MetaApiClient._trade_result_ok(
        {
            'numericCode': 10009,
            'stringCode': 'TRADE_RETCODE_DONE',
            'message': 'Request completed',
        }
    )
    assert ok is True
    assert reason is None


def test_trade_result_ok_accepts_no_changes_code() -> None:
    ok, reason = MetaApiClient._trade_result_ok(
        {
            'numericCode': 10025,
            'stringCode': 'TRADE_RETCODE_NO_CHANGES',
            'message': 'No changes',
        }
    )
    assert ok is True
    assert reason is None


def test_trade_result_ok_rejects_unknown_code() -> None:
    ok, reason = MetaApiClient._trade_result_ok(
        {
            'numericCode': -1,
            'stringCode': 'TRADE_RETCODE_UNKNOWN',
            'message': 'Unknown trade return code',
        }
    )
    assert ok is False
    assert reason is not None
    assert 'Unknown trade return code' in reason


def test_validate_symbol_for_market_order_rejects_disabled_trade_mode() -> None:
    ok, reason = MetaApiClient._validate_symbol_for_market_order(
        'EURUSD',
        {'tradeMode': 'SYMBOL_TRADE_MODE_DISABLED', 'allowedOrderTypes': ['SYMBOL_ORDER_MARKET']},
    )
    assert ok is False
    assert reason is not None
    assert 'disabled' in reason.lower()


def test_validate_symbol_for_market_order_accepts_market_tradable_symbol() -> None:
    ok, reason = MetaApiClient._validate_symbol_for_market_order(
        'EURUSD',
        {'tradeMode': 'SYMBOL_TRADE_MODE_FULL', 'allowedOrderTypes': ['SYMBOL_ORDER_MARKET']},
    )
    assert ok is True
    assert reason is None


def test_to_utc_datetime_handles_epoch_milliseconds() -> None:
    source = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
    ts_ms = int(source.timestamp() * 1000)
    parsed = MetaApiClient._to_utc_datetime(ts_ms)
    assert parsed == source


def test_to_utc_datetime_handles_mt5_dotted_datetime() -> None:
    parsed = MetaApiClient._to_utc_datetime('2026.03.12 23:03:06')
    assert parsed == datetime(2026, 3, 12, 23, 3, 6, tzinfo=timezone.utc)


def test_to_utc_datetime_handles_trailing_gmt_suffix() -> None:
    parsed = MetaApiClient._to_utc_datetime('2026.03.12 23:03:06 GMT+0200')
    assert parsed == datetime(2026, 3, 12, 23, 3, 6, tzinfo=timezone.utc)


def test_filter_items_by_time_range_applies_strict_window_and_sort() -> None:
    client = MetaApiClient()
    start = datetime(2026, 3, 10, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 11, 0, 0, 0, tzinfo=timezone.utc)

    items = [
        {'id': 'old', 'time': '2026-03-09T23:59:59Z'},
        {'id': 'in-early', 'time': '2026-03-10T10:00:00Z'},
        {'id': 'in-late', 'time': '2026-03-10T18:00:00Z'},
        {'id': 'new', 'time': '2026-03-11T00:00:01Z'},
        {'id': 'missing-ts'},
    ]

    filtered = client._filter_items_by_time_range(
        items,
        start,
        end,
        candidate_keys=('time', 'brokerTime'),
    )

    assert [item['id'] for item in filtered] == ['in-late', 'in-early']


def test_filter_items_by_time_range_accepts_mt5_broker_time_format() -> None:
    client = MetaApiClient()
    start = datetime(2026, 3, 12, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 13, 0, 0, 0, tzinfo=timezone.utc)

    items = [
        {'id': 'in', 'brokerTime': '2026.03.12 23:03:06'},
        {'id': 'out', 'brokerTime': '2026.03.13 01:00:00'},
    ]

    filtered = client._filter_items_by_time_range(
        items,
        start,
        end,
        candidate_keys=('time', 'brokerTime'),
    )

    assert [item['id'] for item in filtered] == ['in']


def test_normalize_time_range_days_zero_starts_at_utc_midnight() -> None:
    end = datetime(2026, 3, 14, 15, 30, 45, tzinfo=timezone.utc)
    start, normalized_end = MetaApiClient._normalize_time_range(None, end, 0)

    assert normalized_end == end
    assert start == datetime(2026, 3, 14, 0, 0, 0, tzinfo=timezone.utc)


def test_normalize_time_range_days_positive_uses_rolling_window() -> None:
    end = datetime(2026, 3, 14, 15, 30, 45, tzinfo=timezone.utc)
    start, normalized_end = MetaApiClient._normalize_time_range(None, end, 7)

    assert normalized_end == end
    assert start == datetime(2026, 3, 7, 15, 30, 45, tzinfo=timezone.utc)


def test_get_market_candles_sdk_skips_empty_symbol_candidate(monkeypatch) -> None:
    client = MetaApiClient()
    monkeypatch.setattr(client, '_resolve_account_id', lambda account_id=None: 'acc-1')

    class FakeAccount:
        state = 'DEPLOYED'

        async def get_historical_candles(self, symbol: str, timeframe: str, start_time, limit: int):
            if symbol == 'EURUSD':
                return [
                    {
                        'time': '2026-03-15T12:00:00Z',
                        'open': 1.1400,
                        'high': 1.1420,
                        'low': 1.1390,
                        'close': 1.1410,
                    }
                ]
            return []

    class FakeAccountApi:
        async def get_account(self, account_id: str):
            return FakeAccount()

    fake_sdk = SimpleNamespace(metatrader_account_api=FakeAccountApi())
    monkeypatch.setattr(client, '_get_sdk', lambda region=None: fake_sdk)

    result = asyncio.run(client.get_market_candles(pair='EURUSD.PRO', timeframe='H1', limit=50))

    assert result.get('degraded') is False
    assert result.get('provider') == 'sdk'
    assert result.get('symbol') == 'EURUSD'
    assert isinstance(result.get('candles'), list)
    assert len(result['candles']) == 1
    assert 'EURUSD.pro' in result.get('tried_symbols', [])


def test_get_market_candles_rest_skips_empty_symbol_candidate(monkeypatch) -> None:
    client = MetaApiClient()
    monkeypatch.setattr(client, '_resolve_account_id', lambda account_id=None: 'acc-1')
    monkeypatch.setattr(client, '_get_sdk', lambda region=None: None)
    monkeypatch.setattr(client, '_resolve_token', lambda: 'token')

    class FakeResponse:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers=None, params=None):
            if '/symbols/EURUSD.pro/' in url:
                return FakeResponse(200, [])
            if '/symbols/EURUSD/' in url:
                return FakeResponse(
                    200,
                    [
                        {
                            'time': '2026-03-15T13:00:00Z',
                            'open': 1.1400,
                            'high': 1.1430,
                            'low': 1.1390,
                            'close': 1.1420,
                        }
                    ],
                )
            return FakeResponse(500, {'message': 'error'})

    monkeypatch.setattr('app.services.trading.metaapi_client.httpx.AsyncClient', FakeAsyncClient)

    result = asyncio.run(client.get_market_candles(pair='EURUSD.PRO', timeframe='H1', limit=50))

    assert result.get('degraded') is False
    assert result.get('provider') == 'rest'
    assert result.get('symbol') == 'EURUSD'
    assert len(result.get('candles', [])) == 1


def test_get_deals_uses_sdk_even_when_market_data_flag_disabled(monkeypatch) -> None:
    client = MetaApiClient()
    client.settings.metaapi_use_sdk_for_market_data = False
    monkeypatch.setattr(client, '_resolve_account_id', lambda account_id=None: 'acc-1')

    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    class FakeConnection:
        async def connect(self):
            return None

        async def wait_synchronized(self):
            return None

        async def get_deals_by_time_range(self, start, end, offset, limit):
            return {'deals': [{'ticket': '1', 'time': now_iso}], 'synchronizing': False}

        async def close(self):
            return None

    class FakeAccount:
        state = 'DEPLOYED'

        def get_rpc_connection(self):
            return FakeConnection()

    class FakeAccountApi:
        async def get_account(self, account_id: str):
            return FakeAccount()

    fake_sdk = SimpleNamespace(metatrader_account_api=FakeAccountApi())
    monkeypatch.setattr(client, '_get_sdk', lambda region=None: fake_sdk)

    async def fail_rest(*args, **kwargs):
        raise AssertionError('REST fallback should not be called when SDK succeeds')

    monkeypatch.setattr(client, '_rest_get_history', fail_rest)

    result = asyncio.run(client.get_deals(days=1))

    assert result.get('degraded') is False
    assert result.get('provider') == 'sdk'
    assert len(result.get('deals', [])) == 1


def test_get_history_orders_uses_sdk_even_when_market_data_flag_disabled(monkeypatch) -> None:
    client = MetaApiClient()
    client.settings.metaapi_use_sdk_for_market_data = False
    monkeypatch.setattr(client, '_resolve_account_id', lambda account_id=None: 'acc-1')

    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    class FakeConnection:
        async def connect(self):
            return None

        async def wait_synchronized(self):
            return None

        async def get_history_orders_by_time_range(self, start, end, offset, limit):
            return {'historyOrders': [{'id': '1', 'doneTime': now_iso}], 'synchronizing': False}

        async def close(self):
            return None

    class FakeAccount:
        state = 'DEPLOYED'

        def get_rpc_connection(self):
            return FakeConnection()

    class FakeAccountApi:
        async def get_account(self, account_id: str):
            return FakeAccount()

    fake_sdk = SimpleNamespace(metatrader_account_api=FakeAccountApi())
    monkeypatch.setattr(client, '_get_sdk', lambda region=None: fake_sdk)

    async def fail_rest(*args, **kwargs):
        raise AssertionError('REST fallback should not be called when SDK succeeds')

    monkeypatch.setattr(client, '_rest_get_history', fail_rest)

    result = asyncio.run(client.get_history_orders(days=1))

    assert result.get('degraded') is False
    assert result.get('provider') == 'sdk'
    assert len(result.get('history_orders', [])) == 1


def test_account_rpc_unavailable_reason_detects_disconnected_broker_status() -> None:
    client = MetaApiClient()
    account = SimpleNamespace(state='DEPLOYED', connection_status='DISCONNECTED_FROM_BROKER')

    reason = client._account_rpc_unavailable_reason(account)

    assert reason is not None
    assert 'not connected to broker' in reason.lower()


def test_get_positions_skips_sdk_rpc_when_account_disconnected(monkeypatch) -> None:
    client = MetaApiClient()
    monkeypatch.setattr(client, '_resolve_account_id', lambda account_id=None: 'acc-1')

    class FakeAccount:
        state = 'DEPLOYED'
        connection_status = 'DISCONNECTED_FROM_BROKER'

        def get_rpc_connection(self):
            raise AssertionError('RPC connection should not be opened when account is disconnected')

    class FakeAccountApi:
        async def get_account(self, account_id: str):
            return FakeAccount()

    fake_sdk = SimpleNamespace(metatrader_account_api=FakeAccountApi())
    monkeypatch.setattr(client, '_get_sdk', lambda region=None: fake_sdk)

    async def fake_rest_get(*args, **kwargs):
        return {'degraded': False, 'payload': [{'id': 'p-1'}], 'endpoint': 'mock'}

    monkeypatch.setattr(client, '_rest_get', fake_rest_get)

    result = asyncio.run(client.get_positions())

    assert result.get('degraded') is False
    assert result.get('provider') == 'rest'
    assert len(result.get('positions', [])) == 1


def test_place_order_returns_clear_reason_when_account_disconnected(monkeypatch) -> None:
    client = MetaApiClient()
    monkeypatch.setattr(client, '_resolve_account_id', lambda account_id=None: 'acc-1')

    class FakeAccount:
        state = 'DEPLOYED'
        connection_status = 'DISCONNECTED_FROM_BROKER'

        def get_rpc_connection(self):
            raise AssertionError('RPC connection should not be opened when account is disconnected')

    class FakeAccountApi:
        async def get_account(self, account_id: str):
            return FakeAccount()

    fake_sdk = SimpleNamespace(metatrader_account_api=FakeAccountApi())
    monkeypatch.setattr(client, '_get_sdk', lambda region=None: fake_sdk)

    async def fail_rest_post(*args, **kwargs):
        raise AssertionError('REST order submission should not be attempted when broker is disconnected')

    monkeypatch.setattr(client, '_rest_post', fail_rest_post)

    result = asyncio.run(client.place_order(symbol='EURUSD', side='BUY', volume=0.1))

    assert result.get('executed') is False
    assert result.get('degraded') is True
    assert result.get('provider') == 'sdk'
    assert 'not connected to broker' in str(result.get('reason', '')).lower()


def test_close_position_does_not_open_opposite_order_when_fallback_disabled(monkeypatch) -> None:
    client = MetaApiClient()
    monkeypatch.setattr(client, '_resolve_account_id', lambda account_id=None: 'acc-1')
    monkeypatch.setattr(client, '_get_sdk', lambda region=None: None)

    async def fake_rest_post(*args, **kwargs):
        return {'degraded': True, 'executed': False, 'reason': 'REST close failed'}

    async def fail_place_order(*args, **kwargs):
        raise AssertionError('place_order must not be called when opposite fallback is disabled')

    monkeypatch.setattr(client, '_rest_post', fake_rest_post)
    monkeypatch.setattr(client, 'place_order', fail_place_order)

    result = asyncio.run(
        client.close_position(
            position_id='123',
            volume=0.1,
            side='BUY',
            symbol='EURUSD',
            allow_opposite_fallback=False,
        )
    )

    assert result.get('executed') is False
    assert result.get('degraded') is True
    assert str(result.get('reason', '')).strip() == 'REST close failed'
