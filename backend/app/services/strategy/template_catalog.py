from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class StrategyTemplateSpec:
    key: str
    description: str
    params: dict[str, str]
    best_for: str
    category: str


EXECUTABLE_STRATEGY_TEMPLATES: dict[str, StrategyTemplateSpec] = {
    'ema_crossover': StrategyTemplateSpec(
        key='ema_crossover',
        description='EMA crossover with RSI filter',
        params={'ema_fast': 'int (5-50)', 'ema_slow': 'int (20-200)', 'rsi_filter': 'int (15-50)'},
        best_for='trending markets, medium-term',
        category='trend',
    ),
    'rsi_mean_reversion': StrategyTemplateSpec(
        key='rsi_mean_reversion',
        description='RSI mean reversion',
        params={'rsi_period': 'int (5-30)', 'oversold': 'int (10-40)', 'overbought': 'int (60-90)'},
        best_for='ranging markets',
        category='mean_reversion',
    ),
    'bollinger_breakout': StrategyTemplateSpec(
        key='bollinger_breakout',
        description='Bollinger Band breakout',
        params={'bb_period': 'int (5-50)', 'bb_std': 'float (0.5-4.0)'},
        best_for='breakout setups',
        category='breakout',
    ),
    'macd_divergence': StrategyTemplateSpec(
        key='macd_divergence',
        description='MACD signal line crossover',
        params={'fast': 'int (4-20)', 'slow': 'int (15-50)', 'signal': 'int (3-15)'},
        best_for='momentum shifts',
        category='momentum',
    ),
}

EXECUTABLE_STRATEGY_PARAM_RANGES: dict[str, dict[str, tuple[float | int, float | int]]] = {
    'ema_crossover': {'ema_fast': (5, 50), 'ema_slow': (20, 200), 'rsi_filter': (15, 50)},
    'rsi_mean_reversion': {'rsi_period': (5, 30), 'oversold': (10, 40), 'overbought': (60, 90)},
    'bollinger_breakout': {'bb_period': (5, 50), 'bb_std': (0.5, 4.0)},
    'macd_divergence': {'fast': (4, 20), 'slow': (15, 50), 'signal': (3, 15)},
}


def build_strategy_system_prompt() -> str:
    lines = [
        'You are a quantitative trading strategy designer. You create trading strategies based on user descriptions.',
        '',
        'Available strategy templates and their configurable parameters:',
        '',
    ]
    for index, (template, spec) in enumerate(EXECUTABLE_STRATEGY_TEMPLATES.items(), start=1):
        lines.append(f'{index}. {template}: {spec.description}')
        for param_name, param_range in spec.params.items():
            lines.append(f'   - {param_name}: {param_range}')
        lines.append('')

    lines.extend([
        'Available symbols: EURUSD.PRO, GBPUSD.PRO, USDJPY.PRO, AUDUSD.PRO, USDCAD.PRO, NZDUSD.PRO, USDCHF.PRO, EURGBP.PRO, EURJPY.PRO, GBPJPY.PRO, BTCUSD, ETHUSD, ADAUSD, XRPUSD, SOLUSD, DOTUSD, LINKUSD, AVAXUSD, MATICUSD, UNIUSD, AAVEUSD, LTCUSD, ATOMUSD',
        'Available timeframes: M5, M15, H1, H4, D1',
        '',
        'Choose the symbol and timeframe that best match the user\'s description. If unspecified, default to EURUSD.PRO and H1.',
        '',
        'RESPOND ONLY WITH VALID JSON (no markdown, no explanation):',
        '{',
        '  "template": "<one of the 4 template names>",',
        '  "name": "<short strategy name using underscores, max 30 chars>",',
        '  "symbol": "<symbol>",',
        '  "timeframe": "<timeframe>",',
        '  "params": { <template-specific params> },',
        '  "description": "<one sentence describing the strategy logic>"',
        '}',
    ])
    return '\n'.join(lines)


def sanitize_executable_strategy_params(template: str, params: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    if template not in EXECUTABLE_STRATEGY_TEMPLATES:
        raise ValueError(f'Unsupported strategy template: {template}')

    raw_params = params or {}
    allowed_ranges = EXECUTABLE_STRATEGY_PARAM_RANGES[template]
    sanitized: dict[str, Any] = {}
    warnings: list[str] = []

    for key, raw_value in raw_params.items():
        if key not in allowed_ranges:
            warnings.append(f'dropped unsupported param {key}')
            continue

        lo, hi = allowed_ranges[key]
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            warnings.append(f'dropped invalid param {key}')
            continue

        clamped = min(max(value, float(lo)), float(hi))
        if clamped != value:
            warnings.append(f'{key}={value} outside range {lo}-{hi}, clamped')
        sanitized[key] = int(clamped) if isinstance(lo, int) and isinstance(hi, int) else round(clamped, 2)

    return sanitized, warnings
