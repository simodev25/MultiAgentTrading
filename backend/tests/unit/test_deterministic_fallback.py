"""Unit tests for trader-agent deterministic fallback — verifies proper score injection."""

from app.services.agentscope.decision_helpers import (
    compute_deterministic_score,
    count_aligned_sources,
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
    # Simulating the run-2 scenario: tech=0.06, news=-0.2, ctx=0
    outputs = _outputs(tech_score=0.0607, tech_conf=0.1849,
                       news_score=-0.2, news_conf=0.45,
                       ctx_score=0.0, ctx_conf=0.0)
    score = compute_deterministic_score(outputs)
    # Should be non-zero: tech is slightly positive, news is bearish
    assert score != 0.0, "Score should not be zero with real analysis data"


def test_deterministic_score_reflects_bearish_news() -> None:
    """Bearish news with weak bullish tech → net slightly negative or near-zero."""
    outputs = _outputs(tech_score=0.06, tech_conf=0.18,
                       news_score=-0.2, news_conf=0.45)
    score = compute_deterministic_score(outputs)
    # News bearish dominates over weak tech
    assert score < 0.1, f"Expected near-zero or negative, got {score}"


def test_aligned_sources_counted_from_outputs() -> None:
    """Aligned sources should count from real Phase 1 metadata."""
    outputs = _outputs(tech_score=0.3, tech_signal="bullish",
                       news_score=-0.2, news_signal="bearish")
    # For bullish direction: only tech is aligned
    assert count_aligned_sources(outputs, "bullish") == 1
    # For bearish direction: only news is aligned
    assert count_aligned_sources(outputs, "bearish") == 1


def test_trend_momentum_from_snapshot() -> None:
    """Trend and momentum derived deterministically from snapshot."""
    snapshot = {"trend": "bullish", "macd_diff": -0.000537}
    trend, momentum = derive_trend_momentum(snapshot)
    assert trend == "bullish"
    assert momentum == "bearish"  # MACD negative = bearish momentum


def test_decision_gating_gets_real_values() -> None:
    """Simulate what _build_tool_kwargs should produce for decision_gating."""
    outputs = _outputs(tech_score=0.4, tech_conf=0.7,
                       news_score=0.3, news_conf=0.6,
                       ctx_score=0.1, ctx_conf=0.5)
    score = compute_deterministic_score(outputs)
    direction = "bullish" if score > 0 else "bearish"
    aligned = count_aligned_sources(outputs, direction)

    assert score > 0.2, f"Score should be clearly positive: {score}"
    assert aligned >= 2, f"At least 2 agents should be aligned: {aligned}"


def test_decision_gating_would_pass_with_real_data() -> None:
    """With strong consensus, the gating should pass (score > 0.22, conf > 0.28)."""
    from app.services.mcp.trading_server import decision_gating

    outputs = _outputs(tech_score=0.5, tech_conf=0.8,
                       news_score=0.3, news_conf=0.7,
                       ctx_score=0.2, ctx_conf=0.6)
    score = compute_deterministic_score(outputs)
    confs = [0.8, 0.7, 0.6]
    avg_conf = sum(confs) / len(confs)
    direction = "bullish"
    aligned = count_aligned_sources(outputs, direction)

    result = decision_gating(
        combined_score=abs(score),
        confidence=avg_conf,
        aligned_sources=aligned,
        mode="balanced",
    )
    assert result["gates_passed"] is True, f"Gates should pass: {result['blocked_by']}"


def test_decision_gating_would_block_weak_data() -> None:
    """With weak/mixed data, the gating should block."""
    from app.services.mcp.trading_server import decision_gating

    # Simulating run-2: tech=0.06/0.18, news=-0.2/0.45, ctx=0/0
    outputs = _outputs(tech_score=0.06, tech_conf=0.18,
                       news_score=-0.2, news_conf=0.45,
                       ctx_score=0.0, ctx_conf=0.0)
    score = compute_deterministic_score(outputs)
    confs = [0.18, 0.45, 0.0]
    avg_conf = sum(confs) / len(confs)

    result = decision_gating(
        combined_score=abs(score),
        confidence=avg_conf,
        aligned_sources=0,
        mode="balanced",
    )
    assert result["gates_passed"] is False, "Gates should block weak/mixed data"
