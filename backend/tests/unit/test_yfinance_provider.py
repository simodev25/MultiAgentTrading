import pandas as pd
import httpx
import pytest
from fnmatch import fnmatch
from datetime import datetime, timedelta, timezone

from app.services.market.yfinance_provider import YFinanceMarketProvider


def _frame(rows: int = 3) -> pd.DataFrame:
    index = pd.date_range('2026-01-01', periods=rows, freq='h')
    return pd.DataFrame(
        {
            'Open': [1.0 + idx * 0.001 for idx in range(rows)],
            'High': [1.01 + idx * 0.001 for idx in range(rows)],
            'Low': [0.99 + idx * 0.001 for idx in range(rows)],
            'Close': [1.005 + idx * 0.001 for idx in range(rows)],
            'Volume': [100 + idx for idx in range(rows)],
        },
        index=index,
    )


def _iso_hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=max(hours, 0))).isoformat().replace('+00:00', 'Z')


def _epoch_hours_ago(hours: int) -> int:
    return int((datetime.now(timezone.utc) - timedelta(hours=max(hours, 0))).timestamp())


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None, nx: bool | None = None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def delete(self, *keys: str):
        for key in keys:
            self.store.pop(key, None)
        return len(keys)

    def scan(self, cursor: int | str = 0, match: str | None = None, count: int | None = None):
        del count
        keys = list(self.store.keys())
        if match:
            keys = [key for key in keys if fnmatch(key, match)]
        return 0, keys


@pytest.fixture(autouse=True)
def _use_env_defaults_for_runtime_connector_keys(monkeypatch):
    monkeypatch.setattr(
        'app.services.market.yfinance_provider.RuntimeConnectorSettings.get_string',
        lambda _connector_name, _keys, **kwargs: str(kwargs.get('default') or '').strip(),
    )


def test_ticker_candidates_include_suffixless_fx_variant() -> None:
    candidates = YFinanceMarketProvider._ticker_candidates('EURUSD.PRO')
    assert 'EURUSD.PRO' not in candidates
    assert 'EURUSD=X' in candidates


def test_ticker_candidates_map_crypto_pairs_to_spot_symbols() -> None:
    candidates = YFinanceMarketProvider._ticker_candidates('LTCUSD')
    assert candidates[0] == 'LTC-USD'
    assert 'LTCUSD=X' not in candidates


def test_normalize_pair_strips_broker_suffix_for_non_fx_symbols() -> None:
    assert YFinanceMarketProvider._normalize_pair('AAPL.PRO') == 'AAPL'


def test_ticker_candidates_include_index_alias() -> None:
    candidates = YFinanceMarketProvider._ticker_candidates('SPX500')
    assert '^GSPC' in candidates


def test_news_symbol_candidates_for_crypto_avoid_fx_dollar_fallbacks() -> None:
    candidates = YFinanceMarketProvider._news_symbol_candidates('DOTUSD')
    assert candidates[0] == 'DOT-USD'
    assert 'DX-Y.NYB' not in candidates
    assert '^DXY' not in candidates


def test_normalize_article_item_maps_same_usd_story_by_base_quote_role() -> None:
    eurusd = YFinanceMarketProvider._normalize_article_item(
        provider='newsapi',
        pair='EURUSD',
        title='Dollar falls after soft US CPI as Fed turns dovish',
        summary='USD weakens as lower Treasury yields weigh on the greenback.',
        url='https://example.com/eurusd-story',
        published_at=_iso_hours_ago(1),
        source_name='Reuters',
    )
    usdjpy = YFinanceMarketProvider._normalize_article_item(
        provider='newsapi',
        pair='USDJPY',
        title='Dollar falls after soft US CPI as Fed turns dovish',
        summary='USD weakens as lower Treasury yields weigh on the greenback.',
        url='https://example.com/usdjpy-story',
        published_at=_iso_hours_ago(1),
        source_name='Reuters',
    )

    assert eurusd is not None
    assert usdjpy is not None
    assert eurusd['impact_on_quote'] == 'weakening'
    assert eurusd['pair_directional_effect'] == 'bullish'
    assert usdjpy['impact_on_base'] == 'weakening'
    assert usdjpy['pair_directional_effect'] == 'bearish'


