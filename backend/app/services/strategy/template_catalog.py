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
    # ── Trend Following ──
    'ema_crossover': StrategyTemplateSpec(
        key='ema_crossover',
        description='EMA crossover with RSI filter — classic trend following',
        params={'ema_fast': 'int (5-50)', 'ema_slow': 'int (20-200)', 'rsi_filter': 'int (15-50)'},
        best_for='trending markets, medium-term',
        category='trend',
    ),
    'supertrend': StrategyTemplateSpec(
        key='supertrend',
        description='Supertrend — ATR-based trend following with dynamic support/resistance',
        params={'atr_period': 'int (7-21)', 'atr_multiplier': 'float (1.0-5.0)'},
        best_for='strong trending markets, all timeframes',
        category='trend',
    ),
    'adx_trend': StrategyTemplateSpec(
        key='adx_trend',
        description='ADX + DI directional movement — trades only when trend is strong',
        params={'adx_period': 'int (7-25)', 'adx_threshold': 'int (20-40)', 'di_period': 'int (7-25)'},
        best_for='trending markets, filters out ranging periods',
        category='trend',
    ),
    'ichimoku': StrategyTemplateSpec(
        key='ichimoku',
        description='Ichimoku Cloud — Tenkan/Kijun cross with cloud breakout',
        params={'tenkan': 'int (7-12)', 'kijun': 'int (22-30)', 'senkou_b': 'int (44-60)'},
        best_for='strong trends, medium to long-term',
        category='trend',
    ),
    'parabolic_sar': StrategyTemplateSpec(
        key='parabolic_sar',
        description='Parabolic SAR — trailing stop that flips long/short on reversal',
        params={'af_start': 'float (0.01-0.03)', 'af_step': 'float (0.01-0.03)', 'af_max': 'float (0.1-0.3)'},
        best_for='trending markets with clear directional moves',
        category='trend',
    ),
    'donchian_breakout': StrategyTemplateSpec(
        key='donchian_breakout',
        description='Donchian Channel breakout — buy new highs, sell new lows (turtle trading)',
        params={'entry_period': 'int (10-55)', 'exit_period': 'int (5-20)'},
        best_for='breakout trading, medium to long-term trends',
        category='trend',
    ),

    # ── Mean Reversion ──
    'rsi_mean_reversion': StrategyTemplateSpec(
        key='rsi_mean_reversion',
        description='RSI mean reversion — buy oversold, sell overbought',
        params={'rsi_period': 'int (5-30)', 'oversold': 'int (10-40)', 'overbought': 'int (60-90)'},
        best_for='ranging markets',
        category='mean_reversion',
    ),
    'stochastic_reversal': StrategyTemplateSpec(
        key='stochastic_reversal',
        description='Stochastic K/D crossover — buy oversold cross up, sell overbought cross down',
        params={'k_period': 'int (5-21)', 'd_period': 'int (3-7)', 'oversold': 'int (15-30)', 'overbought': 'int (70-85)'},
        best_for='ranging and choppy markets, short to medium-term',
        category='mean_reversion',
    ),
    'williams_r': StrategyTemplateSpec(
        key='williams_r',
        description='Williams %R — momentum oscillator overbought/oversold',
        params={'period': 'int (7-21)', 'oversold': 'int (-90,-70)', 'overbought': 'int (-30,-10)'},
        best_for='ranging markets, mean reversion on pullbacks',
        category='mean_reversion',
    ),
    'cci_reversal': StrategyTemplateSpec(
        key='cci_reversal',
        description='CCI reversal — buy when CCI crosses above oversold, sell above overbought',
        params={'cci_period': 'int (10-30)', 'oversold': 'int (-150,-80)', 'overbought': 'int (80,150)'},
        best_for='cyclical markets, commodities, forex ranging',
        category='mean_reversion',
    ),
    'keltner_reversion': StrategyTemplateSpec(
        key='keltner_reversion',
        description='Keltner Channel mean reversion — buy below lower band, sell above upper',
        params={'ema_period': 'int (10-30)', 'atr_period': 'int (10-20)', 'atr_multiplier': 'float (1.0-3.0)'},
        best_for='ranging markets with clear boundaries',
        category='mean_reversion',
    ),

    # ── Breakout / Volatility ──
    'bollinger_breakout': StrategyTemplateSpec(
        key='bollinger_breakout',
        description='Bollinger Band breakout — squeeze detection and breakout entry',
        params={'bb_period': 'int (5-50)', 'bb_std': 'float (0.5-4.0)'},
        best_for='breakout setups',
        category='breakout',
    ),
    'squeeze_momentum': StrategyTemplateSpec(
        key='squeeze_momentum',
        description='Bollinger/Keltner squeeze — low volatility compression then breakout',
        params={'bb_period': 'int (15-25)', 'bb_std': 'float (1.5-2.5)', 'kc_period': 'int (15-25)', 'kc_multiplier': 'float (1.0-2.0)'},
        best_for='consolidation breakouts, all timeframes',
        category='breakout',
    ),
    'atr_trailing_stop': StrategyTemplateSpec(
        key='atr_trailing_stop',
        description='ATR trailing stop — enters on trend, trails stop using ATR distance',
        params={'atr_period': 'int (10-21)', 'atr_multiplier': 'float (1.5-4.0)', 'trend_ema': 'int (20-50)'},
        best_for='riding strong trends with dynamic risk management',
        category='breakout',
    ),

    # ── Momentum ──
    'macd_divergence': StrategyTemplateSpec(
        key='macd_divergence',
        description='MACD signal line crossover — momentum shift detection',
        params={'fast': 'int (4-20)', 'slow': 'int (15-50)', 'signal': 'int (3-15)'},
        best_for='momentum shifts',
        category='momentum',
    ),
    'roc_momentum': StrategyTemplateSpec(
        key='roc_momentum',
        description='Rate of Change — momentum by comparing current price to N bars ago',
        params={'roc_period': 'int (9-21)', 'signal_period': 'int (5-12)', 'threshold': 'float (0.5-3.0)'},
        best_for='momentum acceleration detection',
        category='momentum',
    ),
    'vwap_strategy': StrategyTemplateSpec(
        key='vwap_strategy',
        description='VWAP — buy below VWAP (discount), sell above VWAP (premium)',
        params={'trend_ema': 'int (20-50)', 'deviation_pct': 'float (0.1-1.0)'},
        best_for='intraday trading, M5/M15/H1',
        category='momentum',
    ),

    # ── Hybrid ──
    'triple_ema': StrategyTemplateSpec(
        key='triple_ema',
        description='Triple EMA alignment — 3 EMAs confirm trend, crossovers trigger entries',
        params={'ema_1': 'int (3-8)', 'ema_2': 'int (10-18)', 'ema_3': 'int (25-55)'},
        best_for='trend following with momentum confirmation',
        category='hybrid',
    ),
    'macd_rsi_combo': StrategyTemplateSpec(
        key='macd_rsi_combo',
        description='MACD + RSI combined — MACD for direction, RSI for timing',
        params={'macd_fast': 'int (8-14)', 'macd_slow': 'int (20-30)', 'macd_signal': 'int (7-12)', 'rsi_period': 'int (10-18)', 'rsi_oversold': 'int (25-40)', 'rsi_overbought': 'int (60-75)'},
        best_for='filtered momentum entries, reduces false signals',
        category='hybrid',
    ),
    'pivot_points': StrategyTemplateSpec(
        key='pivot_points',
        description='Pivot Points — classic S/R levels for intraday trading',
        params={'pivot_type': 'str (standard|fibonacci|woodie)', 'lookback': 'int (1-5)'},
        best_for='intraday support/resistance trading',
        category='hybrid',
    ),
}

