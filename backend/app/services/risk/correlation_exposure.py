"""Correlation exposure detector — identify correlated positions that amplify risk."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.risk.portfolio_state import OpenPosition

logger = logging.getLogger(__name__)


@dataclass
class CorrelationAlert:
    position_a: str
    position_b: str
    correlation: float
    same_direction: bool
    risk_multiplier: float
    severity: str                # "high" | "medium" | "low"
    message: str


@dataclass
class CorrelationExposureReport:
    alerts: list[CorrelationAlert] = field(default_factory=list)
    effective_risk_multiplier: float = 1.0
    max_pairwise_correlation: float = 0.0
    adjusted_open_risk_pct: float = 0.0
    should_reduce: bool = False


def _get_correlation(symbol_a: str, symbol_b: str) -> float | None:
    """Get correlation between two symbols using the correlation_analyzer MCP tool.

    Uses Redis cache (key: corr:{A}:{B}, TTL 1h) to avoid redundant calculations.
    Falls back to None if data is insufficient.
    """
    import redis
    from app.core.config import get_settings

    settings = get_settings()
    cache_key = f"corr:{min(symbol_a, symbol_b)}:{max(symbol_a, symbol_b)}"

    # Try cache first
    try:
        r = redis.from_url(settings.redis_url)
        cached = r.get(cache_key)
        if cached is not None:
            return float(cached)
    except Exception:
        pass

    # Compute correlation
    try:
        from app.services.mcp.trading_server import correlation_analyzer
        from app.services.market.data_provider import MarketProvider

        provider = MarketProvider()
        closes_a = provider.get_close_prices(symbol_a, timeframe="H4", bars=120)
        closes_b = provider.get_close_prices(symbol_b, timeframe="H4", bars=120)

        if not closes_a or not closes_b:
            return None

        result = correlation_analyzer(
            primary_closes=closes_a,
            secondary_closes=closes_b,
            primary_symbol=symbol_a,
            secondary_symbol=symbol_b,
            period=30,
        )

        if result.get("error"):
            return None

        corr = result.get("recent_correlation", result.get("overall_correlation", 0.0))

        # Cache for 1 hour
        try:
            r = redis.from_url(settings.redis_url)
            r.setex(cache_key, 3600, str(corr))
        except Exception:
            pass

        return corr
    except Exception as exc:
        logger.debug("Correlation fetch failed for %s/%s: %s", symbol_a, symbol_b, exc)
        return None


def _correlation_to_multiplier(corr: float, same_direction: bool) -> float:
    """Convert correlation + direction into a risk multiplier.

    Same direction + high correlation = amplified risk.
    Opposite direction + high correlation = hedge effect.
    """
    abs_corr = abs(corr)

    if not same_direction:
        # Opposite direction on correlated pair = hedge
        if abs_corr >= 0.80:
            return 0.6  # Effective hedge
        if abs_corr >= 0.50:
            return 0.8
        return 1.0

    # Same direction on correlated pair = amplified risk
    if abs_corr >= 0.80:
        return 1.8
    if abs_corr >= 0.50:
        return 1.4
    return 1.0


def _correlation_severity(abs_corr: float) -> str:
    if abs_corr >= 0.80:
        return "high"
    if abs_corr >= 0.50:
        return "medium"
    return "low"


def compute_correlation_exposure(
    positions: list[OpenPosition],
    open_risk_total_pct: float,
    max_correlation_risk_multiplier: float = 2.0,
) -> CorrelationExposureReport:
    """Detect correlated positions and compute effective risk multiplier.

    For N positions, checks N*(N-1)/2 pairs.
    Limited to 10 positions max → max 45 pairs → acceptable perf.
    """
    if len(positions) < 2:
        return CorrelationExposureReport(
            adjusted_open_risk_pct=open_risk_total_pct,
        )

    alerts: list[CorrelationAlert] = []
    max_corr = 0.0
    multipliers: list[float] = []

    # Get unique symbols
    symbols = list({p.symbol for p in positions})
    if len(symbols) < 2:
        return CorrelationExposureReport(
            adjusted_open_risk_pct=open_risk_total_pct,
        )

    # Check all pairs
    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            sym_a, sym_b = symbols[i], symbols[j]

            corr = _get_correlation(sym_a, sym_b)
            if corr is None:
                continue

            abs_corr = abs(corr)
            if abs_corr > max_corr:
                max_corr = abs_corr

            if abs_corr < 0.50:
                continue  # Below threshold, skip

            # Check if positions are in the same direction
            sides_a = {p.side for p in positions if p.symbol == sym_a}
            sides_b = {p.side for p in positions if p.symbol == sym_b}

            # If there are multiple positions on same symbol with different sides,
            # use the dominant side
            dominant_a = "BUY" if sides_a == {"BUY"} else ("SELL" if sides_a == {"SELL"} else "MIXED")
            dominant_b = "BUY" if sides_b == {"BUY"} else ("SELL" if sides_b == {"SELL"} else "MIXED")

            if dominant_a == "MIXED" or dominant_b == "MIXED":
                continue  # Can't determine direction, skip

            # For positive correlation: same side = same direction
            # For negative correlation: opposite side = same direction
            if corr > 0:
                same_direction = (dominant_a == dominant_b)
            else:
                same_direction = (dominant_a != dominant_b)

            mult = _correlation_to_multiplier(corr, same_direction)
            multipliers.append(mult)
            severity = _correlation_severity(abs_corr)

            if same_direction:
                msg = (
                    f"{dominant_a} {sym_a} + {dominant_b} {sym_b}: "
                    f"correlation {corr:.2f}, same effective direction "
                    f"→ {mult:.1f}x risk"
                )
            else:
                msg = (
                    f"{dominant_a} {sym_a} + {dominant_b} {sym_b}: "
                    f"correlation {corr:.2f}, opposite direction "
                    f"→ hedge effect ({mult:.1f}x)"
                )

            alerts.append(CorrelationAlert(
                position_a=sym_a,
                position_b=sym_b,
                correlation=round(corr, 4),
                same_direction=same_direction,
                risk_multiplier=mult,
                severity=severity,
                message=msg,
            ))

    # Compute effective multiplier: weighted average of all pair multipliers
    if multipliers:
        effective_mult = sum(multipliers) / len(multipliers)
    else:
        effective_mult = 1.0

    adjusted_risk = round(open_risk_total_pct * effective_mult, 2)
    should_reduce = effective_mult > max_correlation_risk_multiplier

    return CorrelationExposureReport(
        alerts=alerts,
        effective_risk_multiplier=round(effective_mult, 2),
        max_pairwise_correlation=round(max_corr, 4),
        adjusted_open_risk_pct=adjusted_risk,
        should_reduce=should_reduce,
    )
