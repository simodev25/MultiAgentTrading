"""Multi-product risk engine tests.

Validates that the RiskEngine produces correct, asset-class-aware results
for several instrument types: forex, crypto, indices, metals, energy,
equities, and ETFs.  Also tests SL/TP update validation.
"""

import pytest

from app.services.risk.rules import RiskEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

engine = RiskEngine()


# ---------------------------------------------------------------------------
# FOREX
# ---------------------------------------------------------------------------

def test_forex_eurusd_position_sizing() -> None:
    result = engine.evaluate(
        mode='simulation',
        decision='BUY',
        risk_percent=1.0,
        price=1.1000,
        stop_loss=1.0950,
        pair='EURUSD',
        equity=10_000.0,
    )
    assert result.accepted is True
    assert result.asset_class == 'forex'
    assert result.pip_size == 0.0001
    assert result.suggested_volume >= 0.01


def test_forex_gbpjpy_uses_jpy_pip_size() -> None:
    result = engine.evaluate(
        mode='live',
        decision='SELL',
        risk_percent=1.0,
        price=190.0,
        stop_loss=190.5,
        pair='GBPJPY',
        equity=10_000.0,
    )
    assert result.accepted is True
    assert result.pip_size == 0.01  # JPY pairs


def test_forex_live_mode_rejects_excessive_risk() -> None:
    result = engine.evaluate(
        mode='live',
        decision='BUY',
        risk_percent=5.0,  # above live limit of 2%
        price=1.1000,
        stop_loss=1.0950,
        pair='EURUSD',
    )
    assert result.accepted is False
    assert any('2.0%' in r or 'limit' in r.lower() for r in result.reasons)


# ---------------------------------------------------------------------------
# CRYPTO
# ---------------------------------------------------------------------------

def test_crypto_btcusd_high_price_adaptive_pip() -> None:
    result = engine.evaluate(
        mode='simulation',
        decision='BUY',
        risk_percent=1.0,
        price=65_000.0,
        stop_loss=64_000.0,
        pair='BTCUSD',
        asset_class='crypto',
        equity=10_000.0,
    )
    assert result.accepted is True
    assert result.asset_class == 'crypto'
    assert result.pip_size == 1.0  # adaptive: price >= 10000


def test_crypto_ethusd_mid_price_adaptive_pip() -> None:
    result = engine.evaluate(
        mode='paper',
        decision='BUY',
        risk_percent=1.0,
        price=2_500.0,
        stop_loss=2_450.0,
        pair='ETHUSD',
        asset_class='crypto',
        equity=10_000.0,
    )
    assert result.accepted is True
    assert result.pip_size == 0.1  # price in [100, 10000)


# ---------------------------------------------------------------------------
# INDICES
# ---------------------------------------------------------------------------

def test_index_us500_position_sizing() -> None:
    result = engine.evaluate(
        mode='simulation',
        decision='BUY',
        risk_percent=1.0,
        price=5_200.0,
        stop_loss=5_150.0,
        pair='US500',
        asset_class='index',
        equity=10_000.0,
    )
    assert result.accepted is True
    assert result.asset_class == 'index'
    assert result.pip_size == 1.0


# ---------------------------------------------------------------------------
# METALS
# ---------------------------------------------------------------------------

def test_metal_xauusd_position_sizing() -> None:
    result = engine.evaluate(
        mode='simulation',
        decision='BUY',
        risk_percent=1.0,
        price=2_300.0,
        stop_loss=2_280.0,
        pair='XAUUSD',
        asset_class='metal',
        equity=10_000.0,
    )
    assert result.accepted is True
    assert result.asset_class == 'metal'


# ---------------------------------------------------------------------------
# EQUITIES
# ---------------------------------------------------------------------------

def test_equity_aapl_position_sizing() -> None:
    result = engine.evaluate(
        mode='simulation',
        decision='BUY',
        risk_percent=1.0,
        price=180.0,
        stop_loss=175.0,
        pair='AAPL',
        asset_class='equity',
        equity=10_000.0,
    )
    assert result.accepted is True
    assert result.asset_class == 'equity'
    assert result.suggested_volume >= 1.0  # equities min_volume = 1.0


# ---------------------------------------------------------------------------
# HOLD always passes regardless of mode
# ---------------------------------------------------------------------------

def test_hold_always_accepted() -> None:
    for mode in ('simulation', 'paper', 'live'):
        result = engine.evaluate(
            mode=mode,
            decision='HOLD',
            risk_percent=0.0,
            price=1.0,
            stop_loss=None,
        )
        assert result.accepted is True, f"HOLD should always be accepted in {mode} mode"
        assert result.suggested_volume == 0.0


# ---------------------------------------------------------------------------
# SL/TP update validation
# ---------------------------------------------------------------------------

def test_sl_tp_update_valid_buy_side() -> None:
    result = engine.validate_sl_tp_update(
        mode='simulation',
        side='BUY',
        current_price=1.1000,
        new_stop_loss=1.0950,
        new_take_profit=1.1100,
        pair='EURUSD',
    )
    assert result.accepted is True


def test_sl_tp_update_rejects_sl_above_entry_for_buy() -> None:
    result = engine.validate_sl_tp_update(
        mode='simulation',
        side='BUY',
        current_price=1.1000,
        new_stop_loss=1.1050,  # above entry — invalid for BUY
        new_take_profit=1.1200,
        pair='EURUSD',
    )
    assert result.accepted is False
    assert any('below' in r.lower() or 'buy' in r.lower() for r in result.reasons)


def test_sl_tp_update_rejects_sl_below_entry_for_sell() -> None:
    result = engine.validate_sl_tp_update(
        mode='simulation',
        side='SELL',
        current_price=1.1000,
        new_stop_loss=1.0950,  # below entry — invalid for SELL
        new_take_profit=1.0800,
        pair='EURUSD',
    )
    assert result.accepted is False
    assert any('above' in r.lower() or 'sell' in r.lower() for r in result.reasons)


def test_sl_tp_update_rejects_tight_stop() -> None:
    result = engine.validate_sl_tp_update(
        mode='simulation',
        side='BUY',
        current_price=1.1000,
        new_stop_loss=1.0999,  # only 1 pip away — too tight
        new_take_profit=1.1100,
        pair='EURUSD',
    )
    assert result.accepted is False


def test_sl_tp_update_handles_crypto_correctly() -> None:
    result = engine.validate_sl_tp_update(
        mode='simulation',
        side='BUY',
        current_price=65_000.0,
        new_stop_loss=64_000.0,
        new_take_profit=67_000.0,
        pair='BTCUSD',
        asset_class='crypto',
    )
    assert result.accepted is True
    assert result.asset_class == 'crypto'
