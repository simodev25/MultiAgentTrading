"""Unit tests for _estimate_position_risk() — accurate per-asset-class risk calculation."""

from app.services.risk.portfolio_state import OpenPosition, PortfolioStateService


def _pos(symbol: str = "EURUSD.PRO", sl: float | None = 1.0950,
         entry: float = 1.1000, volume: float = 0.1) -> OpenPosition:
    return OpenPosition(
        symbol=symbol, side="BUY", volume=volume,
        entry_price=entry, current_price=entry,
        unrealized_pnl=0, stop_loss=sl,
    )


def test_forex_risk_reasonable() -> None:
    """0.1 lot EURUSD with 50 pip SL should be ~$50 risk, not thousands."""
    pos = _pos("EURUSD.PRO", sl=1.0950, entry=1.1000, volume=0.1)
    risk = PortfolioStateService._estimate_position_risk(pos, equity=10000.0)
    # 50 pips * $10/pip/lot * 0.1 lot = $50 → 0.5% of $10k
    assert 0.1 < risk < 2.0, f"Forex risk should be ~0.5%, got {risk}%"


def test_forex_half_lot_50_pips() -> None:
    """0.5 lot EURUSD with 50 pip SL → ~$250 risk → 0.5% of $50k."""
    pos = _pos("EURUSD.PRO", sl=1.0950, entry=1.1000, volume=0.5)
    risk = PortfolioStateService._estimate_position_risk(pos, equity=50000.0)
    assert 0.1 < risk < 2.0, f"Expected ~0.5%, got {risk}%"


def test_forex_not_100_percent() -> None:
    """The old bug: 0.5 lot was calculated as 100% risk. Must be << 10%."""
    pos = _pos("EURUSD.PRO", sl=1.0950, entry=1.1000, volume=0.5)
    risk = PortfolioStateService._estimate_position_risk(pos, equity=50000.0)
    assert risk < 10.0, f"Risk should never be {risk}% for a standard forex position"


def test_no_stop_loss_fallback() -> None:
    """Without SL, fallback to 2% conservative estimate."""
    pos = _pos("EURUSD.PRO", sl=None)
    risk = PortfolioStateService._estimate_position_risk(pos, equity=10000.0)
    assert risk == 2.0


def test_zero_equity() -> None:
    """Zero equity → 0% risk."""
    pos = _pos()
    risk = PortfolioStateService._estimate_position_risk(pos, equity=0.0)
    assert risk == 0.0


def test_crypto_risk() -> None:
    """Crypto: 0.01 BTC with SL 1000 below entry."""
    pos = OpenPosition(
        symbol="BTCUSD", side="BUY", volume=0.01,
        entry_price=60000, current_price=60000,
        unrealized_pnl=0, stop_loss=59000,
    )
    risk = PortfolioStateService._estimate_position_risk(pos, equity=10000.0)
    # Should be reasonable, not 100%
    assert risk < 20.0, f"Crypto risk too high: {risk}%"


def test_metal_risk() -> None:
    """Gold: 0.1 lot XAUUSD with SL $20 below entry."""
    pos = OpenPosition(
        symbol="XAUUSD", side="BUY", volume=0.1,
        entry_price=2400, current_price=2400,
        unrealized_pnl=0, stop_loss=2380,
    )
    risk = PortfolioStateService._estimate_position_risk(pos, equity=10000.0)
    assert risk < 30.0, f"Metal risk too high: {risk}%"


def test_contract_size_resolve() -> None:
    """Verify contract size resolution for known asset classes."""
    forex_cs = PortfolioStateService._resolve_contract_size("EURUSD.PRO")
    assert forex_cs == 100_000

    crypto_cs = PortfolioStateService._resolve_contract_size("BTCUSD")
    assert crypto_cs == 1

    metal_cs = PortfolioStateService._resolve_contract_size("XAUUSD")
    assert metal_cs == 100
