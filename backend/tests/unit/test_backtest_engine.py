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

    monkeypatch.setattr('app.services.market.news_provider.MarketProvider.get_historical_candles', lambda *args, **kwargs: frame)

    engine = BacktestEngine()
    result = engine.run('EURUSD', 'D1', '2025-01-01', '2025-06-30', strategy='ema_rsi')

    assert 'total_return_pct' in result.metrics
    assert 'sharpe_ratio' in result.metrics
    assert result.metrics.get('strategy') == 'ema_rsi'
    assert result.metrics.get('workflow_source') == 'BacktestEngine.ema_rsi'
    assert isinstance(result.equity_curve, list)
    assert len(result.equity_curve) > 0


def test_backtest_engine_rejects_removed_agents_strategy() -> None:
    assert BacktestEngine.normalize_strategy('agents') is None
    assert BacktestEngine.normalize_strategy('multi-agent') is None
