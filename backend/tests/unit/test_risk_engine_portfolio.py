"""Unit tests for RiskEngine.evaluate_portfolio() — portfolio-level risk checks."""

from types import SimpleNamespace

from app.services.risk.limits import RiskLimits, get_risk_limits
from app.services.risk.portfolio_state import OpenPosition, PortfolioState
from app.services.risk.rules import ProposedTrade, RiskEngine


def _make_portfolio(**overrides) -> PortfolioState:
    """Helper: build a PortfolioState with sensible defaults."""
    defaults = dict(
        balance=10000.0,
        equity=10000.0,
        free_margin=8000.0,
        used_margin=2000.0,
        leverage=100.0,
        open_positions=[],
        open_position_count=0,
        open_risk_total_pct=0.0,
        daily_realized_pnl=0.0,
        daily_unrealized_pnl=0.0,
        daily_drawdown_pct=0.0,
        daily_high_equity=10000.0,
        risk_budget_remaining_pct=6.0,
        trades_remaining_today=3,
        exposure_by_symbol={},
        degraded=False,
        degraded_reasons=[],
        fetched_at="2026-04-01T12:00:00Z",
    )
    defaults.update(overrides)
    return PortfolioState(**defaults)


def _make_trade(**overrides) -> ProposedTrade:
    """Helper: build a ProposedTrade with sensible defaults."""
    defaults = dict(
        decision="BUY",
        pair="EURUSD.PRO",
        entry_price=1.1000,
        stop_loss=1.0950,
        risk_percent=1.0,
        mode="live",
        asset_class="forex",
    )
    defaults.update(overrides)
    return ProposedTrade(**defaults)


LIVE_LIMITS = get_risk_limits("live")


def test_reject_daily_loss_exceeded() -> None:
    """Drawdown 3.5% with limit 3% -> REJECT."""
    engine = RiskEngine()
    portfolio = _make_portfolio(daily_drawdown_pct=3.5)
    trade = _make_trade()
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is False
    assert "daily loss limit reached" in result.reasons[0]
    assert result.primary_rejection_reason == "daily_drawdown_pct"


def test_reject_risk_budget_exceeded() -> None:
    """Open risk 5% + new 2% > max 6% -> REJECT."""
    engine = RiskEngine()
    portfolio = _make_portfolio(open_risk_total_pct=5.0)
    trade = _make_trade(risk_percent=2.0)
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is False
    assert "risk budget exceeded" in result.reasons[0]
    assert result.primary_rejection_reason == "portfolio_open_risk_pct"


def test_reject_max_positions() -> None:
    """3 positions open, max 3 -> REJECT."""
    engine = RiskEngine()
    positions = [
        OpenPosition(
            symbol=f"PAIR{i}", side="BUY", volume=0.1,
            entry_price=1.1, current_price=1.1, unrealized_pnl=0,
        )
        for i in range(3)
    ]
    portfolio = _make_portfolio(
        open_positions=positions,
        open_position_count=3,
    )
    trade = _make_trade()
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is False
    assert "max positions reached" in result.reasons[0]
    assert result.primary_rejection_reason == "max_positions"


def test_reject_max_per_symbol() -> None:
    """1 position EURUSD.PRO, max 1 per symbol for live -> REJECT."""
    engine = RiskEngine()
    positions = [
        OpenPosition(
            symbol="EURUSD.PRO", side="BUY", volume=0.1,
            entry_price=1.1, current_price=1.1, unrealized_pnl=0,
        )
    ]
    portfolio = _make_portfolio(
        open_positions=positions,
        open_position_count=1,
    )
    trade = _make_trade(pair="EURUSD.PRO")
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is False
    assert "max positions on EURUSD.PRO reached" in result.reasons[0]
    assert result.primary_rejection_reason == "max_positions_per_symbol"


def test_reject_insufficient_margin() -> None:
    """Free margin 15% < min 50% for live -> REJECT."""
    engine = RiskEngine()
    portfolio = _make_portfolio(
        equity=10000.0,
        free_margin=1500.0,  # 15%
    )
    trade = _make_trade()
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is False
    assert "insufficient free margin" in result.reasons[0]
    assert result.primary_rejection_reason == "free_margin_pct"


def test_accept_within_limits() -> None:
    """All checks pass -> ACCEPT with a valid volume."""
    engine = RiskEngine()
    portfolio = _make_portfolio(
        equity=10000.0,
        free_margin=8000.0,
        open_risk_total_pct=1.0,
        daily_drawdown_pct=0.5,
    )
    trade = _make_trade(risk_percent=1.0)
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is True
    assert result.suggested_volume > 0


