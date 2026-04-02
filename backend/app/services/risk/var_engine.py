"""Value at Risk (VaR) engine — Monte Carlo simulation for portfolio risk.

Computes VaR 95%, VaR 99%, and CVaR (Expected Shortfall) using
correlated Monte Carlo simulations via Cholesky decomposition.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class VaRResult:
    # Main results
    var_95: float = 0.0                  # VaR 95% in account currency
    var_99: float = 0.0                  # VaR 99% in account currency
    var_95_pct: float = 0.0              # VaR 95% as % of equity
    var_99_pct: float = 0.0              # VaR 99% as % of equity
    cvar_95: float = 0.0                 # Conditional VaR (Expected Shortfall) 95%

    # Context
    horizon_hours: int = 24
    simulations: int = 10_000
    portfolio_value: float = 0.0
    method: str = "monte_carlo"

    # Decomposition
    var_by_position: dict[str, float] = field(default_factory=dict)
    marginal_var: dict[str, float] = field(default_factory=dict)


def calculate_var(
    positions: list[dict],
    returns_history: dict[str, list[float]],
    correlation_matrix: dict[str, dict[str, float]] | None = None,
    equity: float = 10000.0,
    horizon_hours: int = 24,
    n_simulations: int = 10_000,
    seed: int | None = None,
) -> VaRResult:
    """Calculate portfolio VaR using Monte Carlo simulation.

    Args:
        positions: List of dicts with keys: symbol, side, volume, entry_price, current_price
        returns_history: Dict mapping symbol -> list of historical returns (log returns)
        correlation_matrix: Optional pairwise correlations {sym_a: {sym_b: corr}}
        equity: Current account equity
        horizon_hours: VaR time horizon
        n_simulations: Number of Monte Carlo scenarios
        seed: Optional random seed for reproducibility

    Returns:
        VaRResult with VaR 95%, 99%, CVaR, and per-position decomposition.
    """
    if not positions or equity <= 0:
        return VaRResult(portfolio_value=equity)

    rng = np.random.default_rng(seed)

    # Filter positions with available return data
    valid_positions = []
    for p in positions:
        sym = p.get("symbol", "")
        if sym in returns_history and len(returns_history[sym]) >= 10:
            valid_positions.append(p)

    if not valid_positions:
        return VaRResult(portfolio_value=equity)

    n_assets = len(valid_positions)
    symbols = [p["symbol"] for p in valid_positions]

    # 1. Compute individual volatilities (std of returns)
    volatilities = np.zeros(n_assets)
    for i, p in enumerate(valid_positions):
        ret = np.array(returns_history[p["symbol"]], dtype=float)
        ret = ret[np.isfinite(ret)]
        if len(ret) > 0:
            volatilities[i] = np.std(ret)

    # Scale volatility to horizon (H4 bars -> horizon hours)
    bars_per_horizon = max(horizon_hours / 4, 1)
    scaled_vol = volatilities * np.sqrt(bars_per_horizon)

    # 2. Build correlation matrix
    corr_mat = np.eye(n_assets)
    if correlation_matrix and n_assets > 1:
        for i in range(n_assets):
            for j in range(i + 1, n_assets):
                sym_a, sym_b = symbols[i], symbols[j]
                corr = (
                    correlation_matrix.get(sym_a, {}).get(sym_b)
                    or correlation_matrix.get(sym_b, {}).get(sym_a)
                    or 0.0
                )
                corr_mat[i, j] = corr
                corr_mat[j, i] = corr

    # 3. Build covariance matrix: Cov = diag(vol) @ Corr @ diag(vol)
    vol_diag = np.diag(scaled_vol)
    cov_matrix = vol_diag @ corr_mat @ vol_diag

    # Ensure positive semi-definite (fix floating point issues)
    eigvals, eigvecs = np.linalg.eigh(cov_matrix)
    eigvals = np.maximum(eigvals, 1e-10)
    cov_matrix = eigvecs @ np.diag(eigvals) @ eigvecs.T

    # 4. Cholesky decomposition
    try:
        L = np.linalg.cholesky(cov_matrix)
    except np.linalg.LinAlgError:
        # Fallback: use diagonal (independent assets)
        logger.warning("Cholesky failed, falling back to independent simulation")
        L = np.diag(scaled_vol)

    # 5. Compute position exposures (signed notional value)
    exposures = np.zeros(n_assets)
    contract_sizes = {"forex": 100_000, "crypto": 1, "metal": 100, "energy": 1000}

    for i, p in enumerate(valid_positions):
        price = p.get("current_price", p.get("entry_price", 1.0))
        volume = p.get("volume", 0.0)
        side_sign = 1.0 if p.get("side", "BUY") == "BUY" else -1.0

        # Determine contract size
        contract_size = 100_000  # default forex
        try:
            from app.services.market.instrument import InstrumentClassifier
            desc = InstrumentClassifier.classify(p["symbol"])
            ac = desc.asset_class.value.lower()
            contract_size = contract_sizes.get(ac, 100_000)
        except Exception:
            pass

        exposures[i] = side_sign * volume * contract_size * price

    # 6. Monte Carlo simulation
    # Generate correlated random returns
    Z = rng.standard_normal((n_assets, n_simulations))
    simulated_returns = L @ Z  # (n_assets, n_simulations)

    # Portfolio PnL for each simulation
    pnl = exposures @ simulated_returns  # (n_simulations,)

    # 7. Extract VaR and CVaR
    var_95 = float(-np.percentile(pnl, 5))   # 5th percentile loss
    var_99 = float(-np.percentile(pnl, 1))   # 1st percentile loss
    cvar_95 = float(-np.mean(pnl[pnl <= np.percentile(pnl, 5)]))  # Expected Shortfall

    # Ensure non-negative
    var_95 = max(var_95, 0.0)
    var_99 = max(var_99, 0.0)
    cvar_95 = max(cvar_95, 0.0)

    # 8. Per-position VaR decomposition (component VaR)
    var_by_position: dict[str, float] = {}
    for i, p in enumerate(valid_positions):
        # Individual position PnL
        pos_pnl = exposures[i] * simulated_returns[i, :]
        pos_var = float(-np.percentile(pos_pnl, 5))
        var_by_position[p["symbol"]] = round(max(pos_var, 0.0), 2)

    # 9. Marginal VaR: VaR with position - VaR without position
    marginal_var: dict[str, float] = {}
    for i, p in enumerate(valid_positions):
        # Remove this position's exposure
        mod_exposures = exposures.copy()
        mod_exposures[i] = 0.0
        mod_pnl = mod_exposures @ simulated_returns
        mod_var_95 = float(-np.percentile(mod_pnl, 5))
        marginal = var_95 - max(mod_var_95, 0.0)
        marginal_var[p["symbol"]] = round(max(marginal, 0.0), 2)

    return VaRResult(
        var_95=round(var_95, 2),
        var_99=round(var_99, 2),
        var_95_pct=round((var_95 / equity) * 100, 2) if equity > 0 else 0.0,
        var_99_pct=round((var_99 / equity) * 100, 2) if equity > 0 else 0.0,
        cvar_95=round(cvar_95, 2),
        horizon_hours=horizon_hours,
        simulations=n_simulations,
        portfolio_value=equity,
        method="monte_carlo",
        var_by_position=var_by_position,
        marginal_var=marginal_var,
    )
