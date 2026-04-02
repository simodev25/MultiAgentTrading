"""Unit tests for correlation exposure detector."""

from app.services.risk.correlation_exposure import (
    CorrelationExposureReport,
    _correlation_to_multiplier,
    _correlation_severity,
    compute_correlation_exposure,
)
from app.services.risk.portfolio_state import OpenPosition


def _pos(symbol: str, side: str, volume: float = 0.1) -> OpenPosition:
    return OpenPosition(
        symbol=symbol, side=side, volume=volume,
        entry_price=1.1, current_price=1.1, unrealized_pnl=0,
    )


def test_high_correlation_same_direction_multiplier() -> None:
    """High correlation + same direction -> 1.8x risk."""
    mult = _correlation_to_multiplier(0.85, same_direction=True)
    assert mult == 1.8


def test_high_correlation_opposite_direction_hedge() -> None:
    """High correlation + opposite direction -> 0.6x (hedge)."""
    mult = _correlation_to_multiplier(0.85, same_direction=False)
    assert mult == 0.6


def test_medium_correlation_same_direction() -> None:
    """Medium correlation + same direction -> 1.4x."""
    mult = _correlation_to_multiplier(0.55, same_direction=True)
    assert mult == 1.4


def test_low_correlation_independent() -> None:
    """Low correlation -> 1.0x (independent)."""
    mult = _correlation_to_multiplier(0.3, same_direction=True)
    assert mult == 1.0


def test_severity_high() -> None:
    assert _correlation_severity(0.85) == "high"


def test_severity_medium() -> None:
    assert _correlation_severity(0.55) == "medium"


def test_severity_low() -> None:
    assert _correlation_severity(0.3) == "low"


def test_single_position_returns_empty() -> None:
    """Single position -> no correlation to check."""
    positions = [_pos("EURUSD.PRO", "BUY")]
    report = compute_correlation_exposure(positions, open_risk_total_pct=2.0)
    assert len(report.alerts) == 0
    assert report.effective_risk_multiplier == 1.0


def test_same_symbol_positions_returns_empty() -> None:
    """Multiple positions on same symbol -> no cross-pair correlation."""
    positions = [
        _pos("EURUSD.PRO", "BUY"),
        _pos("EURUSD.PRO", "BUY"),
    ]
    report = compute_correlation_exposure(positions, open_risk_total_pct=2.0)
    assert len(report.alerts) == 0


def test_empty_positions() -> None:
    """No positions -> empty report."""
    report = compute_correlation_exposure([], open_risk_total_pct=0.0)
    assert len(report.alerts) == 0
    assert report.adjusted_open_risk_pct == 0.0


def test_weekly_drawdown_in_evaluate_portfolio() -> None:
    """Weekly drawdown 6% with limit 5% -> REJECT."""
    from app.services.risk.limits import get_risk_limits
    from app.services.risk.portfolio_state import PortfolioState
    from app.services.risk.rules import ProposedTrade, RiskEngine

    engine = RiskEngine()
    limits = get_risk_limits("live")
    portfolio = PortfolioState(
        balance=10000.0, equity=10000.0, free_margin=8000.0,
        used_margin=2000.0, leverage=100.0,
        weekly_drawdown_pct=6.0,  # Above live limit of 5%
        daily_high_equity=10000.0,
        fetched_at="2026-04-01T12:00:00Z",
    )
    trade = ProposedTrade(
        decision="BUY", pair="EURUSD.PRO", entry_price=1.1,
        stop_loss=1.095, risk_percent=1.0, mode="live",
    )
    result = engine.evaluate_portfolio(portfolio, limits, trade)
    assert result.accepted is False
    assert "weekly loss limit reached" in result.reasons[0]