def test_normalize_article_item_handles_cross_pairs_generically() -> None:
    eurgbp = YFinanceMarketProvider._normalize_article_item(
        provider='newsapi',
        pair='EURGBP',
        title='Sterling rises after hawkish Bank of England remarks',
        summary='GBP strengthens as markets price tighter UK policy.',
        url='https://example.com/eurgbp-story',
        published_at=_iso_hours_ago(2),
        source_name='Bloomberg',
    )
    audnzd = YFinanceMarketProvider._normalize_article_item(
        provider='newsapi',
        pair='AUDNZD',
        title='Aussie rallies after hawkish RBA surprise',
        summary='AUD strengthens as traders price a higher rates path.',
        url='https://example.com/audnzd-story',
        published_at=_iso_hours_ago(2),
        source_name='Bloomberg',
    )

    assert eurgbp is not None
    assert audnzd is not None
    assert eurgbp['impact_on_quote'] == 'strengthening'
    assert eurgbp['pair_directional_effect'] == 'bearish'
    assert audnzd['impact_on_base'] == 'strengthening'
    assert audnzd['pair_directional_effect'] == 'bullish'


def test_get_historical_candles_tries_fallback_candidates(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    calls: list[str] = []

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **kwargs):
            calls.append(self.symbol)
            if self.symbol == 'EURUSD=X':
                return _frame()
            return pd.DataFrame()

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    frame = provider.get_historical_candles('EURUSD.PRO', 'H1', '2026-01-01', '2026-01-02')
    assert not frame.empty
    assert 'EURUSD=X' in calls


def test_get_market_snapshot_exposes_instrument_resolution_trace(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **kwargs):
            return _frame(rows=60)

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    snapshot = provider.get_market_snapshot('EURUSD.PRO', 'H1')

    assert snapshot['degraded'] is False
    assert snapshot['symbol'] == 'EURUSD=X'
    assert snapshot['instrument']['canonical_symbol'] == 'EURUSD'
    assert snapshot['instrument']['asset_class'] == 'forex'
    assert snapshot['provider_resolution']['provider_symbol'] == 'EURUSD=X'


def test_get_news_context_tries_fallback_candidates(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': True, 'priority': 100},
        'newsapi': {'enabled': False},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
    }
    calls: list[str] = []

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def news(self):
            calls.append(self.symbol)
            if self.symbol == 'EURUSD=X':
                return [{'title': 'Test headline', 'publisher': 'unit', 'link': 'https://example.com', 'providerPublishTime': _epoch_hours_ago(1)}]
            return []

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    payload = provider.get_news_context('EURUSD.PRO', limit=5)
    assert payload['degraded'] is False
    assert payload['symbol'] == 'EURUSD=X'
    assert payload['instrument']['canonical_symbol'] == 'EURUSD'
    assert payload['provider_resolution']['provider_symbol'] == 'EURUSD=X'
    assert any(item.get('symbol') == 'EURUSD=X' for item in payload['news_candidates'])
    assert len(payload['news']) == 1
    assert 'EURUSD=X' in calls


def test_get_news_context_supports_nested_yfinance_news_schema(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': True, 'priority': 100},
        'newsapi': {'enabled': False},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
    }

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def news(self):
            if self.symbol == 'AAPL':
                return [
                    {
                        'id': 'abc',
                        'content': {
                            'title': 'Apple expands AI tooling',
                            'provider': {'displayName': 'Yahoo Finance'},
                            'canonicalUrl': {'url': 'https://example.com/apple-ai'},
                            'summary': 'Apple announced new AI features.',
                            'pubDate': _iso_hours_ago(1),
                        },
                    }
                ]
            return []

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    payload = provider.get_news_context('AAPL', limit=5)
    assert payload['degraded'] is False
    assert len(payload['news']) == 1
    assert payload['news'][0]['title'] == 'Apple expands AI tooling'
    assert payload['news'][0]['publisher'] == 'Yahoo Finance'
    assert payload['news'][0]['link'] == 'https://example.com/apple-ai'
    assert payload['news'][0]['summary'] == 'Apple announced new AI features.'


def test_get_news_context_prefers_preview_link_over_canonical_when_available(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': True, 'priority': 100},
        'newsapi': {'enabled': False},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
    }

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def news(self):
            if self.symbol == 'EURUSD=X':
                return [
                    {
                        'id': 'mismatch',
                        'content': {
                            'title': 'Sterling Rises After Bank of England Votes Unanimously to Hold Rates',
                            'provider': {'displayName': 'The Wall Street Journal'},
                            'canonicalUrl': {'url': 'https://example.com/unrelated-wsj-url'},
                            'previewUrl': 'https://finance.yahoo.com/m/correct-preview-url',
                            'summary': 'Sterling rose after BOE held rates.',
                            'pubDate': _iso_hours_ago(1),
                        },
                    }
                ]
            return []

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    payload = provider.get_news_context('EURUSD.PRO', limit=5)
    assert payload['degraded'] is False
    assert len(payload['news']) == 1
    assert payload['news'][0]['title'].startswith('Sterling Rises')
    assert payload['news'][0]['link'] == 'https://finance.yahoo.com/m/correct-preview-url'
    assert payload['news'][0]['summary'] == 'Sterling rose after BOE held rates.'


