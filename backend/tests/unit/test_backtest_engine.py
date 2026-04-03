import numpy as np
import pandas as pd
import pytest

from app.services.backtest.engine import BacktestEngine
from app.services.strategy.signal_engine import compute_strategy_overlays_and_signals


def _candles(close_values: list[float]) -> list[dict]:
    start = pd.Timestamp('2025-01-01T00:00:00Z')
    return [
        {
            'time': (start + pd.Timedelta(hours=idx)).isoformat().replace('+00:00', 'Z'),
            'open': value,
            'high': value + 0.001,
            'low': value - 0.001,
            'close': value,
            'volume': 1000,
        }
        for idx, value in enumerate(close_values)
    ]


def _frame_from_candles(candles: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            'Open': [candle['open'] for candle in candles],
            'High': [candle['high'] for candle in candles],
            'Low': [candle['low'] for candle in candles],
            'Close': [candle['close'] for candle in candles],
            'Volume': [candle['volume'] for candle in candles],
        },
        index=pd.DatetimeIndex([candle['time'] for candle in candles]),
    )


def _expected_signal_series_from_shared_engine(
    candles: list[dict],
    template: str,
    params: dict,
) -> pd.Series:
    shared = compute_strategy_overlays_and_signals(candles, template, params)
    signal_by_time = {
        signal['time']: 1 if signal['side'] == 'BUY' else -1
        for signal in shared['signals']
    }

    values: list[int] = []
    current = 0
    for candle in candles:
        next_value = signal_by_time.get(candle['time'])
        if next_value is not None:
            current = next_value
        values.append(current)

    return pd.Series(values, index=pd.DatetimeIndex([candle['time'] for candle in candles]), dtype='int64')


def test_backtest_engine_returns_metrics(monkeypatch) -> None:
    index = pd.date_range('2025-01-01', periods=180, freq='D')
    trend = np.linspace(1.05, 1.20, len(index))
    noise = 0.002 * np.sin(np.arange(len(index)))
    close = trend + noise

    frame = pd.DataFrame(
        {
            'Open': close,
            'High': close + 0.003,
            'Low': close - 0.003,
            'Close': close,
            'Volume': np.full(len(index), 1000),
        },
        index=index,
    )

    monkeypatch.setattr('app.services.backtest.engine.BacktestEngine._fetch_backtest_candles', lambda *args, **kwargs: frame)

    engine = BacktestEngine()
    result = engine.run('EURUSD', 'D1', '2025-01-01', '2025-06-30', strategy='ema_rsi')

    assert 'total_return_pct' in result.metrics
    assert 'sharpe_ratio' in result.metrics
    assert result.metrics.get('strategy') == 'ema_rsi'
    assert result.metrics.get('workflow_source') == 'BacktestEngine.ema_rsi'
    assert isinstance(result.equity_curve, list)
    assert len(result.equity_curve) > 0


def test_backtest_engine_strategy_normalization() -> None:
    assert BacktestEngine.normalize_strategy('ema_rsi') == 'ema_rsi'
    assert BacktestEngine.normalize_strategy('ema-rsi') == 'ema_rsi'
    assert BacktestEngine.normalize_strategy('default') == 'ema_rsi'
    # Reject unknown strategies
    assert BacktestEngine.normalize_strategy('unknown_xyz') is None


def test_backtest_engine_resolves_strategy_params() -> None:
    explicit = {'ema_fast': 7, 'ema_slow': 14}
    fallback = {'ema_fast': 99, 'ema_slow': 200}

    assert BacktestEngine._resolve_strategy_params(
        agent_config={'strategy_params': fallback},
        strategy_params=explicit,
    ) == explicit
    assert BacktestEngine._resolve_strategy_params(
        agent_config={'strategy_params': fallback},
        strategy_params=None,
    ) == fallback


def test_backtest_engine_threads_explicit_strategy_params(monkeypatch) -> None:
    index = pd.date_range('2025-01-01', periods=60, freq='D')
    close = np.linspace(1.05, 1.20, len(index))

    frame = pd.DataFrame(
        {
            'Open': close,
            'High': close + 0.003,
            'Low': close - 0.003,
            'Close': close,
            'Volume': np.full(len(index), 1000),
        },
        index=index,
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        'app.services.backtest.engine.BacktestEngine._fetch_backtest_candles',
        lambda *args, **kwargs: frame,
    )
    monkeypatch.setattr(
        'app.services.backtest.engine.BacktestEngine._prepare_indicator_frame',
        lambda self, prepared_frame: prepared_frame,
    )

    def fake_signal_series(self, prepared_frame, strategy, params=None, target_index=None):  # noqa: ANN001
        captured['params'] = params
        captured['strategy'] = strategy
        captured['target_index'] = target_index
        index = target_index if target_index is not None else prepared_frame.index
        return pd.Series(np.zeros(len(index), dtype=int), index=index)

    monkeypatch.setattr('app.services.backtest.engine.BacktestEngine._signal_series_for_strategy', fake_signal_series)

    engine = BacktestEngine()
    engine.run(
        'EURUSD',
        'D1',
        '2025-01-01',
        '2025-03-01',
        strategy='ema_crossover',
        strategy_params={'ema_fast': 7, 'ema_slow': 14, 'rsi_filter': 25},
    )

    assert captured['params'] == {'ema_fast': 7, 'ema_slow': 14, 'rsi_filter': 25}
    assert captured['strategy'] == 'ema_crossover'
    assert captured['target_index'] is not None


