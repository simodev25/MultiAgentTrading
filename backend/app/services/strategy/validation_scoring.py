from __future__ import annotations

MIN_VALIDATION_SCORE = 40.0
MIN_VALIDATION_PROFIT_FACTOR = 1.1
MAX_VALIDATION_DRAWDOWN_PCT = 35.0


def sample_size_factor(total_trades: int) -> float:
    if total_trades <= 0:
        return 0.0
    if total_trades < 10:
        return total_trades / 20.0
    if total_trades < 30:
        return 0.5 + ((total_trades - 10) * (0.5 / 20.0))
    return 1.0


def return_penalty_factor(total_return_pct: float) -> float:
    if total_return_pct <= 0:
        return 0.7
    return 1.0


def compute_validation_score(
    *,
    win_rate: float,
    profit_factor: float,
    max_dd: float,
    total_return: float,
    trades: int,
) -> tuple[float, float, float]:
    raw_score = min(
        100.0,
        max(
            0.0,
            win_rate * 0.3
            + min(profit_factor * 20.0, 40.0)
            + max(0.0, 30.0 - max_dd * 3.0),
        ),
    )
    sample_factor = sample_size_factor(trades)
    return_penalty = return_penalty_factor(total_return)
    score = raw_score * sample_factor * return_penalty
    return score, raw_score, sample_factor


def should_validate_strategy(
    *,
    score: float,
    total_return: float,
    profit_factor: float,
    max_dd: float,
) -> bool:
    return (
        float(score) >= MIN_VALIDATION_SCORE
        and float(total_return) > 0.0
        and float(profit_factor) >= MIN_VALIDATION_PROFIT_FACTOR
        and float(max_dd) <= MAX_VALIDATION_DRAWDOWN_PCT
    )