def test_get_news_context_uses_macro_fallback_when_pair_has_no_headlines(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': True, 'priority': 100},
        'newsapi': {'enabled': False},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
    }
    calls: list[str] = []

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def news(self):
            calls.append(self.symbol)
            if self.symbol == '^GSPC':
                return [{'title': 'Risk sentiment shifts', 'publisher': 'unit', 'link': 'https://example.com/risk', 'providerPublishTime': _epoch_hours_ago(2)}]
            return []

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    payload = provider.get_news_context('EURUSD.PRO', limit=5)
    assert payload['degraded'] is False
    assert len(payload['news']) == 1
    assert payload['news'][0]['source_symbol'] == '^GSPC'
    assert '^GSPC' in calls


def test_get_news_context_yahoo_filters_out_stale_items_by_lookback(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': True, 'priority': 100, 'lookback_hours': 48},
        'newsapi': {'enabled': False},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
    }

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def news(self):
            if self.symbol == 'EURUSD=X':
                return [
                    {
                        'title': 'Fresh headline',
                        'publisher': 'unit',
                        'link': 'https://example.com/fresh',
                        'providerPublishTime': _epoch_hours_ago(2),
                    },
                    {
                        'title': 'Stale headline',
                        'publisher': 'unit',
                        'link': 'https://example.com/stale',
                        'providerPublishTime': _epoch_hours_ago(24 * 40),
                    },
                ]
            return []

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    payload = provider.get_news_context('EURUSD.PRO', limit=5)
    assert payload['degraded'] is False
    assert payload['fetch_status'] == 'ok'
    assert len(payload['news']) == 1
    assert payload['news'][0]['title'] == 'Fresh headline'


def test_get_market_snapshot_uses_cache(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = True
    provider.settings.yfinance_snapshot_cache_min_ttl_seconds = 10
    provider.settings.yfinance_snapshot_cache_max_ttl_seconds = 10
    provider._redis = _FakeRedis()
    provider._redis_unavailable_until = 0.0
    monkeypatch.setattr(provider, '_timeframe_cache_bucket', lambda timeframe: 42)

    calls: list[str] = []

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **kwargs):
            calls.append(self.symbol)
            return _frame(rows=120)

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    first = provider.get_market_snapshot('EURUSD.PRO', 'H1')
    second = provider.get_market_snapshot('EURUSD.PRO', 'H1')

    assert first['degraded'] is False
    assert second['degraded'] is False
    assert first == second
    assert len(calls) == 1


def test_get_market_snapshot_refreshes_cache_when_bucket_changes(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = True
    provider.settings.yfinance_snapshot_cache_min_ttl_seconds = 10
    provider.settings.yfinance_snapshot_cache_max_ttl_seconds = 10
    provider._redis = _FakeRedis()
    provider._redis_unavailable_until = 0.0

    calls = {'count': 0}
    buckets = iter([100, 100, 101])
    monkeypatch.setattr(provider, '_timeframe_cache_bucket', lambda timeframe: next(buckets))

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **kwargs):
            calls['count'] += 1
            frame = _frame(rows=120)
            frame['Close'] = frame['Close'] + (calls['count'] - 1) * 0.1
            return frame

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    first = provider.get_market_snapshot('EURUSD.PRO', 'H1')
    second = provider.get_market_snapshot('EURUSD.PRO', 'H1')
    third = provider.get_market_snapshot('EURUSD.PRO', 'H1')

    assert calls['count'] == 2
    assert first == second
    assert third != second


def test_get_historical_candles_uses_cache(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = True
    provider.settings.yfinance_historical_cache_ttl_seconds = 600
    provider._redis = _FakeRedis()
    provider._redis_unavailable_until = 0.0

    calls: list[str] = []

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **kwargs):
            calls.append(self.symbol)
            return _frame(rows=24)

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    first = provider.get_historical_candles('EURUSD.PRO', 'H1', '2026-01-01', '2026-01-02')
    second = provider.get_historical_candles('EURUSD.PRO', 'H1', '2026-01-01', '2026-01-02')

    assert not first.empty
    assert not second.empty
    assert len(calls) == 1
    assert list(first.columns) == list(second.columns)
    assert len(first) == len(second)


