"""Contract tests for portfolio_risk_evaluation payload semantics."""

from types import SimpleNamespace

from app.services.mcp.trading_server import portfolio_risk_evaluation
from app.services.risk.limits import RiskLimits, get_risk_limits
from app.services.risk.portfolio_state import OpenPosition, PortfolioState


def _state() -> PortfolioState:
    return PortfolioState(
        balance=50000.0,
        equity=50000.0,
        free_margin=40000.0,
        used_margin=5000.0,
        leverage=100.0,
        open_positions=[
            OpenPosition(
                symbol="USDJPY.pro",
                side="SELL",
                volume=1.0,
                entry_price=160.0,
                current_price=159.0,
                unrealized_pnl=0.0,
                risk_pct=1.5,
            )
        ],
        open_position_count=1,
        open_risk_total_pct=1.5,
        daily_realized_pnl=0.0,
        daily_unrealized_pnl=0.0,
        daily_drawdown_pct=0.2,
        daily_high_equity=50000.0,
        weekly_drawdown_pct=0.5,
        weekly_high_equity=50000.0,
        risk_budget_remaining_pct=4.5,
        trades_remaining_today=3,
        exposure_by_symbol={"USDJPY.pro": 1.0},
        degraded=False,
        degraded_reasons=[],
        fetched_at="2026-04-05T10:00:00Z",
    )


def test_portfolio_risk_evaluation_exposes_canonical_and_legacy_currency_metrics(monkeypatch) -> None:
    base = get_risk_limits("live")
    limits = RiskLimits(
        **{
            **base.__dict__,
            "max_positions": 5,
            "max_positions_per_symbol": 2,
            "max_currency_notional_exposure_pct_warn": 150.0,
            "max_currency_notional_exposure_pct_block": 500.0,
            "max_gross_exposure_pct": 500.0,
        }
    )
    monkeypatch.setattr("app.services.risk.limits.get_risk_limits", lambda mode: limits)

    result = portfolio_risk_evaluation(
        trader_decision={
            "decision": "BUY",
            "pair": "ETHUSD",
            "entry": 2500.0,
            "stop_loss": 2400.0,
            "take_profit": 2800.0,
            "asset_class": "crypto",
        },
        risk_percent=1.0,
        mode="live",
        injected_portfolio_state=_state(),
    )

    assert "breached_limits" in result
    assert "primary_rejection_reason" in result
    assert result["primary_rejection_reason"] is None
    usd = result["currency_exposure"]["USD"]
    assert "currency_notional_exposure_pct" in usd
    assert "currency_open_risk_pct" in usd
    assert result["portfolio_summary"]["portfolio_open_risk_pct"] == 1.5
    assert result["portfolio_summary"]["incremental_trade_risk_pct"] == 1.0
    assert result["incremental_currency_open_risk_pct"]["ETH"] == 1.0
    assert result["incremental_currency_open_risk_pct"]["USD"] == 1.0


def test_portfolio_risk_evaluation_uses_trader_decision_mode_when_tool_mode_is_missing(monkeypatch) -> None:
    base = get_risk_limits("live")
    seen_modes: list[str] = []

    def _fake_get_risk_limits(mode: str):
        seen_modes.append(mode)
        return base

    monkeypatch.setattr("app.services.risk.limits.get_risk_limits", _fake_get_risk_limits)

    portfolio_risk_evaluation(
        trader_decision={
            "decision": "BUY",
            "pair": "BTCUSD",
            "mode": "live",
            "entry": 2500.0,
            "stop_loss": 2400.0,
            "take_profit": 2800.0,
            "asset_class": "crypto",
        },
        risk_percent=1.0,
        mode="simulation",
        injected_portfolio_state=_state(),
    )

    assert seen_modes[0] == "live"


def test_portfolio_risk_evaluation_stress_summary_uses_same_required_scenarios_as_risk_engine(monkeypatch) -> None:
    base = get_risk_limits("live")
    limits = RiskLimits(**{**base.__dict__, "stress_test_survival_required": ("risk_off", "usd_crash")})
    scenario_calls: list[tuple[str, ...]] = []

    monkeypatch.setattr("app.services.risk.limits.get_risk_limits", lambda mode: limits)

    def _fake_stress_test(*args, **kwargs):
        scenarios = kwargs.get("scenarios") or []
        scenario_calls.append(tuple(s.name for s in scenarios))
        return SimpleNamespace(worst_case_pnl_pct=-8.0, scenarios_surviving=len(scenarios), scenarios_total=len(scenarios), recommendation="reduce_exposure")

    monkeypatch.setattr("app.services.risk.stress_test.run_stress_test", _fake_stress_test)

    result = portfolio_risk_evaluation(
        trader_decision={
            "decision": "BUY",
            "pair": "BTCUSD",
            "mode": "live",
            "entry": 2500.0,
            "stop_loss": 2400.0,
            "take_profit": 2800.0,
            "asset_class": "crypto",
        },
        risk_percent=1.0,
        mode="live",
        injected_portfolio_state=_state(),
    )

    assert scenario_calls
    assert set(scenario_calls[0]) == {"risk_off", "usd_crash"}
    assert set(scenario_calls[-1]) == {"risk_off", "usd_crash"}
    assert result["stress_test"]["scenarios_survived"] == "2/2"
