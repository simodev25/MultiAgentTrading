"""Unit tests for stress testing engine."""

from app.services.risk.portfolio_state import OpenPosition
from app.services.risk.stress_test import (
    SCENARIOS,
    StressScenario,
    run_stress_test,
)


def _pos(symbol: str, side: str, volume: float = 0.1, price: float = 1.1) -> OpenPosition:
    return OpenPosition(
        symbol=symbol, side=side, volume=volume,
        entry_price=price, current_price=price, unrealized_pnl=0,
    )


def test_usd_crash_long_eurusd() -> None:
    """BUY EURUSD + USD crash -> should profit (EUR up, USD down)."""
    positions = [_pos("EURUSD.PRO", "BUY", 0.5, 1.15)]
    report = run_stress_test(positions, equity=10000.0)

    usd_crash = next((r for r in report.results if r.scenario == "usd_crash"), None)
    assert usd_crash is not None
    # EUR +2%, USD -3% -> EURUSD goes up ~5% -> BUY profits
    assert usd_crash.portfolio_pnl > 0


def test_usd_crash_long_usdjpy() -> None:
    """BUY USDJPY + USD crash -> should lose (USD down, JPY up)."""
    positions = [_pos("USDJPY.PRO", "BUY", 0.5, 150.0)]
    report = run_stress_test(positions, equity=10000.0)

    usd_crash = next((r for r in report.results if r.scenario == "usd_crash"), None)
    assert usd_crash is not None
    # USD -3%, JPY +2.5% -> USDJPY drops ~5.5% -> BUY loses
    assert usd_crash.portfolio_pnl < 0


def test_flash_crash_margin_call() -> None:
    """Heavily leveraged portfolio + flash crash -> potential margin call."""
    # Large position relative to equity
    positions = [_pos("EURUSD.PRO", "BUY", 5.0, 1.15)]
    report = run_stress_test(positions, equity=10000.0, used_margin=8000.0)

    flash = next((r for r in report.results if r.scenario == "flash_crash"), None)
    assert flash is not None
    assert flash.portfolio_pnl < 0  # Flash crash hurts


def test_risk_off_crypto_heavy() -> None:
    """Crypto-heavy portfolio + risk-off -> big loss."""
    positions = [
        _pos("BTCUSD", "BUY", 0.5, 60000.0),
        _pos("ETHUSD", "BUY", 2.0, 3000.0),
    ]
    report = run_stress_test(positions, equity=100000.0)

    risk_off = next((r for r in report.results if r.scenario == "risk_off"), None)
    assert risk_off is not None
    assert risk_off.portfolio_pnl < 0
    assert risk_off.portfolio_pnl_pct < -5.0  # Significant loss


def test_portfolio_survives_all_scenarios() -> None:
    """Small, diversified portfolio should survive all scenarios."""
    positions = [_pos("EURUSD.PRO", "BUY", 0.01, 1.15)]
    report = run_stress_test(positions, equity=10000.0)

    assert report.scenarios_surviving == report.scenarios_total
    assert report.recommendation == "safe"


def test_custom_scenario() -> None:
    """Custom scenario applied correctly."""
    custom = StressScenario(
        name="custom_test",
        description="EUR drops 10%",
        shocks={"EUR": -10.0, "USD": 0.0},
        probability="extreme",
    )
    positions = [_pos("EURUSD.PRO", "BUY", 0.5, 1.15)]
    report = run_stress_test(positions, equity=10000.0, scenarios=[custom])

    assert len(report.results) == 1
    assert report.results[0].scenario == "custom_test"
    assert report.results[0].portfolio_pnl < 0  # EUR drop hurts long EURUSD


def test_no_positions() -> None:
    """Empty portfolio -> safe."""
    report = run_stress_test([], equity=10000.0)
    assert report.recommendation == "safe"
    assert report.worst_case_pnl_pct == 0.0


def test_all_predefined_scenarios_exist() -> None:
    """Verify all 8 predefined scenarios are defined."""
    names = {s.name for s in SCENARIOS}
    expected = {"usd_crash", "usd_rally", "risk_off", "flash_crash",
                "rate_shock", "crypto_collapse", "commodity_spike", "liquidity_crisis"}
    assert names == expected
