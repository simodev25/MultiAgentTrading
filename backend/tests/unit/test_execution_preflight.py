"""Unit tests for ExecutionPreflightEngine."""

from unittest.mock import patch
from datetime import datetime, timezone

from app.services.execution.preflight import (
    ExecutionPreflightEngine,
    ExecutionStatus,
    PreflightResult,
)


def _trader(decision: str = "BUY", entry: float = 1.15, sl: float = 1.14,
            tp: float = 1.17) -> dict:
    return {"metadata": {
        "decision": decision, "entry": entry,
        "stop_loss": sl, "take_profit": tp,
    }}


def _risk(accepted: bool = True, volume: float = 0.15,
          reasons: list | None = None) -> dict:
    return {"metadata": {
        "accepted": accepted, "suggested_volume": volume,
        "reasons": reasons or ["Risk checks passed."],
    }}


def _snapshot(spread: float = 0.00002, last_price: float = 1.15) -> dict:
    return {"spread": spread, "last_price": last_price}


ENGINE = ExecutionPreflightEngine()


def test_hold_decision_skipped() -> None:
    result = ENGINE.validate(_trader("HOLD"), _risk(), _snapshot(), "EURUSD.PRO", "simulation")
    assert result.status == ExecutionStatus.SKIPPED
    assert result.can_execute is False
    assert "HOLD" in result.reason


def test_invalid_decision_blocked() -> None:
    result = ENGINE.validate(_trader("MAYBE"), _risk(), _snapshot(), "EURUSD.PRO", "simulation")
    assert result.status == ExecutionStatus.BLOCKED
    assert "invalid decision" in result.reason


def test_risk_rejected_refused() -> None:
    result = ENGINE.validate(
        _trader(), _risk(accepted=False, reasons=["daily loss limit"]),
        _snapshot(), "EURUSD.PRO", "simulation",
    )
    assert result.status == ExecutionStatus.REFUSED
    assert result.can_execute is False
    assert "REFUSED" in result.reason


def test_missing_volume_blocked() -> None:
    result = ENGINE.validate(_trader(), _risk(volume=0), _snapshot(), "EURUSD.PRO", "simulation")
    assert result.status == ExecutionStatus.BLOCKED
    assert "volume" in result.reason


def test_missing_stop_loss_blocked() -> None:
    result = ENGINE.validate(
        _trader(sl=0), _risk(), _snapshot(), "EURUSD.PRO", "simulation",
    )
    assert result.status == ExecutionStatus.BLOCKED
    assert "stop_loss" in result.reason


def test_missing_entry_blocked() -> None:
    result = ENGINE.validate(
        _trader(entry=0), _risk(), _snapshot(), "EURUSD.PRO", "simulation",
    )
    assert result.status == ExecutionStatus.BLOCKED
    assert "entry" in result.reason


def test_side_flip_blocked() -> None:
    """Trader says BUY but risk metadata says SELL → blocked."""
    risk = _risk()
    risk["metadata"]["decision"] = "SELL"
    result = ENGINE.validate(_trader("BUY"), risk, _snapshot(), "EURUSD.PRO", "simulation")
    assert result.status == ExecutionStatus.BLOCKED
    assert "side inconsistency" in result.reason


def test_market_closed_saturday_blocked() -> None:
    """Saturday → forex market closed."""
    # Mock datetime to return a Saturday
    import app.services.execution.preflight as pf_module
    fake_saturday = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)  # April 4 2026 = Saturday
    with patch.object(pf_module, 'datetime', wraps=datetime) as mock_dt:
        mock_dt.now.return_value = fake_saturday
        result = ENGINE.validate(_trader(), _risk(), _snapshot(), "EURUSD.PRO", "live")
    assert result.status == ExecutionStatus.BLOCKED
    assert "Saturday" in result.reason or "closed" in result.reason


def test_market_closed_friday_late_blocked() -> None:
    """Friday 23:00 UTC → forex market closed."""
    import app.services.execution.preflight as pf_module
    fake_friday_late = datetime(2026, 4, 3, 23, 0, tzinfo=timezone.utc)  # April 3 2026 = Friday
    with patch.object(pf_module, 'datetime', wraps=datetime) as mock_dt:
        mock_dt.now.return_value = fake_friday_late
        result = ENGINE.validate(_trader(), _risk(), _snapshot(), "EURUSD.PRO", "live")
    assert result.status == ExecutionStatus.BLOCKED
    assert "Friday" in result.reason or "closed" in result.reason


