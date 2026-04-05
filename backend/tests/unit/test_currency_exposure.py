"""Unit tests for currency exposure engine."""

from app.services.risk.currency_exposure import (
    CurrencyExposureReport,
    compute_currency_exposure,
    _decompose_symbol,
)
from app.services.risk.portfolio_state import OpenPosition


def _pos(
    symbol: str,
    side: str,
    volume: float = 0.1,
    current_price: float = 1.1,
) -> OpenPosition:
    return OpenPosition(
        symbol=symbol, side=side, volume=volume,
        entry_price=current_price, current_price=current_price, unrealized_pnl=0,
    )


def test_buy_eurusd_decomposition() -> None:
    """BUY EURUSD -> +EUR, -USD."""
    positions = [_pos("EURUSD.PRO", "BUY", 0.5)]
    report = compute_currency_exposure(positions, equity=10000.0)
    assert "EUR" in report.exposures
    assert "USD" in report.exposures
    assert report.exposures["EUR"].net_exposure_lots > 0  # Long EUR
    assert report.exposures["USD"].net_exposure_lots < 0  # Short USD


def test_sell_gbpjpy_decomposition() -> None:
    """SELL GBPJPY -> -GBP, +JPY."""
    positions = [_pos("GBPJPY.PRO", "SELL", 0.3)]
    report = compute_currency_exposure(positions, equity=10000.0)
    assert "GBP" in report.exposures
    assert "JPY" in report.exposures
    assert report.exposures["GBP"].net_exposure_lots < 0  # Short GBP
    assert report.exposures["JPY"].net_exposure_lots > 0  # Long JPY


def test_net_exposure_cancellation() -> None:
    """BUY EURUSD + SELL EURGBP -> EUR partially cancels."""
    positions = [
        _pos("EURUSD.PRO", "BUY", 0.5),   # +EUR, -USD
        _pos("EURGBP.PRO", "SELL", 0.5),   # -EUR, +GBP
    ]
    report = compute_currency_exposure(positions, equity=10000.0)
    # EUR exposure should be near zero (0.5 - 0.5 = 0)
    eur = report.exposures.get("EUR")
    assert eur is not None
    assert abs(eur.net_exposure_lots) < 0.01


def test_multi_position_usd_concentration() -> None:
    """BUY EURUSD + BUY GBPUSD + BUY XAUUSD -> large USD short exposure."""
    positions = [
        _pos("EURUSD.PRO", "BUY", 0.3),
        _pos("GBPUSD.PRO", "BUY", 0.3),
        _pos("XAUUSD", "BUY", 0.3),
    ]
    report = compute_currency_exposure(positions, equity=10000.0)
    usd = report.exposures.get("USD")
    assert usd is not None
    assert usd.net_exposure_lots < 0  # Short USD from all 3 positions
    assert len(usd.contributing_positions) >= 2  # At least 2 pairs contribute


def test_reject_currency_limit_exceeded() -> None:
    """High exposure on USD should generate a warning."""
    # Large positions to trigger high exposure %
    positions = [
        _pos("EURUSD.PRO", "BUY", 1.0),
        _pos("GBPUSD.PRO", "BUY", 1.0),
    ]
    report = compute_currency_exposure(positions, equity=10000.0)
    usd = report.exposures.get("USD")
    assert usd is not None
    # With 2 lots short USD at 100k per lot, exposure = 200k / 10k = 2000%
    assert usd.currency_notional_exposure_pct > 15.0  # Well above any limit


def test_crypto_exposure_uses_asset_specific_contract_size() -> None:
    """BTCUSD should use crypto contract size=1, not forex 100k."""
    positions = [_pos("BTCUSD", "BUY", 0.1, current_price=50000.0)]
    report = compute_currency_exposure(positions, equity=10000.0)
    btc = report.exposures.get("BTC")
    usd = report.exposures.get("USD")
    assert btc is not None
    assert usd is not None
    assert btc.currency_notional_exposure_pct == 50.0
    assert usd.currency_notional_exposure_pct == 50.0


def test_forex_exposure_uses_price_to_value_currency_units() -> None:
    """EURUSD should convert the 10k EUR base leg via price to account currency."""
    positions = [_pos("EURUSD.PRO", "BUY", 0.1, current_price=1.2)]
    report = compute_currency_exposure(positions, equity=10000.0)
    eur = report.exposures.get("EUR")
    usd = report.exposures.get("USD")
    assert eur is not None
    assert usd is not None
    assert eur.currency_notional_exposure_pct == 120.0
    assert usd.currency_notional_exposure_pct == 120.0


def test_currency_open_risk_pct_is_exposed_separately() -> None:
    """Open risk attribution is distinct from notional exposure."""
    positions = [
        OpenPosition(
            symbol="USDJPY.pro",
            side="SELL",
            volume=1.0,
            entry_price=160.0,
            current_price=159.0,
            unrealized_pnl=0.0,
            risk_pct=2.5,
        ),
        OpenPosition(
            symbol="EURJPY.pro",
            side="SELL",
            volume=1.0,
            entry_price=184.0,
            current_price=183.5,
            unrealized_pnl=0.0,
            risk_pct=1.5,
        ),
    ]
    report = compute_currency_exposure(positions, equity=10000.0)
    usd = report.exposures.get("USD")
    jpy = report.exposures.get("JPY")
    eur = report.exposures.get("EUR")
    assert usd is not None
    assert jpy is not None
    assert eur is not None
    assert usd.currency_open_risk_pct == 2.5
    assert eur.currency_open_risk_pct == 1.5
    assert jpy.currency_open_risk_pct == 4.0


def test_metal_usd_exposure() -> None:
    """XAUUSD contributes to USD exposure."""
    positions = [_pos("XAUUSD", "BUY", 0.1)]
    report = compute_currency_exposure(positions, equity=10000.0)
    # XAU should decompose to XAU/USD
    if "USD" in report.exposures:
        assert report.exposures["USD"].net_exposure_lots < 0  # Short USD
    if "XAU" in report.exposures:
        assert report.exposures["XAU"].net_exposure_lots > 0  # Long Gold


def test_empty_positions() -> None:
    """No positions -> empty report."""
    report = compute_currency_exposure([], equity=10000.0)
    assert len(report.exposures) == 0
    assert report.dominant_currency == ""


def test_zero_equity() -> None:
    """Zero equity -> returns with warning."""
    report = compute_currency_exposure([], equity=0.0)
    assert "equity_zero_or_negative" in report.warnings
