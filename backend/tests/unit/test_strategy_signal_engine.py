import os
import sys
import types

import pytest
import pandas as pd

os.environ['DATABASE_URL'] = 'sqlite:///./test.db'
os.environ['BOOTSTRAP_ADMIN_PASSWORD'] = 'admin1234'

from app.api.routes.strategies import _compute_indicators
from app.services.backtest.engine import BacktestEngine
from app.services.strategy.signal_engine import (
    compute_strategy_overlays_and_signals,
    get_supported_strategy_templates,
)
from app.services.strategy.template_catalog import (
    EXECUTABLE_STRATEGY_TEMPLATES,
    build_strategy_system_prompt,
    sanitize_executable_strategy_params,
)


class _FakeCeleryApp:
    def task(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(func):
            return func

        return _decorator


sys.modules.setdefault(
    'app.tasks.celery_app',
    types.SimpleNamespace(celery_app=_FakeCeleryApp()),
)

from app.tasks.strategy_monitor_task import _compute_latest_signal


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


def _signal_sides(result: dict) -> list[str]:
    return [signal['side'] for signal in result['signals']]


def _frame_from_candles(candles: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            'Open': [candle['open'] for candle in candles],
            'High': [candle['high'] for candle in candles],
            'Low': [candle['low'] for candle in candles],
            'Close': [candle['close'] for candle in candles],
            'Volume': [candle['volume'] for candle in candles],
        },
        index=pd.DatetimeIndex([candle['time'] for candle in candles]),
    )
    return frame


def _backtest_entries(series: pd.Series, candles: list[dict]) -> list[dict]:
    entries: list[dict] = []
    previous = 0

    for signal, candle in zip(series.tolist(), candles):
        current = int(signal)
        if current != 0 and current != previous:
            entries.append(
                {
                    'time': candle['time'],
                    'price': float(candle['close']),
                    'side': 'BUY' if current == 1 else 'SELL',
                }
            )
        previous = current

    return entries


def test_supported_strategy_templates_are_executable() -> None:
    templates = set(get_supported_strategy_templates())
    # Must include original 4 + new ones
    assert {'ema_crossover', 'rsi_mean_reversion', 'bollinger_breakout', 'macd_divergence'}.issubset(templates)
    assert len(templates) >= 20  # 20 templates total


def test_executable_template_params_match_the_engine_contract() -> None:
    assert set(EXECUTABLE_STRATEGY_TEMPLATES['ema_crossover'].params) == {'ema_fast', 'ema_slow', 'rsi_filter'}
    assert set(EXECUTABLE_STRATEGY_TEMPLATES['rsi_mean_reversion'].params) == {'rsi_period', 'oversold', 'overbought'}
    assert set(EXECUTABLE_STRATEGY_TEMPLATES['bollinger_breakout'].params) == {'bb_period', 'bb_std'}
    assert set(EXECUTABLE_STRATEGY_TEMPLATES['macd_divergence'].params) == {'fast', 'slow', 'signal'}


def test_strategy_system_prompt_is_derived_from_executable_catalog() -> None:
    prompt = build_strategy_system_prompt()

    assert '5-50' in prompt
    assert '20-200' in prompt
    assert 'rsi_period: int (5-30)' in prompt
    assert 'bb_period: int (5-50)' in prompt
    assert 'bb_std: float (0.5-4.0)' in prompt
    # New templates should also appear
    assert 'supertrend' in prompt
    assert 'ichimoku' in prompt


def test_sanitize_executable_strategy_params_drops_unknown_keys() -> None:
    params, warnings = sanitize_executable_strategy_params(
        'bollinger_breakout',
        {'bb_period': 20, 'bb_std': 2.0, 'noise': 42},
    )

    assert params == {'bb_period': 20, 'bb_std': 2.0}
    assert any('noise' in warning for warning in warnings)