def test_get_news_context_uses_cache(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = True
    provider.settings.yfinance_news_cache_ttl_seconds = 120
    provider._redis = _FakeRedis()
    provider._redis_unavailable_until = 0.0

    calls: list[str] = []

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def news(self):
            calls.append(self.symbol)
            return [{'title': 'cached headline', 'publisher': 'unit', 'link': 'https://example.com', 'providerPublishTime': _epoch_hours_ago(1)}]

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    first = provider.get_news_context('EURUSD.PRO', limit=3)
    second = provider.get_news_context('EURUSD.PRO', limit=3)

    assert first['degraded'] is False
    assert second['degraded'] is False
    assert first == second
    assert len(calls) == 1


def test_provider_api_key_prefers_runtime_connector_settings(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.newsapi_api_key = 'env-newsapi-key'
    monkeypatch.setattr(
        'app.services.market.yfinance_provider.RuntimeConnectorSettings.get_string',
        lambda _connector_name, keys, **_kwargs: 'runtime-newsapi-key' if 'NEWSAPI_API_KEY' in keys else '',
    )

    api_key = provider._provider_api_key(
        'newsapi',
        {'api_key_env': 'NEWSAPI_API_KEY'},
    )
    assert api_key == 'runtime-newsapi-key'


def test_news_providers_config_applies_runtime_provider_enabled_overrides(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.news_providers = {
        'newsapi': {'enabled': True},
        'finnhub': {'enabled': False},
    }
    monkeypatch.setattr(
        'app.services.market.yfinance_provider.RuntimeConnectorSettings.settings',
        lambda _connector_name: {'news_providers': {'newsapi': {'enabled': False}, 'finnhub': {'enabled': True}}},
    )

    resolved = provider._news_providers_config()
    assert resolved['newsapi']['enabled'] is False
    assert resolved['finnhub']['enabled'] is True


def test_get_news_context_marks_unavailable_provider_when_credentials_missing() -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': False},
        'newsapi': {'enabled': True, 'priority': 90, 'api_key_env': 'NEWSAPI_API_KEY'},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
    }
    provider.settings.newsapi_api_key = ''

    payload = provider.get_news_context('EURUSD.PRO', limit=5)

    assert payload['degraded'] is False
    assert payload['fetch_status'] == 'empty'
    assert payload['provider_status_compact']['newsapi'] == 'unavailable'
    assert payload['news'] == []
    assert payload['macro_events'] == []


def test_get_news_context_returns_error_when_all_callable_providers_fail(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': False},
        'newsapi': {'enabled': True, 'priority': 90, 'api_key_env': 'NEWSAPI_API_KEY'},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
    }
    provider.settings.newsapi_api_key = 'unit-test-key'

    class _ErrorClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, *args, **kwargs):
            raise httpx.ReadTimeout('timeout')

    monkeypatch.setattr('app.services.market.yfinance_provider.httpx.Client', _ErrorClient)

    payload = provider.get_news_context('EURUSD.PRO', limit=5)

    assert payload['degraded'] is True
    assert payload['fetch_status'] == 'error'
    assert payload['provider_status_compact']['newsapi'] == 'error'
    assert payload['news'] == []
    assert payload['macro_events'] == []


def test_get_news_context_marks_alphavantage_unavailable_when_rate_limited(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': False},
        'newsapi': {'enabled': False},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': True, 'priority': 60, 'api_key_env': 'ALPHAVANTAGE_API_KEY'},
    }
    provider.settings.alphavantage_api_key = 'unit-test-key'

    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.content = b'{}'

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, *_args, **_kwargs):
            return _FakeResponse(
                {
                    'Note': 'Please consider spreading out your free API requests more sparingly (1 request per second).',
                    'feed': [],
                }
            )

    monkeypatch.setattr('app.services.market.yfinance_provider.httpx.Client', _FakeClient)

    payload = provider.get_news_context('EURUSD.PRO', limit=5)

    assert payload['degraded'] is False
    assert payload['fetch_status'] == 'empty'
    assert payload['provider_status_compact']['alphavantage'] == 'unavailable'
    provider_status = payload.get('provider_status', {})
    assert isinstance(provider_status, dict)
    assert provider_status.get('alphavantage', {}).get('error') == 'rate_limited'
    assert payload['news'] == []
    assert payload['macro_events'] == []


