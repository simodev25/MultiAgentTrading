"""Stress testing engine — simulate extreme market scenarios on portfolio."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.risk.portfolio_state import OpenPosition

logger = logging.getLogger(__name__)


@dataclass
class StressScenario:
    name: str
    description: str
    shocks: dict[str, float]       # Shock per currency/symbol (in %)
    probability: str               # "rare" | "occasional" | "extreme"


@dataclass
class StressTestResult:
    scenario: str
    description: str
    portfolio_pnl: float           # Simulated PnL in account currency
    portfolio_pnl_pct: float       # Simulated PnL as % of equity
    surviving: bool                # True if equity > 0 after shock
    margin_call: bool              # True if equity < used_margin
    positions_affected: list[dict] = field(default_factory=list)


@dataclass
class StressTestReport:
    results: list[StressTestResult] = field(default_factory=list)
    worst_case_pnl_pct: float = 0.0
    scenarios_surviving: int = 0
    scenarios_total: int = 0
    recommendation: str = "safe"   # "safe" | "reduce_exposure" | "critical"


# ── Predefined scenarios ──

SCENARIOS: list[StressScenario] = [
    StressScenario(
        name="usd_crash",
        description="USD crashes: USD -3%, EUR +2%, GBP +1.5%, JPY +2.5%, XAU +4%",
        shocks={"USD": -3.0, "EUR": 2.0, "GBP": 1.5, "JPY": 2.5, "CHF": 1.0, "XAU": 4.0, "XAG": 5.0},
        probability="rare",
    ),
    StressScenario(
        name="usd_rally",
        description="USD rallies: USD +3%, EUR -2.5%, GBP -2%, JPY -1%, XAU -3%",
        shocks={"USD": 3.0, "EUR": -2.5, "GBP": -2.0, "JPY": -1.0, "XAU": -3.0, "XAG": -4.0},
        probability="rare",
    ),
    StressScenario(
        name="risk_off",
        description="Risk-off: equities -5%, crypto -15%, XAU +3%, JPY +2%, CHF +1.5%",
        shocks={"BTC": -15.0, "ETH": -20.0, "SOL": -25.0, "XAU": 3.0, "JPY": 2.0, "CHF": 1.5, "EUR": -1.0, "GBP": -1.5},
        probability="occasional",
    ),
    StressScenario(
        name="flash_crash",
        description="Flash crash: all assets -5% to -8%, high spread",
        shocks={"USD": -2.0, "EUR": -5.0, "GBP": -6.0, "JPY": -3.0, "CHF": -4.0,
                "AUD": -7.0, "NZD": -8.0, "CAD": -5.0, "XAU": -5.0, "XAG": -8.0,
                "BTC": -10.0, "ETH": -12.0},
        probability="extreme",
    ),
    StressScenario(
        name="rate_shock",
        description="Rate shock: JPY -4%, EUR -1.5%, GBP -2%, USD +2%",
        shocks={"JPY": -4.0, "EUR": -1.5, "GBP": -2.0, "USD": 2.0, "CHF": -0.5},
        probability="rare",
    ),
    StressScenario(
        name="crypto_collapse",
        description="Crypto collapse: BTC -20%, ETH -25%, altcoins -30%",
        shocks={"BTC": -20.0, "ETH": -25.0, "SOL": -30.0, "ADA": -30.0, "DOT": -30.0,
                "LINK": -25.0, "DOGE": -35.0, "AVAX": -30.0, "MATIC": -30.0},
        probability="occasional",
    ),
    StressScenario(
        name="commodity_spike",
        description="Commodity spike: Oil +15%, XAU +5%, USD -1%",
        shocks={"XAU": 5.0, "XAG": 7.0, "USD": -1.0, "EUR": 0.5, "CAD": 2.0},
        probability="rare",
    ),
    StressScenario(
        name="liquidity_crisis",
        description="Liquidity crisis: all assets -3%, extreme spread",
        shocks={"USD": -1.0, "EUR": -3.0, "GBP": -3.0, "JPY": -2.0, "CHF": -2.0,
                "AUD": -4.0, "NZD": -4.0, "CAD": -3.0, "XAU": -2.0,
                "BTC": -8.0, "ETH": -10.0},
        probability="extreme",
    ),
]


def _decompose_position(pos: OpenPosition) -> tuple[str | None, str | None]:
    """Get base/quote currency for a position."""
    try:
        from app.services.market.instrument import InstrumentClassifier
        desc = InstrumentClassifier.classify(pos.symbol)
        if desc.has_base_quote:
            return desc.base_asset, desc.quote_asset
    except Exception:
        pass
    return None, None


def _estimate_position_value(pos: OpenPosition) -> float:
    """Estimate notional value of a position."""
    contract_size = 100_000  # default forex
    try:
        from app.services.market.instrument import InstrumentClassifier
        desc = InstrumentClassifier.classify(pos.symbol)
        ac = desc.asset_class.value.lower()
        sizes = {"forex": 100_000, "crypto": 1, "metal": 100, "energy": 1000}
        contract_size = sizes.get(ac, 100_000)
    except Exception:
        pass
    return pos.volume * contract_size * pos.current_price


def run_stress_test(
    positions: list[OpenPosition],
    equity: float,
    used_margin: float = 0.0,
    scenarios: list[StressScenario] | None = None,
) -> StressTestReport:
    """Run stress tests on current portfolio.

    For each scenario, applies currency shocks to positions and computes PnL impact.
    """
    if not positions or equity <= 0:
        return StressTestReport(recommendation="safe")

    test_scenarios = scenarios or SCENARIOS
    results: list[StressTestResult] = []
    worst_pnl_pct = 0.0
    surviving_count = 0

    for scenario in test_scenarios:
        total_pnl = 0.0
        affected: list[dict] = []

        for pos in positions:
            base, quote = _decompose_position(pos)
            if not base:
                continue

            # Calculate price shock
            base_shock = scenario.shocks.get(base, 0.0) / 100.0
            quote_shock = scenario.shocks.get(quote, 0.0) / 100.0

            # For a pair BASE/QUOTE:
            # If base goes up by X% and quote goes down by Y%,
            # the pair moves approximately by (X - Y)%
            pair_shock_pct = base_shock - quote_shock

            # Position PnL
            side_sign = 1.0 if pos.side == "BUY" else -1.0
            notional = _estimate_position_value(pos)
            position_pnl = side_sign * notional * pair_shock_pct

            total_pnl += position_pnl
            if abs(position_pnl) > 0.01:
                affected.append({
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "volume": pos.volume,
                    "shock_pct": round(pair_shock_pct * 100, 2),
                    "pnl": round(position_pnl, 2),
                })

        pnl_pct = (total_pnl / equity) * 100 if equity > 0 else 0.0
        post_equity = equity + total_pnl
        surviving = post_equity > 0
        margin_call = post_equity < used_margin if used_margin > 0 else False

        if surviving:
            surviving_count += 1
        if pnl_pct < worst_pnl_pct:
            worst_pnl_pct = pnl_pct

        results.append(StressTestResult(
            scenario=scenario.name,
            description=scenario.description,
            portfolio_pnl=round(total_pnl, 2),
            portfolio_pnl_pct=round(pnl_pct, 2),
            surviving=surviving,
            margin_call=margin_call,
            positions_affected=affected,
        ))

    # Recommendation
    total = len(results)
    if surviving_count == total and worst_pnl_pct > -5.0:
        recommendation = "safe"
    elif surviving_count >= total - 1 and worst_pnl_pct > -15.0:
        recommendation = "reduce_exposure"
    else:
        recommendation = "critical"

    return StressTestReport(
        results=results,
        worst_case_pnl_pct=round(worst_pnl_pct, 2),
        scenarios_surviving=surviving_count,
        scenarios_total=total,
        recommendation=recommendation,
    )
