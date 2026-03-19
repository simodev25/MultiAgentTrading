import pandas as pd

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


def test_ticker_candidates_include_suffixless_fx_variant() -> None:
    candidates = YFinanceMarketProvider._ticker_candidates('EURUSD.PRO')
    assert 'EURUSD.PRO' not in candidates
    assert 'EURUSD=X' in candidates


def test_ticker_candidates_include_index_alias() -> None:
    candidates = YFinanceMarketProvider._ticker_candidates('SPX500')
    assert '^GSPC' in candidates


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


def test_get_news_context_tries_fallback_candidates(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    calls: list[str] = []

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def news(self):
            calls.append(self.symbol)
            if self.symbol == 'EURUSD=X':
                return [{'title': 'Test headline', 'publisher': 'unit', 'link': 'https://example.com', 'providerPublishTime': 1}]
            return []

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    payload = provider.get_news_context('EURUSD.PRO', limit=5)
    assert payload['degraded'] is False
    assert payload['symbol'] == 'EURUSD=X'
    assert len(payload['news']) == 1
    assert 'EURUSD=X' in calls


def test_get_news_context_uses_macro_fallback_when_pair_has_no_headlines(monkeypatch) -> None:
    provider = YFinanceMarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    calls: list[str] = []

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def news(self):
            calls.append(self.symbol)
            if self.symbol == '^GSPC':
                return [{'title': 'Risk sentiment shifts', 'publisher': 'unit', 'link': 'https://example.com/risk', 'providerPublishTime': 2}]
            return []

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    payload = provider.get_news_context('EURUSD.PRO', limit=5)
    assert payload['degraded'] is False
    assert len(payload['news']) == 1
    assert payload['news'][0]['source_symbol'] == '^GSPC'
    assert '^GSPC' in calls


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
            return [{'title': 'cached headline', 'publisher': 'unit', 'link': 'https://example.com', 'providerPublishTime': 1}]

    monkeypatch.setattr('app.services.market.yfinance_provider.yf.Ticker', _FakeTicker)

    first = provider.get_news_context('EURUSD.PRO', limit=3)
    second = provider.get_news_context('EURUSD.PRO', limit=3)

    assert first['degraded'] is False
    assert second['degraded'] is False
    assert first == second
    assert len(calls) == 1