def test_volume_reduction_near_limit() -> None:
    """Risk budget at >80% usage -> volume reduced."""
    engine = RiskEngine()
    # Budget is 6%, open risk is 5%, new trade is 0.9% -> usage = 0.9/1.0 = 90% > 80%
    portfolio = _make_portfolio(
        equity=10000.0,
        free_margin=8000.0,
        open_risk_total_pct=5.0,
        daily_drawdown_pct=0.5,
    )
    trade = _make_trade(risk_percent=0.9)
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is True
    # Compare with fresh portfolio (no existing risk)
    portfolio_fresh = _make_portfolio(
        equity=10000.0,
        free_margin=8000.0,
        open_risk_total_pct=0.0,
        daily_drawdown_pct=0.0,
    )
    result_fresh = engine.evaluate_portfolio(portfolio_fresh, LIVE_LIMITS, trade)
    assert result.suggested_volume <= result_fresh.suggested_volume


def test_real_equity_used() -> None:
    """Verify that real equity (not hardcoded 10k) is used for sizing."""
    engine = RiskEngine()
    portfolio_small = _make_portfolio(equity=5000.0, free_margin=4000.0)
    portfolio_large = _make_portfolio(equity=50000.0, free_margin=40000.0)
    trade = _make_trade(risk_percent=1.0)
    result_small = engine.evaluate_portfolio(portfolio_small, LIVE_LIMITS, trade)
    result_large = engine.evaluate_portfolio(portfolio_large, LIVE_LIMITS, trade)
    assert result_small.accepted is True
    assert result_large.accepted is True
    # Larger equity -> larger position size
    assert result_large.suggested_volume > result_small.suggested_volume


def test_hold_bypasses_portfolio_checks() -> None:
    """HOLD decision -> no portfolio checks, immediate accept."""
    engine = RiskEngine()
    # Portfolio in terrible shape — should still accept HOLD
    portfolio = _make_portfolio(
        daily_drawdown_pct=99.0,
        open_risk_total_pct=99.0,
        open_position_count=999,
    )
    trade = _make_trade(decision="HOLD")
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is True
    assert result.suggested_volume == 0.0


def test_degraded_data_handling() -> None:
    """Degraded portfolio state is passed through without crashing."""
    engine = RiskEngine()
    portfolio = _make_portfolio(
        degraded=True,
        degraded_reasons=["metaapi_down"],
        equity=10000.0,
        free_margin=8000.0,
    )
    trade = _make_trade()
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert isinstance(result.accepted, bool)
    assert len(result.reasons) > 0


def test_high_currency_notional_exposure_is_collected_in_breached_limits_without_auto_reject() -> None:
    """Notional concentration is observable and explicit, not silently treated as stop-risk."""
    engine = RiskEngine()
    positions = [
        OpenPosition(
            symbol="USDJPY.pro", side="SELL", volume=1.0,
            entry_price=160.0, current_price=159.0, unrealized_pnl=0.0, risk_pct=1.0,
        )
    ]
    portfolio = _make_portfolio(
        equity=50000.0,
        free_margin=40000.0,
        open_positions=positions,
        open_position_count=1,
        open_risk_total_pct=1.0,
    )
    limits = RiskLimits(**{
        **LIVE_LIMITS.__dict__,
        "max_positions": 5,
        "max_positions_per_symbol": 2,
        "max_open_risk_pct": 6.0,
        "max_currency_notional_exposure_pct_warn": 150.0,
        "max_currency_notional_exposure_pct_block": 500.0,
        "max_currency_open_risk_pct": 6.0,
    })
    trade = _make_trade(pair="ETHUSD", risk_percent=1.0, asset_class="crypto")
    result = engine.evaluate_portfolio(portfolio, limits, trade)
    assert result.accepted is True
    assert any(item["metric"] == "currency_notional_exposure_pct[USD]" for item in result.breached_limits)
    assert result.primary_rejection_reason is None


def test_stress_test_critical_causes_rejection(monkeypatch) -> None:
    """Stress-test critical remains a hard block."""
    engine = RiskEngine()
    portfolio = _make_portfolio(
        equity=10000.0,
        free_margin=8000.0,
        open_risk_total_pct=1.0,
        daily_drawdown_pct=0.5,
    )
    trade = _make_trade(risk_percent=1.0)

    def _fake_stress_test(*args, **kwargs):
        return SimpleNamespace(worst_case_pnl_pct=-42.0, recommendation="critical")

    monkeypatch.setattr("app.services.risk.stress_test.run_stress_test", _fake_stress_test)
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is False
    assert result.primary_rejection_reason == "stress_test_worst_case_pct"
    assert any(item["metric"] == "stress_test_worst_case_pct" for item in result.breached_limits)