def test_backtest_engine_threads_agent_config_strategy_params(monkeypatch) -> None:
    index = pd.date_range('2025-01-01', periods=60, freq='D')
    close = np.linspace(1.05, 1.20, len(index))

    frame = pd.DataFrame(
        {
            'Open': close,
            'High': close + 0.003,
            'Low': close - 0.003,
            'Close': close,
            'Volume': np.full(len(index), 1000),
        },
        index=index,
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        'app.services.backtest.engine.BacktestEngine._fetch_backtest_candles',
        lambda *args, **kwargs: frame,
    )
    monkeypatch.setattr(
        'app.services.backtest.engine.BacktestEngine._prepare_indicator_frame',
        lambda self, prepared_frame: prepared_frame,
    )

    def fake_signal_series(self, prepared_frame, strategy, params=None, target_index=None):  # noqa: ANN001
        captured['params'] = params
        captured['strategy'] = strategy
        captured['target_index'] = target_index
        index = target_index if target_index is not None else prepared_frame.index
        return pd.Series(np.zeros(len(index), dtype=int), index=index)

    monkeypatch.setattr('app.services.backtest.engine.BacktestEngine._signal_series_for_strategy', fake_signal_series)

    engine = BacktestEngine()
    engine.run(
        'EURUSD',
        'D1',
        '2025-01-01',
        '2025-03-01',
        strategy='ema_crossover',
        agent_config={'strategy_params': {'ema_fast': 99, 'ema_slow': 200, 'rsi_filter': 45}},
    )

    assert captured['params'] == {'ema_fast': 99, 'ema_slow': 200, 'rsi_filter': 45}
    assert captured['strategy'] == 'ema_crossover'
    assert captured['target_index'] is not None


@pytest.mark.parametrize(
    ('method_name', 'template', 'params'),
    [
        ('_signal_series_rsi_mean_reversion', 'rsi_mean_reversion', {'rsi_period': 7, 'oversold': 30, 'overbought': 70}),
        ('_signal_series_bollinger_breakout', 'bollinger_breakout', {'bb_period': 14, 'bb_std': 1.5}),
        ('_signal_series_macd_divergence', 'macd_divergence', {'fast': 5, 'slow': 13, 'signal': 8}),
    ],
)
def test_executable_template_helpers_delegate_to_shared_signal_engine_on_minimal_frames(
    monkeypatch,
    method_name: str,
    template: str,
    params: dict,
) -> None:
    index = pd.date_range('2025-01-01', periods=3, freq='D')
    frame = pd.DataFrame({'Close': [100.0, 101.0, 102.0]}, index=index)
    captured: dict[str, object] = {}

    def fake_signal_series_for_strategy(self, incoming_frame, strategy, strategy_params=None):  # noqa: ANN001
        captured['frame_columns'] = list(incoming_frame.columns)
        captured['strategy'] = strategy
        captured['params'] = strategy_params
        return pd.Series([0, 1, -1], index=incoming_frame.index, dtype='int64')

    monkeypatch.setattr(BacktestEngine, '_signal_series_for_strategy', fake_signal_series_for_strategy)

    engine = BacktestEngine()
    signals = getattr(engine, method_name)(frame, params)

    assert captured['frame_columns'] == ['Close']
    assert captured['strategy'] == template
    assert captured['params'] == params
    assert signals.tolist() == [0, 1, -1]


@pytest.mark.parametrize(
    ('template', 'params', 'close_values'),
    [
        (
            'ema_crossover',
            {'ema_fast': 5, 'ema_slow': 20, 'rsi_filter': 30},
            [1.3 - i * 0.001 for i in range(60)] + [1.24 + i * 0.0015 for i in range(60)],
        ),
        (
            'rsi_mean_reversion',
            {'rsi_period': 5, 'oversold': 30, 'overbought': 70},
            [1.0] * 60 + [0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30, 1.35, 1.40],
        ),
        (
            'bollinger_breakout',
            {'bb_period': 20, 'bb_std': 2.0},
            [1.0 + i * 0.0002 for i in range(80)] + [0.97, 0.96, 0.95, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99, 1.00],
        ),
        (
            'macd_divergence',
            {'fast': 6, 'slow': 18, 'signal': 5},
            [1.2 - i * 0.0015 for i in range(60)] + [1.11 + i * 0.0015 for i in range(60)],
        ),
    ],
)
def test_backtest_run_uses_shared_signal_series_after_indicator_preparation(
    monkeypatch,
    template: str,
    params: dict,
    close_values: list[float],
) -> None:
    raw_candles = _candles(close_values)
    raw_frame = _frame_from_candles(raw_candles)
    captured: dict[str, pd.Series] = {}

    monkeypatch.setattr(
        'app.services.backtest.engine.BacktestEngine._fetch_backtest_candles',
        lambda *args, **kwargs: raw_frame,
    )

    def fake_extract_trades(self, frame, signals):  # noqa: ANN001
        captured['signals'] = signals.copy()
        return []

    monkeypatch.setattr(BacktestEngine, '_extract_trades', fake_extract_trades)

    engine = BacktestEngine()
    prepared = engine._prepare_indicator_frame(raw_frame)
    expected_raw = _expected_signal_series_from_shared_engine(raw_candles, template, params)
    expected_aligned = expected_raw.loc[prepared.index]

    engine.run(
        'EURUSD',
        'H1',
        '2025-01-01',
        '2025-06-01',
        strategy=template,
        strategy_params=params,
    )

    pd.testing.assert_series_equal(captured['signals'], expected_aligned, check_names=False)