def test_market_open_weekday() -> None:
    """Tuesday 14:00 UTC → forex market open."""
    import app.services.execution.preflight as pf_module
    fake_tuesday = datetime(2026, 3, 31, 14, 0, tzinfo=timezone.utc)  # March 31 2026 = Tuesday
    with patch.object(pf_module, 'datetime', wraps=datetime) as mock_dt:
        mock_dt.now.return_value = fake_tuesday
        result = ENGINE.validate(_trader(), _risk(), _snapshot(), "EURUSD.PRO", "simulation")
    assert result.can_execute is True
    assert any("market_open" in c for c in result.checks_passed)


def test_crypto_always_open() -> None:
    """Crypto on Saturday → still open (24/7)."""
    import app.services.execution.preflight as pf_module
    fake_saturday = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)
    with patch.object(pf_module, 'datetime', wraps=datetime) as mock_dt:
        mock_dt.now.return_value = fake_saturday
        result = ENGINE.validate(_trader(), _risk(), _snapshot(), "BTCUSD", "simulation")
    # Crypto should pass market hours check
    assert any("crypto" in c.lower() or "market_open" in c for c in result.checks_passed)


def test_spread_excessive_blocked() -> None:
    """Spread 0.05% with live limit 0.01% → blocked."""
    # spread = 0.000575, price = 1.15 → spread_pct = 0.05%
    result = ENGINE.validate(
        _trader(), _risk(), _snapshot(spread=0.000575, last_price=1.15),
        "EURUSD.PRO", "live",
    )
    assert result.status == ExecutionStatus.BLOCKED
    assert "spread" in result.reason


def test_spread_acceptable() -> None:
    """Spread 0.002% with simulation limit 5% → passes."""
    result = ENGINE.validate(
        _trader(), _risk(), _snapshot(spread=0.00002, last_price=1.15),
        "EURUSD.PRO", "simulation",
    )
    assert any("spread_ok" in c for c in result.checks_passed)


def test_volume_below_min_blocked() -> None:
    """Volume 0.001 with forex min 0.01 → blocked."""
    result = ENGINE.validate(
        _trader(), _risk(volume=0.001), _snapshot(),
        "EURUSD.PRO", "simulation",
    )
    assert result.status == ExecutionStatus.BLOCKED
    assert "volume" in result.reason and "below" in result.reason


def test_volume_above_max_blocked() -> None:
    """Volume 100 with forex max 10 → blocked."""
    result = ENGINE.validate(
        _trader(), _risk(volume=100), _snapshot(),
        "EURUSD.PRO", "simulation",
    )
    assert result.status == ExecutionStatus.BLOCKED
    assert "volume" in result.reason and "above" in result.reason


def test_all_checks_pass_simulation() -> None:
    """All OK in simulation → simulated, can_execute=true."""
    result = ENGINE.validate(
        _trader(), _risk(), _snapshot(),
        "EURUSD.PRO", "simulation",
    )
    assert result.status == ExecutionStatus.SIMULATED
    assert result.can_execute is True
    assert len(result.checks_failed) == 0
    assert len(result.checks_passed) >= 6


def test_all_checks_pass_live() -> None:
    """All OK in live → executed, can_execute=true."""
    result = ENGINE.validate(
        _trader(), _risk(), _snapshot(spread=0.00001),
        "EURUSD.PRO", "live",
    )
    assert result.status == ExecutionStatus.EXECUTED
    assert result.can_execute is True


def test_checks_passed_list_complete() -> None:
    """Verify checks_passed contains all 8 check names."""
    result = ENGINE.validate(
        _trader(), _risk(), _snapshot(),
        "EURUSD.PRO", "simulation",
    )
    check_names = " ".join(result.checks_passed)
    assert "decision_valid" in check_names
    assert "risk_accepted" in check_names
    assert "params_complete" in check_names
    assert "side_consistent" in check_names
    assert "market_open" in check_names
    assert "spread_ok" in check_names
    assert "volume_ok" in check_names
    assert "instrument_tradable" in check_names


def test_deterministic_summary_no_llm() -> None:
    """PreflightResult contains all fields needed for deterministic summary."""
    result = ENGINE.validate(
        _trader("BUY"), _risk(volume=0.15), _snapshot(),
        "EURUSD.PRO", "simulation",
    )
    assert result.side == "BUY"
    assert result.volume == 0.15
    assert result.entry == 1.15
    assert result.stop_loss == 1.14
    assert result.take_profit == 1.17
    assert result.symbol == "EURUSD.PRO"
    assert result.mode == "simulation"
