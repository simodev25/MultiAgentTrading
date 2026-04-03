import numpy as np
import pandas as pd

from app.services.backtest.engine import BacktestEngine


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

    def fake_signal_series(self, prepared_frame, params=None):  # noqa: ANN001
        captured['params'] = params
        return pd.Series(np.zeros(len(prepared_frame), dtype=int), index=prepared_frame.index)

    monkeypatch.setattr('app.services.backtest.engine.BacktestEngine._signal_series_ema_crossover', fake_signal_series)

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

    def fake_signal_series(self, prepared_frame, params=None):  # noqa: ANN001
        captured['params'] = params
        return pd.Series(np.zeros(len(prepared_frame), dtype=int), index=prepared_frame.index)

    monkeypatch.setattr('app.services.backtest.engine.BacktestEngine._signal_series_ema_crossover', fake_signal_series)

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


def test_rsi_mean_reversion_uses_persisted_params(monkeypatch) -> None:
    index = pd.date_range('2025-01-01', periods=3, freq='D')
    frame = pd.DataFrame(
        {
            'Close': [100.0, 101.0, 102.0],
            'rsi': [50.0, 50.0, 50.0],
        },
        index=index,
    )

    called: dict[str, int] = {}

    class FakeRSIIndicator:
        def __init__(self, close, window):  # noqa: ANN001
            called['window'] = window

        def rsi(self) -> pd.Series:
            return pd.Series([25.0, 50.0, 75.0], index=index)

    monkeypatch.setattr('app.services.backtest.engine.RSIIndicator', FakeRSIIndicator)

    engine = BacktestEngine()
    signals = engine._signal_series_rsi_mean_reversion(
        frame,
        {'rsi_period': 7, 'oversold': 30, 'overbought': 70},
    )

    assert called['window'] == 7
    assert signals.tolist() == [1, 0, -1]


def test_bollinger_breakout_uses_persisted_params(monkeypatch) -> None:
    index = pd.date_range('2025-01-01', periods=3, freq='D')
    frame = pd.DataFrame(
        {
            'Close': [98.0, 100.0, 102.0],
            'bb_upper': [103.0, 103.0, 103.0],
            'bb_lower': [97.0, 97.0, 97.0],
        },
        index=index,
    )

    called: dict[str, float | int] = {}

    class FakeBollingerBands:
        def __init__(self, close, window, window_dev):  # noqa: ANN001
            called['window'] = window
            called['window_dev'] = window_dev

        def bollinger_hband(self) -> pd.Series:
            return pd.Series([99.0, 101.0, 101.0], index=index)

        def bollinger_lband(self) -> pd.Series:
            return pd.Series([99.0, 99.0, 101.0], index=index)

        def bollinger_mavg(self) -> pd.Series:
            return pd.Series([100.0, 100.0, 100.0], index=index)

    monkeypatch.setattr('app.services.backtest.engine.BollingerBands', FakeBollingerBands)

    engine = BacktestEngine()
    signals = engine._signal_series_bollinger_breakout(
        frame,
        {'bb_period': 14, 'bb_std': 1.5},
    )

    assert called['window'] == 14
    assert called['window_dev'] == 1.5
    assert signals.tolist() == [1, 0, -1]


def test_macd_divergence_uses_persisted_params(monkeypatch) -> None:
    index = pd.date_range('2025-01-01', periods=3, freq='D')
    frame = pd.DataFrame(
        {
            'Close': [100.0, 101.0, 102.0],
            'macd': [0.0, 0.0, 0.0],
            'macd_signal': [0.0, 0.0, 0.0],
            'macd_diff': [0.0, 0.0, 0.0],
        },
        index=index,
    )

    called: dict[str, int] = {}

    class FakeMACD:
        def __init__(self, close, window_fast, window_slow, window_sign):  # noqa: ANN001
            called['fast'] = window_fast
            called['slow'] = window_slow
            called['signal'] = window_sign

        def macd(self) -> pd.Series:
            return pd.Series([0.0, 2.0, -2.0], index=index)

        def macd_signal(self) -> pd.Series:
            return pd.Series([0.5, 1.0, 1.0], index=index)

        def macd_diff(self) -> pd.Series:
            return pd.Series([0.0, 1.0, -3.0], index=index)

    monkeypatch.setattr('app.services.backtest.engine.MACD', FakeMACD)

    engine = BacktestEngine()
    signals = engine._signal_series_macd_divergence(
        frame,
        {'fast': 5, 'slow': 13, 'signal': 8},
    )

    assert called['fast'] == 5
    assert called['slow'] == 13
    assert called['signal'] == 8
    assert signals.tolist() == [0, 1, -1]