def test_get_news_context_returns_partial_when_one_provider_fails(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': True, 'priority': 100},
        'newsapi': {'enabled': True, 'priority': 90, 'api_key_env': 'NEWSAPI_API_KEY'},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
    }
    provider.settings.newsapi_api_key = 'unit-test-key'

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def news(self):
            if self.symbol == 'EURUSD=X':
                return [
                    {
                        'title': 'Dollar falls as risk appetite improves',
                        'publisher': 'Unit',
                        'link': 'https://example.com/news-1',
                        'providerPublishTime': _iso_hours_ago(2),
                    }
                ]
            return []

    class _ErrorClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, *args, **kwargs):
            raise httpx.ReadTimeout('timeout')

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)
    monkeypatch.setattr('app.services.market.yfinance_provider.httpx.Client', _ErrorClient)

    payload = provider.get_news_context('EURUSD.PRO', limit=5)

    assert payload['degraded'] is False
    assert payload['fetch_status'] == 'partial'
    assert payload['provider_status_compact']['yahoo_finance'] == 'ok'
    assert payload['provider_status_compact']['newsapi'] == 'error'
    assert len(payload['news']) == 1


def test_get_news_context_deduplicates_same_item_from_multiple_providers(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': True, 'priority': 100},
        'newsapi': {'enabled': True, 'priority': 90, 'api_key_env': 'NEWSAPI_API_KEY'},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
    }
    provider.settings.newsapi_api_key = 'unit-test-key'
    published_at = _iso_hours_ago(2)

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def news(self):
            if self.symbol == 'EURUSD=X':
                return [
                        {
                            'title': 'Dollar falls as risk appetite improves',
                            'publisher': 'Reuters',
                            'link': 'https://example.com/duplicate',
                            'providerPublishTime': published_at,
                        }
                    ]
                return []

    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.content = b'{}'

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, *args, **kwargs):
            return _FakeResponse(
                {
                    'articles': [
                        {
                            'title': 'Dollar falls as risk appetite improves',
                            'description': 'duplicate article',
                            'url': 'https://example.com/duplicate',
                            'publishedAt': published_at,
                            'source': {'name': 'Reuters'},
                        }
                    ]
                }
            )

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)
    monkeypatch.setattr('app.services.market.yfinance_provider.httpx.Client', _FakeClient)

    payload = provider.get_news_context('EURUSD.PRO', limit=5)

    assert payload['degraded'] is False
    assert payload['fetch_status'] == 'ok'
    assert payload['provider_status_compact']['yahoo_finance'] == 'ok'
    assert payload['provider_status_compact']['newsapi'] == 'ok'
    assert len(payload['news']) == 1


def test_get_news_context_newsapi_uses_header_api_key(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': False},
        'newsapi': {'enabled': True, 'priority': 90, 'api_key_env': 'NEWSAPI_API_KEY'},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
    }
    provider.settings.newsapi_api_key = 'unit-test-key'
    captured: dict[str, object] = {}

    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.content = b'{}'

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url: str, **kwargs):
            captured['url'] = url
            captured['params'] = kwargs.get('params')
            captured['headers'] = kwargs.get('headers')
            return _FakeResponse(
                {
                    'articles': [
                        {
                            'title': 'Dollar falls as risk appetite improves',
                            'description': 'desc',
                            'url': 'https://example.com/newsapi',
                            'publishedAt': _iso_hours_ago(2),
                            'source': {'name': 'Reuters'},
                        }
                    ]
                }
            )

    monkeypatch.setattr('app.services.market.yfinance_provider.httpx.Client', _FakeClient)

    payload = provider.get_news_context('EURUSD.PRO', limit=5)

    assert payload['fetch_status'] == 'ok'
    assert payload['provider_status_compact']['newsapi'] == 'ok'
    params = captured.get('params')
    headers = captured.get('headers')
    assert isinstance(params, dict)
    assert isinstance(headers, dict)
    assert 'apiKey' not in params
    assert headers.get('X-Api-Key') == 'unit-test-key'


