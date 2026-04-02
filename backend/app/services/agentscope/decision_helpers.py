"""Decision helpers — utility functions for traces and advisory checks.

LLM-First: these functions are NO LONGER in the decision loop.
They exist for:
- Debug traces (compute_deterministic_score for comparison)
- Advisory warnings (validate_tool_calls as warning, not blocker)
- Factual derivation (derive_trend_momentum from snapshot)
"""

from __future__ import annotations

import math
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Weights for deterministic combined score (trace/comparison only)
SCORE_WEIGHTS = {
    "technical-analyst": 0.50,
    "news-analyst": 0.25,
    "market-context-analyst": 0.25,
}


def compute_deterministic_score(
    analysis_outputs: dict[str, dict],
    debate_winner: str | None = None,
    debate_confidence: float = 0.5,
) -> float:
    """Compute a deterministic combined_score from Phase 1 outputs + debate.

    TRACE ONLY — not used in the decision loop. Kept for debug traces
    and A/B comparison with LLM decisions.

    Returns a value in [-1.0, 1.0].
    """
    weighted_score = 0.0
    total_weight = 0.0

    for agent_name, weight in SCORE_WEIGHTS.items():
        output = analysis_outputs.get(agent_name, {})
        meta = output.get("metadata", {})
        score = _safe_float(meta.get("score", 0.0))
        confidence = _safe_float(meta.get("confidence", 0.5))

        effective_weight = weight * confidence
        weighted_score += score * effective_weight
        total_weight += effective_weight

    if total_weight > 0:
        base_score = weighted_score / total_weight
    else:
        base_score = 0.0

    # Debate adjustment
    if debate_winner in ("bullish",) and base_score > 0:
        base_score *= (1 + debate_confidence * 0.10)
    elif debate_winner in ("bearish",) and base_score < 0:
        base_score *= (1 + debate_confidence * 0.10)
    elif debate_winner in ("bullish",) and base_score < 0:
        base_score *= (1 - debate_confidence * 0.05)
    elif debate_winner in ("bearish",) and base_score > 0:
        base_score *= (1 - debate_confidence * 0.05)

    return round(max(-1.0, min(1.0, base_score)), 4)


def derive_trend_momentum(snapshot: dict) -> tuple[str, str]:
    """Derive trend and momentum from snapshot deterministically.

    Returns (trend, momentum) as strings. Used for traces and
    as factual data for contradiction_detector.
    """
    trend = str(snapshot.get("trend", "neutral")).lower()
    if trend in ("up", "uptrend"):
        trend = "bullish"
    elif trend in ("down", "downtrend"):
        trend = "bearish"
    elif trend not in ("bullish", "bearish"):
        trend = "neutral"

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
    """Check if the trader-agent called expected tools.

    ADVISORY ONLY — returns (valid, missing_tools) for warning logs.
    Does NOT block execution.
    """
    expected = {"decision_gating", "contradiction_detector"}
    if decision in ("BUY", "SELL"):
        expected.add("trade_sizing")

    called = set(tool_invocations.keys())
    missing = expected - called

    return len(missing) == 0, sorted(missing)


def validate_risk_tool_calls(
    tool_invocations: dict[str, Any],
    decision: str,
) -> tuple[bool, list[str]]:
    """Check if the risk-manager called expected tools.

    ADVISORY ONLY — returns (valid, missing_tools) for warning logs.
    """
    if str(decision or "").strip().upper() == "HOLD":
        return True, []

    expected = {"portfolio_risk_evaluation"}
    called = set(tool_invocations.keys())
    missing = expected - called
    return len(missing) == 0, sorted(missing)


def _safe_float(val: Any) -> float:
    try:
        f = float(val)
        if not math.isfinite(f):
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0