EXECUTABLE_STRATEGY_PARAM_RANGES: dict[str, dict[str, tuple[float | int, float | int]]] = {
    # Trend
    'ema_crossover': {'ema_fast': (5, 50), 'ema_slow': (20, 200), 'rsi_filter': (15, 50)},
    'supertrend': {'atr_period': (5, 30), 'atr_multiplier': (0.5, 6.0)},
    'adx_trend': {'adx_period': (5, 30), 'adx_threshold': (15, 50), 'di_period': (5, 30)},
    'ichimoku': {'tenkan': (5, 15), 'kijun': (18, 35), 'senkou_b': (35, 70)},
    'parabolic_sar': {'af_start': (0.005, 0.05), 'af_step': (0.005, 0.05), 'af_max': (0.05, 0.5)},
    'donchian_breakout': {'entry_period': (5, 60), 'exit_period': (3, 25)},
    # Mean reversion
    'rsi_mean_reversion': {'rsi_period': (5, 30), 'oversold': (10, 40), 'overbought': (60, 90)},
    'stochastic_reversal': {'k_period': (3, 25), 'd_period': (2, 10), 'oversold': (10, 35), 'overbought': (65, 90)},
    'williams_r': {'period': (5, 25), 'oversold': (-95, -60), 'overbought': (-40, -5)},
    'cci_reversal': {'cci_period': (5, 40), 'oversold': (-200, -50), 'overbought': (50, 200)},
    'keltner_reversion': {'ema_period': (5, 40), 'atr_period': (5, 25), 'atr_multiplier': (0.5, 4.0)},
    # Breakout
    'bollinger_breakout': {'bb_period': (5, 50), 'bb_std': (0.5, 4.0)},
    'squeeze_momentum': {'bb_period': (10, 30), 'bb_std': (1.0, 3.0), 'kc_period': (10, 30), 'kc_multiplier': (0.5, 3.0)},
    'atr_trailing_stop': {'atr_period': (5, 25), 'atr_multiplier': (1.0, 5.0), 'trend_ema': (10, 60)},
    # Momentum
    'macd_divergence': {'fast': (4, 20), 'slow': (15, 50), 'signal': (3, 15)},
    'roc_momentum': {'roc_period': (5, 30), 'signal_period': (3, 15), 'threshold': (0.1, 5.0)},
    'vwap_strategy': {'trend_ema': (10, 60), 'deviation_pct': (0.05, 2.0)},
    # Hybrid
    'triple_ema': {'ema_1': (2, 10), 'ema_2': (8, 20), 'ema_3': (20, 60)},
    'macd_rsi_combo': {'macd_fast': (6, 16), 'macd_slow': (18, 35), 'macd_signal': (5, 14), 'rsi_period': (7, 21), 'rsi_oversold': (20, 45), 'rsi_overbought': (55, 80)},
    'pivot_points': {'lookback': (1, 5)},
}