def test_clear_news_cache_deletes_only_news_keys() -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = True
    provider._redis = _FakeRedis()
    provider._redis_unavailable_until = 0.0
    assert provider._redis is not None

    news_key = provider._cache_key('news', 'EURUSD.PRO', 5)
    lock_key = f'{news_key}:lock'
    other_key = provider._cache_key('snapshot', 'EURUSD.PRO', 'M15', 1)
    provider._redis.set(news_key, '{"ok":true}')
    provider._redis.set(lock_key, 'token')
    provider._redis.set(other_key, '{"keep":true}')

    deleted = provider.clear_news_cache()

    assert deleted == 2
    assert provider._redis.get(news_key) is None
    assert provider._redis.get(lock_key) is None
    assert provider._redis.get(other_key) is not None


def test_test_news_provider_returns_disabled_for_disabled_provider() -> None:
    provider = YFinanceMarketProvider()
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': True, 'priority': 100},
        'newsapi': {'enabled': False},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
    }

    result = provider.test_news_provider('newsapi', pair='EURUSD.PRO', max_items=5)

    assert result['provider'] == 'newsapi'
    assert result['enabled'] is False
    assert result['status'] == 'disabled'
    assert result['count'] == 0


def test_test_news_provider_returns_ok_for_newsapi(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': False},
        'newsapi': {'enabled': True, 'priority': 90, 'api_key_env': 'NEWSAPI_API_KEY'},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
    }
    provider.settings.newsapi_api_key = 'unit-test-key'

    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.content = b'{}'

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, *args, **kwargs):
            return _FakeResponse(
                {
                    'articles': [
                        {
                            'title': 'Dollar falls as risk appetite improves',
                            'description': 'desc',
                            'url': 'https://example.com/newsapi',
                            'publishedAt': _iso_hours_ago(2),
                            'source': {'name': 'Reuters'},
                        }
                    ]
                }
            )

    monkeypatch.setattr('app.services.market.yfinance_provider.httpx.Client', _FakeClient)

    result = provider.test_news_provider('newsapi', pair='EURUSD.PRO', max_items=5)

    assert result['provider'] == 'newsapi'
    assert result['enabled'] is True
    assert result['status'] == 'ok'
    assert result['count'] == 1
    assert isinstance(result.get('items'), list)


def test_test_news_provider_alphavantage_uses_sanitized_tickers(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': False},
        'newsapi': {'enabled': False},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': True, 'priority': 60, 'api_key_env': 'ALPHAVANTAGE_API_KEY'},
    }
    provider.settings.alphavantage_api_key = 'unit-test-key'
    captured: dict[str, object] = {}

    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.content = b'{}'

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, _url: str, **kwargs):
            params = kwargs.get('params') or {}
            captured['params'] = params
            return _FakeResponse(
                {
                    'feed': [
                        {
                            'title': 'Dollar weakens after central bank comments',
                            'summary': 'Markets repriced rate expectations.',
                            'url': 'https://example.com/alpha-news',
                            'time_published': '20260319T210000',
                            'source': 'Reuters',
                            'overall_sentiment_label': 'Somewhat-Bearish',
                        }
                    ]
                }
            )

    monkeypatch.setattr('app.services.market.yfinance_provider.httpx.Client', _FakeClient)

    result = provider.test_news_provider('alphavantage', pair='EURUSD.PRO', max_items=5)

    assert result['provider'] == 'alphavantage'
    assert result['enabled'] is True
    assert result['status'] == 'ok'
    assert result['count'] == 1
    assert isinstance(result.get('meta'), dict)
    params = captured.get('params')
    assert isinstance(params, dict)
    tickers = str(params.get('tickers') or '')
    assert '.PRO' not in tickers
    assert 'DX-Y.NYB' not in tickers
    assert 'EURUSD' in tickers


def test_test_news_provider_alphavantage_returns_unavailable_on_api_rate_limit(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': False},
        'newsapi': {'enabled': False},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': True, 'priority': 60, 'api_key_env': 'ALPHAVANTAGE_API_KEY'},
    }
    provider.settings.alphavantage_api_key = 'unit-test-key'

    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.content = b'{}'

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, *_args, **_kwargs):
            return _FakeResponse({'Note': 'API call frequency is 5 requests per minute'})

    monkeypatch.setattr('app.services.market.yfinance_provider.httpx.Client', _FakeClient)

    result = provider.test_news_provider('alphavantage', pair='EURUSD.PRO', max_items=5)

    assert result['provider'] == 'alphavantage'
    assert result['enabled'] is True
    assert result['status'] == 'unavailable'
    assert result.get('error') == 'rate_limited'
    assert isinstance(result.get('meta'), dict)
    assert 'frequency' in str((result.get('meta') or {}).get('message', '')).lower()
