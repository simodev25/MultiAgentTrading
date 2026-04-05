"""Currency exposure engine — compute net exposure per currency from open positions."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.risk.portfolio_state import OpenPosition

logger = logging.getLogger(__name__)


@dataclass
class CurrencyExposure:
    currency: str
    net_exposure_lots: float         # Positive = long, negative = short
    net_exposure_value: float        # In account currency
    currency_notional_exposure_pct: float
    # Deterministic approximation for observability: a position contributes its
    # full stop-based `risk_pct` to each currency touched by the symbol.
    currency_open_risk_pct: float
    contributing_positions: list[str] = field(default_factory=list)


@dataclass
class CurrencyExposureReport:
    exposures: dict[str, CurrencyExposure] = field(default_factory=dict)
    dominant_currency: str = ""
    dominant_notional_exposure_pct: float = 0.0
    total_gross_notional_exposure_pct: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _decompose_symbol(symbol: str) -> tuple[str | None, str | None]:
    """Decompose a symbol into base and quote currency using InstrumentClassifier.

    Returns (base, quote) or (None, None) if decomposition fails.
    """
    try:
        from app.services.market.instrument import InstrumentClassifier
        descriptor = InstrumentClassifier.classify(symbol)
        if descriptor.has_base_quote and descriptor.base_asset and descriptor.quote_asset:
            return descriptor.base_asset, descriptor.quote_asset
    except Exception as exc:
        logger.debug("Symbol decomposition failed for %s: %s", symbol, exc)
    return None, None


def _get_contract_size(symbol: str) -> float:
    """Resolve approximate contract size for a symbol."""
    contract_size = 100_000.0
    try:
        from app.services.market.instrument import InstrumentClassifier
        desc = InstrumentClassifier.classify(symbol)
        asset_class = desc.asset_class.value.lower()
        sizes = {
            "forex": 100_000.0,
            "crypto": 1.0,
            "metal": 100.0,
            "energy": 1000.0,
            "commodity": 1000.0,
        }
        contract_size = sizes.get(asset_class, 100_000.0)
    except Exception:
        pass
    return contract_size


def _build_conversion_graph(positions: list[OpenPosition]) -> dict[str, list[tuple[str, float]]]:
    """Build a simple FX graph from open positions current prices."""
    graph: dict[str, list[tuple[str, float]]] = {}
    for pos in positions:
        base, quote = _decompose_symbol(pos.symbol)
        price = pos.current_price or pos.entry_price or 0.0
        if not base or not quote or price <= 0:
            continue
        graph.setdefault(base, []).append((quote, price))
        graph.setdefault(quote, []).append((base, 1.0 / price))
    return graph


def _find_conversion_rate(
    graph: dict[str, list[tuple[str, float]]],
    source: str,
    target: str,
) -> float | None:
    """Find multiplicative conversion rate from source currency to target."""
    if source == target:
        return 1.0
    queue: deque[tuple[str, float]] = deque([(source, 1.0)])
    seen = {source}
    while queue:
        curr, rate = queue.popleft()
        for nxt, edge_rate in graph.get(curr, []):
            if nxt in seen:
                continue
            next_rate = rate * edge_rate
            if nxt == target:
                return next_rate
            seen.add(nxt)
            queue.append((nxt, next_rate))
    return None


def compute_currency_exposure(
    positions: list[OpenPosition],
    equity: float,
    account_currency: str = "USD",
) -> CurrencyExposureReport:
    """Compute net currency exposure from a list of open positions.

    For each position:
    - BUY EURUSD → +EUR volume, -USD volume
    - SELL EURUSD → -EUR volume, +USD volume

    Exposure value is approximated as volume * contract_size_factor.
    For forex, 1 lot = 100,000 units of base currency.
    """
    if equity <= 0:
        return CurrencyExposureReport(warnings=["equity_zero_or_negative"])

    # Accumulate exposure per currency
    exposure_lots: dict[str, float] = {}
    exposure_units: dict[str, float] = {}
    exposure_symbols: dict[str, list[str]] = {}
    exposure_risk_pct: dict[str, float] = {}

    for pos in positions:
        base, quote = _decompose_symbol(pos.symbol)
        if not base or not quote:
            continue

        sign = 1.0 if pos.side == "BUY" else -1.0
        lots = pos.volume * sign
        contract_size = _get_contract_size(pos.symbol)
        price = pos.current_price or pos.entry_price or 0.0
        base_units = pos.volume * contract_size * sign
        quote_units = -(base_units * price)

        # Base currency: +lots if BUY, -lots if SELL
        exposure_lots[base] = exposure_lots.get(base, 0.0) + lots
        exposure_units[base] = exposure_units.get(base, 0.0) + base_units
        exposure_symbols.setdefault(base, [])
        if pos.symbol not in exposure_symbols[base]:
            exposure_symbols[base].append(pos.symbol)
        exposure_risk_pct[base] = exposure_risk_pct.get(base, 0.0) + max(float(pos.risk_pct or 0.0), 0.0)

        # Quote currency: -lots if BUY, +lots if SELL (opposite of base)
        exposure_lots[quote] = exposure_lots.get(quote, 0.0) - lots
        exposure_units[quote] = exposure_units.get(quote, 0.0) + quote_units
        exposure_symbols.setdefault(quote, [])
        if pos.symbol not in exposure_symbols[quote]:
            exposure_symbols[quote].append(pos.symbol)
        exposure_risk_pct[quote] = exposure_risk_pct.get(quote, 0.0) + max(float(pos.risk_pct or 0.0), 0.0)

    # Build report
    exposures: dict[str, CurrencyExposure] = {}
    conversion_graph = _build_conversion_graph(positions)
    warnings: list[str] = []

    for currency, net_lots in exposure_lots.items():
        net_units = exposure_units.get(currency, 0.0)
        rate_to_account = _find_conversion_rate(conversion_graph, currency, account_currency)
        if rate_to_account is None:
            warnings.append(f"conversion_unavailable:{currency}->{account_currency}")
            net_value = abs(net_units)
        else:
            net_value = abs(net_units) * rate_to_account
        exp_pct = (net_value / equity) * 100 if equity > 0 else 0.0
        open_risk_pct = exposure_risk_pct.get(currency, 0.0)

        exposures[currency] = CurrencyExposure(
            currency=currency,
            net_exposure_lots=round(net_lots, 4),
            net_exposure_value=round(net_value, 2),
            currency_notional_exposure_pct=round(exp_pct, 1),
            currency_open_risk_pct=round(open_risk_pct, 2),
            contributing_positions=exposure_symbols.get(currency, []),
        )

    # Find dominant currency
    dominant = ""
    dominant_pct = 0.0
    total_gross = 0.0
    for ce in exposures.values():
        total_gross += ce.currency_notional_exposure_pct
        if ce.currency_notional_exposure_pct > dominant_pct:
            dominant_pct = ce.currency_notional_exposure_pct
            dominant = ce.currency

    # Generate warnings
    for ce in exposures.values():
        if ce.currency_notional_exposure_pct > 30.0:
            warnings.append(
                f"{ce.currency} currency_notional_exposure_pct {ce.currency_notional_exposure_pct:.1f}% is high "
                f"(positions: {', '.join(ce.contributing_positions)})"
            )

    return CurrencyExposureReport(
        exposures=exposures,
        dominant_currency=dominant,
        dominant_notional_exposure_pct=round(dominant_pct, 1),
        total_gross_notional_exposure_pct=round(total_gross, 1),
        warnings=warnings,
    )


def serialize_currency_exposure_report(report: CurrencyExposureReport) -> dict[str, dict[str, float | list[str]]]:
    """Serialize canonical exposure fields for API payloads."""
    return {
        ce.currency: {
            "net_lots": ce.net_exposure_lots,
            "currency_notional_exposure_pct": ce.currency_notional_exposure_pct,
            "currency_open_risk_pct": ce.currency_open_risk_pct,
            "contributing_positions": ce.contributing_positions,
        }
        for ce in report.exposures.values()
    }
