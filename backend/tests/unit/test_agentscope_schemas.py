"""Unit tests for LLM-First schemas."""
import pytest
from pydantic import ValidationError
from app.services.agentscope.schemas import (
    TechnicalAnalysisResult, NewsAnalysisResult, MarketContextResult,
    DebateThesis, DebateResult, TraderDecisionDraft,
    RiskAssessmentResult, ExecutionPlanResult,
)


# ── Technical Analyst ──

def test_technical_analysis_valid():
    r = TechnicalAnalysisResult(
        structural_bias="bullish", local_momentum="bearish",
        setup_quality="medium", summary="Strong uptrend with mixed momentum",
        tradability="high",
    )
    assert r.structural_bias == "bullish"
    assert r.tradability == "high"
    assert r.degraded is False


def test_technical_analysis_normalizes_signals():
    r = TechnicalAnalysisResult(
        structural_bias="buy", local_momentum="sell",
        summary="test",
    )
    assert r.structural_bias == "bullish"
    assert r.local_momentum == "bearish"


def test_technical_analysis_numeric_tradability():
    """Legacy numeric tradability converted to category."""
    r = TechnicalAnalysisResult(
        structural_bias="neutral", summary="test",
        tradability="0.8",
    )
    assert r.tradability == "high"


def test_technical_analysis_defaults():
    r = TechnicalAnalysisResult(summary="minimal")
    assert r.structural_bias == "neutral"
    assert r.setup_quality == "none"
    assert r.key_levels == []
    assert r.patterns_found == []


# ── News Analyst ──

def test_news_analysis_valid():
    r = NewsAnalysisResult(
        sentiment="bearish", coverage="medium",
        key_drivers=["NFP weak expectations"],
        summary="Negative outlook",
    )
    assert r.sentiment == "bearish"
    assert r.coverage == "medium"


def test_news_analysis_none_coverage_forces_neutral():
    r = NewsAnalysisResult(
        sentiment="bullish", coverage="none",
        summary="test",
    )
    assert r.sentiment == "neutral"


def test_news_analysis_legacy_signal_field():
    """Legacy 'signal' field maps to 'sentiment'."""
    r = NewsAnalysisResult(signal="bearish", coverage="low", summary="test")
    assert r.sentiment == "bearish"


# ── Market Context ──

def test_market_context_valid():
    r = MarketContextResult(
        regime="calm", session_quality="high",
        execution_risk="low", summary="London session active",
    )
    assert r.regime == "calm"
    assert r.session_quality == "high"


def test_market_context_numeric_quality():
    """Legacy numeric values converted to categories."""
    r = MarketContextResult(
        regime="volatile", session_quality="0.8",
        execution_risk="0.2", summary="test",
    )
    assert r.session_quality == "high"
    assert r.execution_risk == "low"


# ── Debate ──

def test_debate_result_bullish():
    r = DebateResult(winner="bullish", conviction="strong", key_argument="Momentum confirmed")
    assert r.winner == "bullish"
    assert r.conviction == "strong"


def test_debate_result_no_edge():
    r = DebateResult(winner="no_edge", conviction="weak")
    assert r.winner == "no_edge"


def test_debate_result_neutral_maps_to_no_edge():
    """Legacy 'neutral' winner maps to 'no_edge'."""
    r = DebateResult(winner="neutral")
    assert r.winner == "no_edge"


def test_debate_result_legacy_winning_side():
    """Legacy 'winning_side' field maps to 'winner'."""
    r = DebateResult(winning_side="bearish", conviction="moderate")
    assert r.winner == "bearish"


def test_debate_result_numeric_conviction():
    """Legacy numeric confidence maps to conviction category."""
    r = DebateResult(winner="bullish", conviction="0.8")
    assert r.conviction == "strong"

    r2 = DebateResult(winner="bearish", conviction="0.5")
    assert r2.conviction == "moderate"

    r3 = DebateResult(winner="no_edge", conviction="0.2")
    assert r3.conviction == "weak"


def test_debate_thesis_valid():
    r = DebateThesis(
        thesis="Bull case based on support holding",
        arguments=["RSI divergence", "hammer pattern"],
        confidence=0.7,
        invalidation_conditions=["price breaks below 1.1500"],
    )
    assert len(r.arguments) == 2
    assert r.confidence == 0.7


# ── Trader ──

def test_trader_decision_buy():
    r = TraderDecisionDraft(
        decision="BUY", conviction=0.75,
        reasoning="Strong confluence", key_level=1.1534,
        invalidation="price below 1.1500",
    )
    assert r.decision == "BUY"
    assert r.conviction == 0.75


def test_trader_decision_hold():
    r = TraderDecisionDraft(
        decision="HOLD", conviction=0.3,
        reasoning="No clear edge",
    )
    assert r.key_level is None
    assert r.invalidation is None


def test_trader_decision_normalizes():
    r = TraderDecisionDraft(decision="bullish", conviction=0.5, reasoning="test")
    assert r.decision == "BUY"


def test_trader_legacy_confidence_maps_to_conviction():
    r = TraderDecisionDraft(decision="HOLD", confidence=0.4, reasoning="test")
    assert r.conviction == 0.4


# ── Risk Manager ──

def test_risk_assessment_approved():
    r = RiskAssessmentResult(approved=True, adjusted_volume=0.5, reasoning="Within limits")
    assert r.risk_flags == []


def test_risk_assessment_legacy_fields():
    """Legacy 'accepted'/'suggested_volume'/'reasons' map to new fields."""
    r = RiskAssessmentResult(accepted=True, suggested_volume=0.3, reasons=["margin ok"])
    assert r.approved is True
    assert r.adjusted_volume == 0.3
    assert r.risk_flags == ["margin ok"]


# ── Execution Optimizer ──

def test_execution_plan_valid():
    r = ExecutionPlanResult(
        order_type="limit", timing="wait_pullback",
        reasoning="Better entry on retracement",
        expected_slippage="low",
    )
    assert r.order_type == "limit"
    assert r.timing == "wait_pullback"


def test_execution_plan_defaults():
    r = ExecutionPlanResult(reasoning="immediate execution")
    assert r.order_type == "market"
    assert r.timing == "immediate"
    assert r.expected_slippage == "medium"
