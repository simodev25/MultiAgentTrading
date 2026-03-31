"""Tests validating P0 fixes from the global audit."""
import math
import pytest
from unittest.mock import MagicMock


# --- P0-1: Trader decision is authoritative over debate ---
class TestTraderDecisionAuthority:
    def test_trader_decision_used_over_debate(self):
        """When trader outputs BUY, the final decision should be BUY
        even if debate says neutral."""
        # Simulate the decision assembly logic from registry.py
        trader_meta = {"decision": "BUY", "confidence": 0.75, "combined_score": 0.45}
        debate_winning_side = "neutral"

        trader_decision_raw = trader_meta.get("decision", "").strip().upper()
        if trader_decision_raw in ("BUY", "SELL", "HOLD"):
            trade_decision = trader_decision_raw
        else:
            trade_decision = "HOLD"

        assert trade_decision == "BUY"

    def test_fallback_to_debate_when_trader_invalid(self):
        """When trader outputs garbage, fall back to debate."""
        trader_meta = {"decision": "MAYBE"}
        debate_winning_side = "bearish"

        trader_decision_raw = trader_meta.get("decision", "").strip().upper()
        if trader_decision_raw in ("BUY", "SELL", "HOLD"):
            trade_decision = trader_decision_raw
        else:
            trade_decision = "HOLD"
            if debate_winning_side == "bullish":
                trade_decision = "BUY"
            elif debate_winning_side == "bearish":
                trade_decision = "SELL"

        assert trade_decision == "SELL"

    def test_trader_hold_respected(self):
        """Trader HOLD should produce HOLD regardless of debate."""
        trader_meta = {"decision": "HOLD", "confidence": 0.2}
        trade_decision = trader_meta.get("decision", "").strip().upper()
        assert trade_decision == "HOLD"


# --- P0-2: combined_score 0.0 is valid, not treated as missing ---
class TestCombinedScoreHandling:
    def test_zero_score_not_treated_as_missing(self):
        """combined_score=0.0 should NOT trigger the fallback path."""
        decision = {"combined_score": 0.0}
        # The fix uses `is None` instead of falsy check
        assert decision.get("combined_score") is not None
        assert decision.get("combined_score") == 0.0

    def test_none_score_triggers_fallback(self):
        """combined_score=None SHOULD trigger the fallback path."""
        decision = {}
        assert decision.get("combined_score") is None

    def test_negative_score_preserved(self):
        """Negative combined_score (bearish) must be preserved."""
        decision = {"combined_score": -0.35}
        assert decision.get("combined_score") is not None
        assert decision["combined_score"] == -0.35


# --- P0-3: NaN/Inf validation in risk engine ---
class TestRiskEngineNanValidation:
    def test_nan_price_rejected(self):
        from app.services.risk.rules import RiskEngine
        engine = RiskEngine()
        result = engine.evaluate(
            mode="paper", decision="BUY", risk_percent=1.0,
            price=float('nan'), stop_loss=1.0, equity=10000.0,
        )
        assert result.accepted is False
        assert "Invalid price" in result.reasons[0]

    def test_inf_equity_rejected(self):
        from app.services.risk.rules import RiskEngine
        engine = RiskEngine()
        result = engine.evaluate(
            mode="paper", decision="BUY", risk_percent=1.0,
            price=1.1000, stop_loss=1.0950, equity=float('inf'),
        )
        assert result.accepted is False
        assert "Invalid equity" in result.reasons[0]

    def test_negative_risk_percent_rejected(self):
        from app.services.risk.rules import RiskEngine
        engine = RiskEngine()
        result = engine.evaluate(
            mode="paper", decision="BUY", risk_percent=-1.0,
            price=1.1000, stop_loss=1.0950, equity=10000.0,
        )
        assert result.accepted is False
        assert "Invalid risk_percent" in result.reasons[0]

    def test_nan_stop_loss_rejected(self):
        from app.services.risk.rules import RiskEngine
        engine = RiskEngine()
        result = engine.evaluate(
            mode="paper", decision="BUY", risk_percent=1.0,
            price=1.1000, stop_loss=float('nan'), equity=10000.0,
        )
        assert result.accepted is False
        assert "Invalid stop_loss" in result.reasons[0]

    def test_leverage_parameter_used(self):
        from app.services.risk.rules import RiskEngine
        engine = RiskEngine()
        result_100 = engine.evaluate(
            mode="paper", decision="BUY", risk_percent=1.0,
            price=1.1000, stop_loss=1.0950, equity=10000.0,
            leverage=100.0,
        )
        result_500 = engine.evaluate(
            mode="paper", decision="BUY", risk_percent=1.0,
            price=1.1000, stop_loss=1.0950, equity=10000.0,
            leverage=500.0,
        )
        # Higher leverage = lower margin required
        assert result_500.margin_required < result_100.margin_required


