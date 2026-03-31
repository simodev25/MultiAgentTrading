from app.services.mcp.trading_server import (
    technical_scoring, news_evidence_scoring, news_validation,
    decision_gating, contradiction_detector, trade_sizing, risk_evaluation,
)


def test_technical_scoring_bullish():
    result = technical_scoring(
        trend="up", rsi=62.0, macd_diff=0.0015, atr=0.0045,
        ema_fast_above_slow=True, change_pct=0.3,
    )
    assert result["signal"] == "bullish"
    assert result["score"] > 0
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["setup_state"] in ("non_actionable", "conditional", "weak_actionable", "actionable", "high_conviction")


def test_technical_scoring_neutral():
    result = technical_scoring(trend="neutral", rsi=50.0, macd_diff=0.0, atr=0.005)
    assert result["signal"] == "neutral"
    assert abs(result["score"]) < 0.15


def test_decision_gating_conservative_blocks_low_score():
    result = decision_gating(combined_score=0.10, confidence=0.50, aligned_sources=2, mode="conservative")
    assert result["execution_allowed"] is False
    assert any("score" in b.lower() for b in result["blocked_by"])


def test_decision_gating_permissive_allows_lower_score():
    result = decision_gating(combined_score=0.15, confidence=0.30, aligned_sources=1, mode="permissive")
    assert result["execution_allowed"] is True


def test_contradiction_detector_major():
    result = contradiction_detector(macd_diff=0.002, atr=0.005, trend="up", momentum="bearish")
    assert result["severity"] == "major"
    assert result["penalty"] > 0.10


def test_contradiction_detector_no_conflict():
    result = contradiction_detector(macd_diff=0.001, atr=0.005, trend="up", momentum="bullish")
    assert result["severity"] == "none"
    assert result["penalty"] == 0.0


def test_trade_sizing_buy():
    result = trade_sizing(price=1.1000, atr=0.0050, decision_side="BUY")
    assert result["stop_loss"] < result["entry"] < result["take_profit"]


def test_trade_sizing_sell():
    result = trade_sizing(price=1.1000, atr=0.0050, decision_side="SELL")
    assert result["take_profit"] < result["entry"] < result["stop_loss"]


def test_technical_scoring_patterns_use_signal_field():
    """Patterns from pattern_detector use 'signal' not 'direction'.
    Bullish patterns must contribute positive score, bearish negative,
    neutral (doji) must contribute zero."""
    result = technical_scoring(
        trend="neutral", rsi=50.0, macd_diff=0.0, atr=0.005,
        patterns=[
            {"signal": "bullish", "type": "bullish_engulfing"},
            {"signal": "bullish", "type": "pin_bar"},
            {"signal": "neutral", "type": "doji"},
            {"signal": "bearish", "type": "bearish_engulfing"},
        ],
    )
    # 2 bullish (+2) + 1 neutral (0) + 1 bearish (-1) = net +1
    assert result["components"]["pattern"] > 0, (
        f"Net bullish patterns should give positive pattern_score, got {result['components']['pattern']}"
    )


def test_technical_scoring_patterns_legacy_direction_field():
    """Legacy patterns with 'direction' field should still work."""
    result = technical_scoring(
        trend="neutral", rsi=50.0, macd_diff=0.0, atr=0.005,
        patterns=[{"direction": "bullish"}, {"direction": "bearish"}],
    )
    # 1 bullish + 1 bearish = net 0
    assert result["components"]["pattern"] == 0


def test_news_evidence_scoring_empty():
    result = news_evidence_scoring()
    assert result["coverage"] == "none"
    assert result["signal"] == "neutral"


def test_news_validation_passthrough():
    result = news_validation(news_output={"signal": "bullish"})
    assert result["validated_output"]["signal"] == "bullish"
