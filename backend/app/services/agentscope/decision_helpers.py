"""Deterministic decision helpers — pre-compute values before the trader-agent LLM call.

Removes LLM dependency for critical numerical inputs:
- combined_score: weighted synthesis of Phase 1 scores + debate adjustment
- aligned_sources: count of Phase 1 agents agreeing with proposed direction
- trend/momentum: derived from snapshot for contradiction_detector
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Weights for deterministic combined score
SCORE_WEIGHTS = {
    "technical-analyst": 0.50,
    "news-analyst": 0.25,
    "market-context-analyst": 0.25,
}

# Max LLM adjustment band (±20%)
LLM_ADJUSTMENT_BAND = 0.20


def compute_deterministic_score(
    analysis_outputs: dict[str, dict],
    debate_winner: str | None = None,
    debate_confidence: float = 0.5,
) -> float:
    """Compute a deterministic combined_score from Phase 1 outputs + debate.

    Returns a value in [-1.0, 1.0].
    """
    weighted_score = 0.0
    total_weight = 0.0

    for agent_name, weight in SCORE_WEIGHTS.items():
        output = analysis_outputs.get(agent_name, {})
        meta = output.get("metadata", {})
        score = _safe_float(meta.get("score", 0.0))
        confidence = _safe_float(meta.get("confidence", 0.5))

        # Weight by both configured weight and agent confidence
        effective_weight = weight * confidence
        weighted_score += score * effective_weight
        total_weight += effective_weight

    if total_weight > 0:
        base_score = weighted_score / total_weight
    else:
        base_score = 0.0

    # Debate adjustment: ±10% bonus if debate converges with score direction
    if debate_winner in ("bullish",) and base_score > 0:
        base_score *= (1 + debate_confidence * 0.10)
    elif debate_winner in ("bearish",) and base_score < 0:
        base_score *= (1 + debate_confidence * 0.10)
    elif debate_winner in ("bullish",) and base_score < 0:
        # Debate contradicts score → dampen
        base_score *= (1 - debate_confidence * 0.05)
    elif debate_winner in ("bearish",) and base_score > 0:
        base_score *= (1 - debate_confidence * 0.05)

    return round(max(-1.0, min(1.0, base_score)), 4)


def compute_score_band(deterministic_score: float) -> tuple[float, float]:
    """Return the (min, max) band the LLM can adjust the score within."""
    if deterministic_score == 0.0:
        return (-LLM_ADJUSTMENT_BAND, LLM_ADJUSTMENT_BAND)
    band = abs(deterministic_score) * LLM_ADJUSTMENT_BAND
    lo = max(-1.0, deterministic_score - band)
    hi = min(1.0, deterministic_score + band)
    return (round(lo, 4), round(hi, 4))


def count_aligned_sources(
    analysis_outputs: dict[str, dict],
    direction: str,
) -> int:
    """Count how many Phase 1 agents agree with the proposed direction.

    Args:
        direction: "bullish" or "bearish"
    """
    count = 0
    for agent_name in ("technical-analyst", "news-analyst", "market-context-analyst"):
        output = analysis_outputs.get(agent_name, {})
        meta = output.get("metadata", {})
        signal = str(meta.get("signal", "neutral")).lower()
        score = _safe_float(meta.get("score", 0.0))

        if direction == "bullish" and (signal == "bullish" or score > 0.05):
            count += 1
        elif direction == "bearish" and (signal == "bearish" or score < -0.05):
            count += 1
    return count


def derive_trend_momentum(snapshot: dict) -> tuple[str, str]:
    """Derive trend and momentum from snapshot deterministically.

    Returns (trend, momentum) as strings for contradiction_detector.
    """
    trend = str(snapshot.get("trend", "neutral")).lower()
    # Normalize trend labels
    if trend in ("up", "uptrend"):
        trend = "bullish"
    elif trend in ("down", "downtrend"):
        trend = "bearish"
    elif trend not in ("bullish", "bearish"):
        trend = "neutral"

    # Momentum from MACD diff sign
    macd_diff = _safe_float(snapshot.get("macd_diff", 0.0))
    if macd_diff > 0:
        momentum = "bullish"
    elif macd_diff < 0:
        momentum = "bearish"
    else:
        momentum = "neutral"

    return trend, momentum


def validate_tool_calls(
    tool_invocations: dict[str, Any],
    decision: str,
) -> tuple[bool, list[str]]:
    """Validate that the trader-agent called the required tools.

    Returns (valid, missing_tools).
    Required tools:
    - decision_gating: ALWAYS
    - contradiction_detector: ALWAYS
    - trade_sizing: only if BUY or SELL
    """
    required = {"decision_gating", "contradiction_detector"}
    if decision in ("BUY", "SELL"):
        required.add("trade_sizing")

    called = set(tool_invocations.keys())
    missing = required - called

    return len(missing) == 0, sorted(missing)


def _safe_float(val: Any) -> float:
    try:
        f = float(val)
        if not __import__("math").isfinite(f):
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0