@pytest.mark.parametrize(
    ('template', 'params', 'close_values', 'expected_side'),
    [
        (
            'ema_crossover',
            {'ema_fast': 5, 'ema_slow': 20, 'rsi_filter': 30},
            [1.2 - i * 0.002 for i in range(40)] + [1.12 + i * 0.002 for i in range(40)],
            'BUY',
        ),
        (
            'ema_crossover',
            {'ema_fast': 5, 'ema_slow': 20, 'rsi_filter': 30},
            [1.0 + i * 0.002 for i in range(40)] + [1.08 - i * 0.002 for i in range(40)],
            'SELL',
        ),
        (
            'bollinger_breakout',
            {'bb_period': 20, 'bb_std': 2.0},
            [1.0 + i * 0.0005 for i in range(30)] + [0.97, 0.96, 0.95, 0.94],
            'BUY',
        ),
        (
            'bollinger_breakout',
            {'bb_period': 20, 'bb_std': 2.0},
            [1.2 - i * 0.0005 for i in range(30)] + [1.23, 1.24, 1.25, 1.26],
            'SELL',
        ),
        (
            'macd_divergence',
            {'fast': 6, 'slow': 18, 'signal': 5},
            [1.2 - i * 0.002 for i in range(35)] + [1.13 + i * 0.002 for i in range(35)],
            'BUY',
        ),
        (
            'macd_divergence',
            {'fast': 6, 'slow': 18, 'signal': 5},
            [1.0 + i * 0.002 for i in range(35)] + [1.07 - i * 0.002 for i in range(35)],
            'SELL',
        ),
    ],
)
def test_trend_and_breakout_templates_emit_expected_signal_direction(
    template: str,
    params: dict,
    close_values: list[float],
    expected_side: str,
) -> None:
    result = compute_strategy_overlays_and_signals(_candles(close_values), template, params)

    assert len(result['signals']) == 1
    assert _signal_sides(result) == [expected_side]


def test_rsi_mean_reversion_emits_both_sides_when_price_stretches_and_reverts() -> None:
    candles = _candles(
        [1.0] * 10
        + [0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30, 1.35, 1.40]
    )

    result = compute_strategy_overlays_and_signals(
        candles,
        'rsi_mean_reversion',
        {'rsi_period': 5, 'oversold': 30, 'overbought': 70},
    )

    assert _signal_sides(result) == ['BUY', 'SELL']


def test_unknown_template_raises_value_error() -> None:
    candles = _candles([1.1000 for _ in range(40)])

    with pytest.raises(ValueError, match='Unsupported strategy template: totally_fake_template'):
        compute_strategy_overlays_and_signals(candles, 'totally_fake_template', {})


@pytest.mark.parametrize(
    ('template', 'params', 'close_values'),
    [
        (
            'ema_crossover',
            {'ema_fast': 5, 'ema_slow': 20, 'rsi_filter': 30},
            [1.2 - i * 0.002 for i in range(40)] + [1.12 + i * 0.002 for i in range(40)],
        ),
        (
            'rsi_mean_reversion',
            {'rsi_period': 5, 'oversold': 30, 'overbought': 70},
            [1.0] * 10
            + [0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30, 1.35, 1.40],
        ),
        (
            'bollinger_breakout',
            {'bb_period': 20, 'bb_std': 2.0},
            [1.0 + i * 0.0005 for i in range(30)] + [0.97, 0.96, 0.95, 0.94],
        ),
        (
            'macd_divergence',
            {'fast': 6, 'slow': 18, 'signal': 5},
            [1.2 - i * 0.002 for i in range(35)] + [1.13 + i * 0.002 for i in range(35)],
        ),
    ],
)
def test_monitor_chart_and_backtest_surfaces_share_signal_rules(
    template: str,
    params: dict,
    close_values: list[float],
) -> None:
    candles = _candles(close_values)
    shared = compute_strategy_overlays_and_signals(candles, template, params)
    chart = _compute_indicators(candles, template, params)
    latest = _compute_latest_signal(candles, template, params)

    engine = BacktestEngine()
    backtest_series = engine._signal_series_for_strategy(_frame_from_candles(candles), template, params)

    assert chart == shared
    assert latest == (shared['signals'][-1] if shared['signals'] else None)
    assert _backtest_entries(backtest_series, candles) == shared['signals']
