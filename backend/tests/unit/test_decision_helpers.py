"""Unit tests for deterministic decision helpers."""

from app.services.agentscope.decision_helpers import (
    compute_deterministic_score,
    compute_score_band,
    count_aligned_sources,
    derive_trend_momentum,
    validate_tool_calls,
)


def _analysis(tech_score=0.0, tech_signal="neutral", tech_conf=0.5,
              news_score=0.0, news_signal="neutral", news_conf=0.5,
              ctx_score=0.0, ctx_signal="neutral", ctx_conf=0.5):
    return {
        "technical-analyst": {"metadata": {"score": tech_score, "signal": tech_signal, "confidence": tech_conf}},
        "news-analyst": {"metadata": {"score": news_score, "signal": news_signal, "confidence": news_conf}},
        "market-context-analyst": {"metadata": {"score": ctx_score, "signal": ctx_signal, "confidence": ctx_conf}},
    }


# ── DM-1: Deterministic Combined Score ──

def test_all_bullish_positive_score() -> None:
    """All agents bullish → positive score."""
    outputs = _analysis(tech_score=0.5, news_score=0.3, ctx_score=0.2)
    score = compute_deterministic_score(outputs)
    assert score > 0


def test_all_bearish_negative_score() -> None:
    """All agents bearish → negative score."""
    outputs = _analysis(tech_score=-0.5, news_score=-0.3, ctx_score=-0.2)
    score = compute_deterministic_score(outputs)
    assert score < 0


def test_mixed_signals_near_zero() -> None:
    """Mixed signals → score near zero."""
    outputs = _analysis(tech_score=0.3, news_score=-0.3, ctx_score=0.0)
    score = compute_deterministic_score(outputs)
    assert -0.3 < score < 0.3


def test_tech_dominates_weighting() -> None:
    """Technical has 50% weight → should dominate."""
    outputs = _analysis(tech_score=0.8, news_score=-0.1, ctx_score=-0.1)
    score = compute_deterministic_score(outputs)
    assert score > 0  # Tech at 50% overwhelms news+ctx at 25% each


def test_debate_convergence_bonus() -> None:
    """Debate agrees with score direction → slight bonus."""
    outputs = _analysis(tech_score=0.4, news_score=0.2, ctx_score=0.1)
    score_no_debate = compute_deterministic_score(outputs)
    score_with_debate = compute_deterministic_score(outputs, debate_winner="bullish", debate_confidence=0.8)
    assert score_with_debate > score_no_debate


def test_debate_contradiction_dampens() -> None:
    """Debate contradicts score direction → dampens."""
    outputs = _analysis(tech_score=0.4, news_score=0.2, ctx_score=0.1)
    score_no_debate = compute_deterministic_score(outputs)
    score_contradicted = compute_deterministic_score(outputs, debate_winner="bearish", debate_confidence=0.8)
    assert score_contradicted < score_no_debate


def test_confidence_weighting() -> None:
    """High confidence agent has more influence than low confidence."""
    high_conf = _analysis(tech_score=0.5, tech_conf=0.9, news_score=-0.5, news_conf=0.1, ctx_score=0.0)
    score = compute_deterministic_score(high_conf)
    assert score > 0  # Tech at 0.9 confidence dominates news at 0.1


def test_score_clamped_to_range() -> None:
    """Score stays within [-1.0, 1.0]."""
    extreme = _analysis(tech_score=1.0, tech_conf=1.0, news_score=1.0, news_conf=1.0, ctx_score=1.0, ctx_conf=1.0)
    score = compute_deterministic_score(extreme, debate_winner="bullish", debate_confidence=1.0)
    assert -1.0 <= score <= 1.0


def test_empty_outputs_zero() -> None:
    """Empty analysis → 0.0."""
    score = compute_deterministic_score({})
    assert score == 0.0


# ── Score Band ──

def test_score_band_positive() -> None:
    band = compute_score_band(0.5)
    assert band[0] < 0.5 < band[1]
    assert abs((band[1] - band[0]) - 0.2) < 0.001


def test_score_band_zero() -> None:
    band = compute_score_band(0.0)
    assert band == (-0.20, 0.20)


