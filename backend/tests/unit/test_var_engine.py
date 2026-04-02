"""Unit tests for VaR Monte Carlo engine."""

import numpy as np

from app.services.risk.var_engine import VaRResult, calculate_var


def _make_position(symbol: str, side: str = "BUY", volume: float = 0.1,
                   price: float = 1.1) -> dict:
    return {
        "symbol": symbol, "side": side, "volume": volume,
        "entry_price": price, "current_price": price,
    }


def _make_returns(n: int = 100, vol: float = 0.01, seed: int = 42) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(rng.normal(0, vol, n))


def test_var_single_position() -> None:
    """Single position -> VaR should be positive and reasonable."""
    positions = [_make_position("EURUSD.PRO")]
    returns = {"EURUSD.PRO": _make_returns()}

    result = calculate_var(positions, returns, equity=10000.0, seed=42)
    assert result.var_95 > 0
    assert result.var_99 > result.var_95  # 99% VaR >= 95% VaR
    assert result.var_95_pct > 0


def test_var_diversified_portfolio() -> None:
    """Uncorrelated positions -> portfolio VaR < sum of individual VaRs."""
    positions = [
        _make_position("A", price=1.1),
        _make_position("B", price=1.2),
    ]
    returns = {
        "A": _make_returns(seed=1),
        "B": _make_returns(seed=99),  # Different seed = uncorrelated
    }
    # No correlation matrix -> assume independent
    result = calculate_var(positions, returns, equity=10000.0, seed=42)

    # Individual VaRs
    var_a = calculate_var([positions[0]], {"A": returns["A"]}, equity=10000.0, seed=42)
    var_b = calculate_var([positions[1]], {"B": returns["B"]}, equity=10000.0, seed=42)

    # Diversification benefit: portfolio VaR < sum of individual VaRs
    assert result.var_95 < var_a.var_95 + var_b.var_95


def test_var_concentrated_portfolio() -> None:
    """Perfectly correlated positions -> portfolio VaR ~ sum of individual VaRs."""
    positions = [
        _make_position("A", price=1.1),
        _make_position("B", price=1.1),
    ]
    same_returns = _make_returns(seed=42)
    returns = {"A": same_returns, "B": same_returns}
    corr_matrix = {"A": {"B": 1.0}}

    result = calculate_var(positions, returns, correlation_matrix=corr_matrix,
                          equity=10000.0, seed=42)
    # With perfect correlation, VaR should be near the sum
    var_single = calculate_var([positions[0]], {"A": same_returns}, equity=10000.0, seed=42)
    # Allow 20% tolerance due to Monte Carlo randomness
    assert result.var_95 > var_single.var_95 * 1.5


def test_cvar_greater_than_var() -> None:
    """CVaR (Expected Shortfall) should always be >= VaR."""
    positions = [_make_position("EURUSD.PRO")]
    returns = {"EURUSD.PRO": _make_returns()}

    result = calculate_var(positions, returns, equity=10000.0, seed=42)
    assert result.cvar_95 >= result.var_95


def test_marginal_var_calculation() -> None:
    """Marginal VaR should be calculated for each position."""
    positions = [
        _make_position("A", price=1.1),
        _make_position("B", price=1.2),
    ]
    returns = {"A": _make_returns(seed=1), "B": _make_returns(seed=2)}

    result = calculate_var(positions, returns, equity=10000.0, seed=42)
    assert "A" in result.marginal_var
    assert "B" in result.marginal_var
    assert result.marginal_var["A"] >= 0
    assert result.marginal_var["B"] >= 0


def test_var_reproducibility() -> None:
    """Fixed seed -> same result."""
    positions = [_make_position("EURUSD.PRO")]
    returns = {"EURUSD.PRO": _make_returns()}

    r1 = calculate_var(positions, returns, equity=10000.0, seed=123)
    r2 = calculate_var(positions, returns, equity=10000.0, seed=123)
    assert r1.var_95 == r2.var_95
    assert r1.var_99 == r2.var_99


def test_empty_positions() -> None:
    """No positions -> zero VaR."""
    result = calculate_var([], {}, equity=10000.0)
    assert result.var_95 == 0.0
    assert result.var_99 == 0.0


def test_no_return_data() -> None:
    """Positions without return history -> zero VaR."""
    positions = [_make_position("UNKNOWN")]
    result = calculate_var(positions, {}, equity=10000.0)
    assert result.var_95 == 0.0
