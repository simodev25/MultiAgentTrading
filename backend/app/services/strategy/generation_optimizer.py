from __future__ import annotations

from typing import Any

from app.services.strategy.validation_scoring import compute_validation_score


def _metric(metrics: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = metrics.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _trades(metrics: dict[str, Any]) -> int:
    for key in ('total_trades', 'trades'):
        value = metrics.get(key)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            continue
    return 0


def compute_generation_candidate_score(metrics: dict[str, Any]) -> float:
    score, _, _ = compute_validation_score(
        win_rate=_metric(metrics, 'win_rate_pct', 'win_rate'),
        profit_factor=_metric(metrics, 'profit_factor'),
        max_dd=abs(_metric(metrics, 'max_drawdown_pct', 'max_drawdown')),
        total_return=_metric(metrics, 'total_return_pct', 'total_return'),
        trades=_trades(metrics),
    )
    return float(score)


def should_optimize_generation(metrics: dict[str, Any]) -> bool:
    trades = _trades(metrics)
    score = compute_generation_candidate_score(metrics)
    total_return = _metric(metrics, 'total_return_pct', 'total_return')
    if trades <= 0:
        return True
    if trades < 10:
        return True
    if total_return <= 0:
        return True
    return score < 35.0


def choose_best_generation_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        raise ValueError('No generation candidates provided')

    def _sort_key(candidate: dict[str, Any]) -> tuple[float, int, float]:
        metrics = candidate.get('metrics', {}) if isinstance(candidate.get('metrics', {}), dict) else {}
        return (
            compute_generation_candidate_score(metrics),
            _trades(metrics),
            _metric(metrics, 'total_return_pct', 'total_return'),
        )

    return max(candidates, key=_sort_key)


def _is_crypto_symbol(symbol: str) -> bool:
    normalized = str(symbol or '').upper()
    return '.PRO' not in normalized and normalized.endswith('USD')


def build_market_adaptive_param_candidates(
    *,
    template: str,
    symbol: str,
    timeframe: str,
    market_regime: str | None,
    current_params: dict[str, Any],
) -> list[dict[str, Any]]:
    if not _is_crypto_symbol(symbol):
        return []

    regime = str(market_regime or '').lower()
    normalized_timeframe = str(timeframe or '').upper()
    candidates: list[dict[str, Any]] = []

    if template == 'ema_crossover':
        presets: list[tuple[dict[str, Any], str]] = [
            (
                {'ema_fast': 9, 'ema_slow': 21, 'rsi_filter': 30},
                'crypto trend preset tuned for higher participation',
            ),
            (
                {'ema_fast': 12, 'ema_slow': 36, 'rsi_filter': 35},
                'crypto trend preset tuned for calmer swings',
            ),
            (
                {'ema_fast': 8, 'ema_slow': 34, 'rsi_filter': 25},
                'crypto trend preset tuned for low-volatility breakouts',
            ),
        ]
        if regime in {'calm', 'range', 'ranging', 'low_volatility'} or normalized_timeframe in {'H1', 'M15'}:
            presets.insert(
                0,
                (
                    {'ema_fast': 7, 'ema_slow': 18, 'rsi_filter': 25},
                    'crypto calm-regime preset tuned to avoid zero-trade drafts',
                ),
            )
        seen = {tuple(sorted((current_params or {}).items()))}
        for params, reason in presets:
            key = tuple(sorted(params.items()))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    'params': params,
                    'reason': reason,
                    'warnings': ['heuristic_crypto_adaptation'],
                }
            )

    return candidates
