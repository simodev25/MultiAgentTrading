from datetime import datetime, timezone

from app.services.trading.metaapi_client import MetaApiClient


def test_resolve_trade_symbol_appends_suffix_once() -> None:
    client = MetaApiClient()
    client.settings.metaapi_symbol_suffix = '.pro'
    assert client._resolve_trade_symbol('EURUSD') == 'EURUSD.pro'
    assert client._resolve_trade_symbol('EURUSD.pro') == 'EURUSD.pro'


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
