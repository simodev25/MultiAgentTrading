"""Tests for unified position sizing — RiskEngine as single source of truth.

Validates that:
- RiskEngine.calculate_position_size() produces correct results per asset class
- The MCP position_size_calculator tool delegates to RiskEngine (no duplicate logic)
- Results are consistent between RiskEngine.evaluate() and calculate_position_size()
"""

import pytest

from app.services.risk.rules import RiskEngine
from app.services.agent_runtime.mcp_trading_server import position_size_calculator


engine = RiskEngine()


# ---------------------------------------------------------------------------
# RiskEngine.calculate_position_size() — standalone method
# ---------------------------------------------------------------------------

def test_calculate_forex_eurusd() -> None:
    result = engine.calculate_position_size(
        asset_class='forex',
        entry_price=1.1000,
        stop_loss=1.0950,
        risk_percent=1.0,
        equity=10_000.0,
    )
    assert result['asset_class'] == 'forex'
    assert result['pip_size'] == 0.0001
    assert result['suggested_volume'] >= 0.01
    # margin_ok depends on leverage — default leverage=1.0 on a 100K contract
    # requires >10K equity, so margin_ok may be False with low equity
    assert 'margin_ok' in result


def test_calculate_forex_jpy_pair() -> None:
    result = engine.calculate_position_size(
        asset_class='forex',
        entry_price=190.0,
        stop_loss=190.5,
        risk_percent=1.0,
        equity=10_000.0,
        pair='GBPJPY',
    )
    assert result['pip_size'] == 0.01  # JPY special case


def test_calculate_crypto_btcusd() -> None:
    result = engine.calculate_position_size(
        asset_class='crypto',
        entry_price=65_000.0,
        stop_loss=64_000.0,
        risk_percent=1.0,
        equity=10_000.0,
    )
    assert result['asset_class'] == 'crypto'
    assert result['pip_size'] == 1.0  # adaptive: price >= 10000
    assert result['suggested_volume'] >= 0.001


def test_calculate_equity_aapl() -> None:
    result = engine.calculate_position_size(
        asset_class='equity',
        entry_price=180.0,
        stop_loss=175.0,
        risk_percent=1.0,
        equity=10_000.0,
    )
    assert result['asset_class'] == 'equity'
    assert result['min_volume'] == 1.0
    assert result['suggested_volume'] >= 1.0


def test_calculate_index() -> None:
    result = engine.calculate_position_size(
        asset_class='index',
        entry_price=5200.0,
        stop_loss=5150.0,
        risk_percent=1.0,
        equity=10_000.0,
    )
    assert result['asset_class'] == 'index'
    assert result['pip_size'] == 1.0


def test_calculate_metal_gold() -> None:
    result = engine.calculate_position_size(
        asset_class='metal',
        entry_price=2300.0,
        stop_loss=2280.0,
        risk_percent=1.0,
        equity=10_000.0,
    )
    assert result['asset_class'] == 'metal'
    assert result['pip_size'] == 0.01


def test_calculate_stop_loss_same_as_entry() -> None:
    result = engine.calculate_position_size(
        asset_class='forex',
        entry_price=1.1000,
        stop_loss=1.1000,
        risk_percent=1.0,
    )
    assert result['error'] == 'stop_loss_same_as_entry'
    assert result['suggested_volume'] == 0.0


def test_calculate_leverage_affects_margin() -> None:
    no_leverage = engine.calculate_position_size(
        asset_class='forex',
        entry_price=1.1000,
        stop_loss=1.0950,
        risk_percent=1.0,
        equity=10_000.0,
        leverage=1.0,
    )
    with_leverage = engine.calculate_position_size(
        asset_class='forex',
        entry_price=1.1000,
        stop_loss=1.0950,
        risk_percent=1.0,
        equity=10_000.0,
        leverage=100.0,
    )
    # Same volume, but margin should differ by 100x
    assert no_leverage['suggested_volume'] == with_leverage['suggested_volume']
    assert with_leverage['margin_required'] < no_leverage['margin_required']