def test_score_band_negative() -> None:
    band = compute_score_band(-0.4)
    assert band[0] < -0.4 < band[1]


# ── DM-5: Aligned Sources ──

def test_aligned_bullish_all_agree() -> None:
    """All agents bullish → 3 aligned."""
    outputs = _analysis(tech_score=0.3, tech_signal="bullish",
                        news_score=0.2, news_signal="bullish",
                        ctx_score=0.1, ctx_signal="bullish")
    assert count_aligned_sources(outputs, "bullish") == 3


def test_aligned_bearish_partial() -> None:
    """Only tech bearish → 1 aligned."""
    outputs = _analysis(tech_score=-0.3, tech_signal="bearish",
                        news_score=0.0, news_signal="neutral",
                        ctx_score=0.1, ctx_signal="neutral")
    assert count_aligned_sources(outputs, "bearish") == 1


def test_aligned_none() -> None:
    """All neutral → 0 aligned."""
    outputs = _analysis()
    assert count_aligned_sources(outputs, "bullish") == 0


def test_aligned_by_score_not_signal() -> None:
    """Signal says neutral but score is positive → counted as aligned."""
    outputs = _analysis(tech_score=0.1, tech_signal="neutral")
    assert count_aligned_sources(outputs, "bullish") == 1


# ── DM-6: Trend/Momentum Derivation ──

def test_derive_bearish_trend_bullish_momentum() -> None:
    snapshot = {"trend": "bearish", "macd_diff": 0.001}
    trend, momentum = derive_trend_momentum(snapshot)
    assert trend == "bearish"
    assert momentum == "bullish"


def test_derive_bullish_trend_bearish_momentum() -> None:
    snapshot = {"trend": "bullish", "macd_diff": -0.002}
    trend, momentum = derive_trend_momentum(snapshot)
    assert trend == "bullish"
    assert momentum == "bearish"


def test_derive_neutral_zero_macd() -> None:
    snapshot = {"trend": "neutral", "macd_diff": 0.0}
    trend, momentum = derive_trend_momentum(snapshot)
    assert trend == "neutral"
    assert momentum == "neutral"


def test_derive_normalizes_up_down() -> None:
    """'up'/'down' normalized to 'bullish'/'bearish'."""
    snapshot = {"trend": "up", "macd_diff": 0.0}
    trend, _ = derive_trend_momentum(snapshot)
    assert trend == "bullish"

    snapshot2 = {"trend": "down", "macd_diff": 0.0}
    trend2, _ = derive_trend_momentum(snapshot2)
    assert trend2 == "bearish"


# ── DM-2: Tool Call Validation ──

def test_validate_all_tools_present_buy() -> None:
    tools = {"decision_gating": {}, "contradiction_detector": {}, "trade_sizing": {}}
    ok, missing = validate_tool_calls(tools, "BUY")
    assert ok is True
    assert missing == []


def test_validate_hold_no_trade_sizing_ok() -> None:
    """HOLD doesn't require trade_sizing."""
    tools = {"decision_gating": {}, "contradiction_detector": {}}
    ok, missing = validate_tool_calls(tools, "HOLD")
    assert ok is True


def test_validate_missing_decision_gating() -> None:
    tools = {"contradiction_detector": {}, "trade_sizing": {}}
    ok, missing = validate_tool_calls(tools, "BUY")
    assert ok is False
    assert "decision_gating" in missing


def test_validate_missing_contradiction_detector() -> None:
    tools = {"decision_gating": {}, "trade_sizing": {}}
    ok, missing = validate_tool_calls(tools, "SELL")
    assert ok is False
    assert "contradiction_detector" in missing


def test_validate_missing_trade_sizing_for_sell() -> None:
    tools = {"decision_gating": {}, "contradiction_detector": {}}
    ok, missing = validate_tool_calls(tools, "SELL")
    assert ok is False
    assert "trade_sizing" in missing


def test_validate_empty_tools() -> None:
    ok, missing = validate_tool_calls({}, "BUY")
    assert ok is False
    assert len(missing) == 3
