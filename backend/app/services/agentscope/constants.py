"""Extracted thresholds, policies, timeframes, and asset constants."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class DecisionGatingPolicy:
    min_combined_score: float
    min_confidence: float
    min_aligned_sources: int
    allow_technical_single_source_override: bool
    block_major_contradiction: bool
    contradiction_penalty_weak: float
    contradiction_penalty_moderate: float
    contradiction_penalty_major: float
    confidence_multiplier_moderate: float
    confidence_multiplier_major: float

CONSERVATIVE = DecisionGatingPolicy(
    min_combined_score=0.32, min_confidence=0.38, min_aligned_sources=2,
    allow_technical_single_source_override=False, block_major_contradiction=True,
    contradiction_penalty_weak=0.0, contradiction_penalty_moderate=0.08,
    contradiction_penalty_major=0.14, confidence_multiplier_moderate=0.80,
    confidence_multiplier_major=0.60,
)
BALANCED = DecisionGatingPolicy(
    min_combined_score=0.22, min_confidence=0.28, min_aligned_sources=1,
    allow_technical_single_source_override=True, block_major_contradiction=True,
    contradiction_penalty_weak=0.0, contradiction_penalty_moderate=0.06,
    contradiction_penalty_major=0.11, confidence_multiplier_moderate=0.85,
    confidence_multiplier_major=0.70,
)
PERMISSIVE = DecisionGatingPolicy(
    min_combined_score=0.13, min_confidence=0.25, min_aligned_sources=1,
    allow_technical_single_source_override=True, block_major_contradiction=True,
    contradiction_penalty_weak=0.01, contradiction_penalty_moderate=0.04,
    contradiction_penalty_major=0.08, confidence_multiplier_moderate=0.90,
    confidence_multiplier_major=0.75,
)
DECISION_MODES: dict[str, DecisionGatingPolicy] = {
    "conservative": CONSERVATIVE, "balanced": BALANCED, "permissive": PERMISSIVE,
}

TIMEFRAME_ORDER = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN")
MAX_USEFUL_TF = "D1"

def higher_timeframes(current_tf: str, max_count: int = 2) -> list[str]:
    try:
        idx = TIMEFRAME_ORDER.index(current_tf)
    except ValueError:
        return []
    cap = TIMEFRAME_ORDER.index(MAX_USEFUL_TF)
    return list(TIMEFRAME_ORDER[idx + 1 : min(idx + 1 + max_count, cap + 1)])

# Technical scoring weights (MUST sum to 1.0)
TREND_WEIGHT = 0.22
EMA_WEIGHT = 0.10
RSI_WEIGHT = 0.13
MACD_WEIGHT = 0.16
CHANGE_WEIGHT = 0.06
PATTERN_WEIGHT = 0.06
DIVERGENCE_WEIGHT = 0.07
MULTI_TF_WEIGHT = 0.14
LEVEL_WEIGHT = 0.06

_WEIGHT_SUM = TREND_WEIGHT + EMA_WEIGHT + RSI_WEIGHT + MACD_WEIGHT + CHANGE_WEIGHT + PATTERN_WEIGHT + DIVERGENCE_WEIGHT + MULTI_TF_WEIGHT + LEVEL_WEIGHT
assert abs(_WEIGHT_SUM - 1.0) < 1e-6, f"Scoring weights must sum to 1.0, got {_WEIGHT_SUM}"

# Risk sizing
SL_ATR_MULTIPLIER = 1.5
TP_ATR_MULTIPLIER = 2.5
SL_PERCENT_FALLBACK = 0.003
TP_PERCENT_FALLBACK = 0.006

# Signal thresholds
SIGNAL_THRESHOLD = 0.05
TECHNICAL_SIGNAL_THRESHOLD = 0.15
NEWS_SIGNAL_THRESHOLD = 0.10
CONTEXT_SIGNAL_THRESHOLD = 0.12

# Asset classes
FIAT_ASSETS = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")
CRYPTO_ASSETS = ("ADA", "AVAX", "BCH", "BNB", "BTC", "DOGE", "DOT", "ETH", "LINK", "LTC", "MATIC", "SOL", "UNI", "XRP")
COMMODITY_ASSETS = ("XAU", "XAG")