def test_calculate_volume_clamped_to_limits() -> None:
    # Very high risk on cheap asset → raw volume exceeds max
    result = engine.calculate_position_size(
        asset_class='forex',
        entry_price=1.1000,
        stop_loss=1.0999,  # 1 pip stop
        risk_percent=5.0,
        equity=1_000_000.0,
    )
    assert result['suggested_volume'] <= result['max_volume']


# ---------------------------------------------------------------------------
# MCP tool delegates to RiskEngine — consistency check
# ---------------------------------------------------------------------------

def test_mcp_tool_delegates_to_risk_engine_forex() -> None:
    mcp_result = position_size_calculator(
        asset_class='forex',
        entry_price=1.1000,
        stop_loss=1.0950,
        risk_percent=1.0,
        equity=10_000.0,
    )
    engine_result = engine.calculate_position_size(
        asset_class='forex',
        entry_price=1.1000,
        stop_loss=1.0950,
        risk_percent=1.0,
        equity=10_000.0,
    )
    assert mcp_result['suggested_volume'] == engine_result['suggested_volume']
    assert mcp_result['pip_size'] == engine_result['pip_size']
    assert mcp_result['pip_value_per_lot'] == engine_result['pip_value_per_lot']
    assert mcp_result['asset_class'] == engine_result['asset_class']


def test_mcp_tool_delegates_to_risk_engine_crypto() -> None:
    mcp_result = position_size_calculator(
        asset_class='crypto',
        entry_price=65_000.0,
        stop_loss=64_000.0,
        risk_percent=1.0,
        equity=10_000.0,
    )
    engine_result = engine.calculate_position_size(
        asset_class='crypto',
        entry_price=65_000.0,
        stop_loss=64_000.0,
        risk_percent=1.0,
        equity=10_000.0,
    )
    assert mcp_result == engine_result


def test_mcp_tool_delegates_to_risk_engine_equity() -> None:
    mcp_result = position_size_calculator(
        asset_class='equity',
        entry_price=180.0,
        stop_loss=175.0,
        risk_percent=1.0,
        equity=10_000.0,
    )
    engine_result = engine.calculate_position_size(
        asset_class='equity',
        entry_price=180.0,
        stop_loss=175.0,
        risk_percent=1.0,
        equity=10_000.0,
    )
    assert mcp_result == engine_result


# ---------------------------------------------------------------------------
# Consistency between evaluate() and calculate_position_size()
# ---------------------------------------------------------------------------

def test_evaluate_and_calculate_produce_same_volume() -> None:
    """evaluate() and calculate_position_size() use the same specs."""
    eval_result = engine.evaluate(
        mode='simulation',
        decision='BUY',
        risk_percent=1.0,
        price=1.1000,
        stop_loss=1.0950,
        pair='EURUSD',
        equity=10_000.0,
    )
    calc_result = engine.calculate_position_size(
        asset_class='forex',
        entry_price=1.1000,
        stop_loss=1.0950,
        risk_percent=1.0,
        equity=10_000.0,
        pair='EURUSD',
    )
    assert eval_result.suggested_volume == calc_result['suggested_volume']
    assert eval_result.pip_size == calc_result['pip_size']
    assert eval_result.pip_value_per_lot == calc_result['pip_value_per_lot']


def test_evaluate_and_calculate_consistent_crypto() -> None:
    eval_result = engine.evaluate(
        mode='simulation',
        decision='BUY',
        risk_percent=1.0,
        price=65_000.0,
        stop_loss=64_000.0,
        pair='BTCUSD',
        equity=10_000.0,
        asset_class='crypto',
    )
    calc_result = engine.calculate_position_size(
        asset_class='crypto',
        entry_price=65_000.0,
        stop_loss=64_000.0,
        risk_percent=1.0,
        equity=10_000.0,
        pair='BTCUSD',
    )
    assert eval_result.suggested_volume == calc_result['suggested_volume']
    assert eval_result.pip_size == calc_result['pip_size']
