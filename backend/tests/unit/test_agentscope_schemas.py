import pytest
from pydantic import ValidationError
from app.services.agentscope.schemas import (
    TechnicalAnalysisResult, NewsAnalysisResult, MarketContextResult,
    DebateThesis, DebateResult, TraderDecisionDraft,
    RiskAssessmentResult, ExecutionPlanResult,
)

def test_technical_analysis_valid():
    r = TechnicalAnalysisResult(signal="bullish", score=0.45, confidence=0.72, setup_state="actionable", summary="Strong uptrend")
    assert r.signal == "bullish"
    assert r.degraded is False

def test_technical_analysis_score_bounds_clamped():
    # Score 1.5 is clamped to 1.0 (not rejected)
    r = TechnicalAnalysisResult(signal="bullish", score=1.5, confidence=0.5, setup_state="actionable", summary="test")
    assert r.score == 1.0

def test_news_analysis_valid():
    r = NewsAnalysisResult(signal="bearish", score=-0.3, confidence=0.6, coverage="medium", evidence_strength=0.7, summary="Negative news")
    assert r.coverage == "medium"

def test_debate_result_valid():
    r = DebateResult(finished=True, winning_side="bullish", confidence=0.8, reason="Strong bull case")
    assert r.finished is True

def test_debate_result_unfinished():
    r = DebateResult(finished=False)
    assert r.winning_side is None
    assert r.confidence == 0.5

def test_trader_decision_buy_without_levels_disables_execution():
    # BUY without entry/SL/TP auto-disables execution instead of raising
    r = TraderDecisionDraft(decision="BUY", confidence=0.7, combined_score=0.4, execution_allowed=True, reason="Go long", entry=None, stop_loss=None, take_profit=None)
    assert r.execution_allowed is False

def test_trader_decision_hold_no_levels_needed():
    r = TraderDecisionDraft(decision="HOLD", confidence=0.5, combined_score=0.1, execution_allowed=False, reason="No signal")
    assert r.entry is None

def test_trader_decision_buy_level_order():
    with pytest.raises(ValidationError):
        TraderDecisionDraft(decision="BUY", confidence=0.7, combined_score=0.4, execution_allowed=True, reason="Go long", entry=1.1000, stop_loss=1.1100, take_profit=1.1200)

def test_risk_assessment_valid():
    r = RiskAssessmentResult(accepted=True, suggested_volume=0.1)
    assert r.reasons == []

def test_execution_plan_valid():
    r = ExecutionPlanResult(decision="BUY", should_execute=True, side="BUY", volume=0.1, reason="All checks passed")
    assert r.degraded is False
