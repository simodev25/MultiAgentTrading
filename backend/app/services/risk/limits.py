"""Risk limits configuration per trading mode."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    # Tier 1
    max_risk_per_trade_pct: float     # Max risk per single trade
    max_daily_loss_pct: float         # Max daily loss before halt
    max_open_risk_pct: float          # Max total open risk
    max_positions: int                # Max simultaneous positions
    max_positions_per_symbol: int     # Max positions per symbol
    min_free_margin_pct: float        # Min free margin percentage
    # Tier 2
    max_currency_exposure_pct: float = 40.0   # Max exposure per currency (% equity)
    max_gross_exposure_pct: float = 100.0      # Max total gross exposure
    max_correlation_risk_multiplier: float = 3.0  # Max effective risk multiplier from correlation
    max_weekly_loss_pct: float = 15.0          # Max weekly loss
    # Tier 3
    max_var_95_pct: float = 15.0              # Max VaR 95% as % of equity
    max_marginal_var_pct: float = 5.0         # Max marginal VaR of a new trade
    min_diversification_score: float = 0.2    # Min diversification score (0-1)
    stress_test_survival_required: tuple[str, ...] = ("risk_off",)  # Scenarios portfolio must survive


RISK_LIMITS: dict[str, RiskLimits] = {
    "simulation": RiskLimits(
        max_risk_per_trade_pct=5.0,
        max_daily_loss_pct=10.0,
        max_open_risk_pct=15.0,
        max_positions=10,
        max_positions_per_symbol=3,
        min_free_margin_pct=20.0,
        max_currency_exposure_pct=40.0,
        max_gross_exposure_pct=100.0,
        max_correlation_risk_multiplier=3.0,
        max_weekly_loss_pct=15.0,
        max_var_95_pct=15.0,
        max_marginal_var_pct=5.0,
        min_diversification_score=0.2,
        stress_test_survival_required=("risk_off",),
    ),
    "paper": RiskLimits(
        max_risk_per_trade_pct=3.0,
        max_daily_loss_pct=6.0,
        max_open_risk_pct=10.0,
        max_positions=5,
        max_positions_per_symbol=2,
        min_free_margin_pct=30.0,
        max_currency_exposure_pct=25.0,
        max_gross_exposure_pct=60.0,
        max_correlation_risk_multiplier=2.0,
        max_weekly_loss_pct=10.0,
        max_var_95_pct=10.0,
        max_marginal_var_pct=3.0,
        min_diversification_score=0.3,
        stress_test_survival_required=("risk_off", "flash_crash"),
    ),
    "live": RiskLimits(
        max_risk_per_trade_pct=2.0,
        max_daily_loss_pct=3.0,
        max_open_risk_pct=6.0,
        max_positions=3,
        max_positions_per_symbol=1,
        min_free_margin_pct=50.0,
        max_currency_exposure_pct=15.0,
        max_gross_exposure_pct=40.0,
        max_correlation_risk_multiplier=1.5,
        max_weekly_loss_pct=5.0,
        max_var_95_pct=5.0,
        max_marginal_var_pct=2.0,
        min_diversification_score=0.4,
        stress_test_survival_required=("risk_off", "flash_crash", "usd_crash"),
    ),
}


def get_risk_limits(mode: str) -> RiskLimits:
    """Return RiskLimits for the given mode, with runtime DB overrides if available."""
    try:
        from app.services.config.trading_config import get_effective_risk_limits
        return get_effective_risk_limits(mode)
    except Exception:
        return RISK_LIMITS.get(mode, RISK_LIMITS["live"])
