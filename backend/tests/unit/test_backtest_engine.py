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


def test_backtest_engine_uses_strategy_params(monkeypatch) -> None:
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

    def fake_generate_signals(self, prepared_frame, strategy, agent_config=None, strategy_params=None):  # noqa: ANN001
        captured['strategy'] = strategy
        captured['agent_config'] = agent_config
        captured['strategy_params'] = strategy_params
        return pd.Series(np.zeros(len(prepared_frame), dtype=int), index=prepared_frame.index)

    monkeypatch.setattr('app.services.backtest.engine.BacktestEngine._generate_signals', fake_generate_signals)

    engine = BacktestEngine()
    engine.run(
        'EURUSD',
        'D1',
        '2025-01-01',
        '2025-03-01',
        strategy='ema_crossover',
        agent_config={'strategy_params': {'ema_fast': 99, 'ema_slow': 200, 'rsi_filter': 45}},
        strategy_params={'ema_fast': 7, 'ema_slow': 14, 'rsi_filter': 25},
    )

    assert captured['strategy'] == 'ema_crossover'
    assert captured['strategy_params'] == {'ema_fast': 7, 'ema_slow': 14, 'rsi_filter': 25}
