"""Unit tests for deterministic helpers — trace/comparison utilities."""

from app.services.agentscope.decision_helpers import (
    compute_deterministic_score,
    derive_trend_momentum,
)


def _outputs(tech_score=0.0, tech_signal="neutral", tech_conf=0.5,
             news_score=0.0, news_signal="neutral", news_conf=0.5,
             ctx_score=0.0, ctx_signal="neutral", ctx_conf=0.5):
    return {
        "technical-analyst": {"metadata": {"score": tech_score, "signal": tech_signal, "confidence": tech_conf}},
        "news-analyst": {"metadata": {"score": news_score, "signal": news_signal, "confidence": news_conf}},
        "market-context-analyst": {"metadata": {"score": ctx_score, "signal": ctx_signal, "confidence": ctx_conf}},
    }


def test_deterministic_score_not_zero_with_data() -> None:
    """With real Phase 1 data, the deterministic score should NOT be 0.0."""
    outputs = _outputs(tech_score=0.0607, tech_conf=0.1849,
                       news_score=-0.2, news_conf=0.45,
                       ctx_score=0.0, ctx_conf=0.0)
    score = compute_deterministic_score(outputs)
    assert score != 0.0, "Score should not be zero with real analysis data"


def test_deterministic_score_reflects_bearish_news() -> None:
    """Bearish news with weak bullish tech → net slightly negative or near-zero."""
    outputs = _outputs(tech_score=0.06, tech_conf=0.18,
                       news_score=-0.2, news_conf=0.45)
    score = compute_deterministic_score(outputs)
    assert score < 0.1, f"Expected near-zero or negative, got {score}"


def test_trend_momentum_from_snapshot() -> None:
    """Trend and momentum derived deterministically from snapshot."""
    snapshot = {"trend": "bullish", "macd_diff": -0.000537}
    trend, momentum = derive_trend_momentum(snapshot)
    assert trend == "bullish"
    assert momentum == "bearish"