def build_strategy_system_prompt() -> str:
    lines = [
        'You are a quantitative trading strategy designer. You create trading strategies based on user descriptions.',
        '',
        'Available strategy templates and their configurable parameters:',
        '',
    ]
    for index, (template, spec) in enumerate(EXECUTABLE_STRATEGY_TEMPLATES.items(), start=1):
        lines.append(f'{index}. {template} [{spec.category}]: {spec.description}')
        lines.append(f'   Best for: {spec.best_for}')
        for param_name, param_range in spec.params.items():
            lines.append(f'   - {param_name}: {param_range}')
        lines.append('')

    lines.extend([
        'Available symbols: EURUSD.PRO, GBPUSD.PRO, USDJPY.PRO, AUDUSD.PRO, USDCAD.PRO, NZDUSD.PRO, USDCHF.PRO, EURGBP.PRO, EURJPY.PRO, GBPJPY.PRO, BTCUSD, ETHUSD, ADAUSD, XRPUSD, SOLUSD, DOTUSD, LINKUSD, AVAXUSD, MATICUSD, UNIUSD, AAVEUSD, LTCUSD, ATOMUSD',
        'Available timeframes: M5, M15, H1, H4, D1',
        '',
        'Template selection policy (mandatory):',
        '1. explicit user request match',
        '2. direct template availability',
        '3. implementation fidelity',
        '4. market regime fit',
        '5. warning generation',
        '',
        'If explicit user intent matches an available template, keep it. Do not silently override.',
        'If no exact template exists, mark approximation or set custom_strategy_required=true.',
        'Only apply "best current fit" substitution when the user explicitly asks for it.',
        '',
        'Choose the symbol and timeframe that best match the user\'s description. If unspecified, default to EURUSD.PRO and H1.',
        '',
        'RESPOND ONLY WITH VALID JSON (no markdown, no explanation):',
        '{',
        f'  "template": "<one of the {len(EXECUTABLE_STRATEGY_TEMPLATES)} template names>",',
        '  "selected_template": "<final selected template name>",',
        '  "requested_archetype": "<explicit user archetype/template or null>",',
        '  "match_basis": "<explicit_template_request|explicit_archetype_request|best_current_fit_request|approximation_due_to_missing_exact_template|model_recommendation>",',
        '  "request_fidelity": "<exact|archetype|best_fit_requested|approximation|inferred>",',
        '  "market_fit": "<strong|good|watch|poor|unknown>",',
        '  "deployment_quality": "<ready|deploy_with_caution|degraded_market_alignment|review_required_approximation|unknown_market_fit>",',
        '  "warnings": ["<warning>", "..."],',
        '  "custom_strategy_required": <true|false>,',
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
    allowed_ranges = EXECUTABLE_STRATEGY_PARAM_RANGES.get(template, {})
    sanitized: dict[str, Any] = {}
    warnings: list[str] = []

    for key, raw_value in raw_params.items():
        if key not in allowed_ranges:
            # Keep string params (like pivot_type) as-is
            if isinstance(raw_value, str):
                sanitized[key] = raw_value
                continue
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


def sanitize_strategy_params_for_template(template: str, params: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """Sanitize params for executable templates and preserve legacy templates unchanged."""
    if template in EXECUTABLE_STRATEGY_TEMPLATES:
        return sanitize_executable_strategy_params(template, params)

    preserved = dict(params or {})
    warnings = [f'preserved params for legacy template {template}']
    return preserved, warnings
