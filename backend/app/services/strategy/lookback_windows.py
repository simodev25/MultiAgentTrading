from __future__ import annotations


def strategy_lookback_days(symbol: str) -> int:
    normalized = str(symbol or '').upper()
    if '.PRO' not in normalized and normalized.endswith('USD'):
        return 90
    return 30