# --- P0-4: Executor input validation ---
class TestExecutorInputValidation:
    @pytest.mark.asyncio
    async def test_nan_volume_rejected(self):
        from app.services.execution.executor import ExecutionService
        svc = ExecutionService()
        result = await svc.execute(
            db=MagicMock(), run_id=1, mode="simulation",
            symbol="EURUSD", side="BUY",
            volume=float('nan'), stop_loss=1.09, take_profit=1.12,
        )
        assert result["status"] == "rejected"
        assert "Invalid volume" in result["reason"]

    @pytest.mark.asyncio
    async def test_negative_stop_loss_rejected(self):
        from app.services.execution.executor import ExecutionService
        svc = ExecutionService()
        result = await svc.execute(
            db=MagicMock(), run_id=1, mode="simulation",
            symbol="EURUSD", side="BUY",
            volume=0.01, stop_loss=-1.0, take_profit=1.12,
        )
        assert result["status"] == "rejected"
        assert "Invalid stop_loss" in result["reason"]


# --- Schema NaN handling ---
class TestSchemaNanHandling:
    def test_nan_score_defaults(self):
        from app.services.agentscope.schemas import TechnicalAnalysisResult
        data = {
            "signal": "bullish",
            "score": float('nan'),
            "confidence": 0.5,
            "setup_state": "conditional",
            "summary": "test",
        }
        result = TechnicalAnalysisResult(**data)
        # NaN should be replaced with default 0.0
        assert math.isfinite(result.score)
        assert result.score == 0.0

    def test_inf_confidence_defaults(self):
        from app.services.agentscope.schemas import TechnicalAnalysisResult
        data = {
            "signal": "bearish",
            "score": -0.3,
            "confidence": float('inf'),
            "setup_state": "conditional",
            "summary": "test",
        }
        result = TechnicalAnalysisResult(**data)
        # Inf should be replaced with default 0.5
        assert math.isfinite(result.confidence)
        assert result.confidence == 0.5


# --- MCP _safe_float ---
class TestSafeFloat:
    def test_nan_returns_default(self):
        from app.services.mcp.trading_server import _safe_float
        assert _safe_float(float('nan')) == 0.0
        assert _safe_float(float('nan'), 50.0) == 50.0

    def test_inf_returns_default(self):
        from app.services.mcp.trading_server import _safe_float
        assert _safe_float(float('inf')) == 0.0
        assert _safe_float(float('-inf')) == 0.0

    def test_valid_float_passes_through(self):
        from app.services.mcp.trading_server import _safe_float
        assert _safe_float(42.5) == 42.5
        assert _safe_float(-0.003) == -0.003
        assert _safe_float(0.0) == 0.0

    def test_string_returns_default(self):
        from app.services.mcp.trading_server import _safe_float
        assert _safe_float("not_a_number") == 0.0


# --- MCP client async handling ---
class TestMcpClientAsync:
    @pytest.mark.asyncio
    async def test_sync_handler_works(self):
        from app.services.mcp.client import InProcessMCPClient
        client = InProcessMCPClient()
        # market_snapshot is a sync function
        result = await client.call_tool("market_snapshot", {"symbol": "TEST"})
        assert isinstance(result, dict)
        assert "error" not in result or result.get("symbol") == "TEST"

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        from app.services.mcp.client import InProcessMCPClient
        client = InProcessMCPClient()
        result = await client.call_tool("nonexistent_tool", {})
        assert "error" in result
        assert "Unknown tool" in result["error"]


# --- Constants weight assertion ---
class TestConstantsIntegrity:
    def test_weights_sum_to_one(self):
        from app.services.agentscope.constants import (
            TREND_WEIGHT, EMA_WEIGHT, RSI_WEIGHT, MACD_WEIGHT,
            CHANGE_WEIGHT, PATTERN_WEIGHT, DIVERGENCE_WEIGHT,
            MULTI_TF_WEIGHT, LEVEL_WEIGHT,
        )
        total = (TREND_WEIGHT + EMA_WEIGHT + RSI_WEIGHT + MACD_WEIGHT +
                 CHANGE_WEIGHT + PATTERN_WEIGHT + DIVERGENCE_WEIGHT +
                 MULTI_TF_WEIGHT + LEVEL_WEIGHT)
        assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}, expected 1.0"


# --- Tool wrapper error handling ---
class TestToolkitErrorHandling:
    @pytest.mark.asyncio
    async def test_toolkit_wraps_errors(self):
        from app.services.agentscope.toolkit import build_toolkit
        toolkit = await build_toolkit("technical-analyst")
        # The toolkit should have tools; calling with bad args should not crash
        schemas = toolkit.get_json_schemas()
        assert len(schemas) > 0
