"""Currency exposure engine — compute net exposure per currency from open positions."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.risk.portfolio_state import OpenPosition

logger = logging.getLogger(__name__)


@dataclass
class CurrencyExposure:
    currency: str
    net_exposure_lots: float         # Positive = long, negative = short
    net_exposure_value: float        # In account currency
    exposure_pct: float              # As % of equity
    contributing_positions: list[str] = field(default_factory=list)


@dataclass
class CurrencyExposureReport:
    exposures: dict[str, CurrencyExposure] = field(default_factory=dict)
    dominant_currency: str = ""
    dominant_exposure_pct: float = 0.0
    total_gross_exposure_pct: float = 0.0
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


def compute_currency_exposure(
    positions: list[OpenPosition],
    equity: float,
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
    exposure_symbols: dict[str, list[str]] = {}

    # Contract size factors by asset class (approximate)
    _CONTRACT_SIZE = {
        "forex": 100_000,
        "crypto": 1,
        "metal": 100,
        "energy": 1000,
        "commodity": 1000,
    }

    for pos in positions:
        base, quote = _decompose_symbol(pos.symbol)
        if not base or not quote:
            continue

        # Determine asset class for contract size
        asset_class = "forex"
        try:
            from app.services.market.instrument import InstrumentClassifier
            desc = InstrumentClassifier.classify(pos.symbol)
            asset_class = desc.asset_class.value.lower()
        except Exception:
            pass

        sign = 1.0 if pos.side == "BUY" else -1.0
        lots = pos.volume * sign

        # Base currency: +lots if BUY, -lots if SELL
        exposure_lots[base] = exposure_lots.get(base, 0.0) + lots
        exposure_symbols.setdefault(base, [])
        if pos.symbol not in exposure_symbols[base]:
            exposure_symbols[base].append(pos.symbol)

        # Quote currency: -lots if BUY, +lots if SELL (opposite of base)
        exposure_lots[quote] = exposure_lots.get(quote, 0.0) - lots
        exposure_symbols.setdefault(quote, [])
        if pos.symbol not in exposure_symbols[quote]:
            exposure_symbols[quote].append(pos.symbol)

    # Build report
    exposures: dict[str, CurrencyExposure] = {}
    contract_size = 100_000  # Use forex standard for % calculation

    for currency, net_lots in exposure_lots.items():
        # Approximate exposure value in account currency
        net_value = abs(net_lots) * contract_size
        exp_pct = (net_value / equity) * 100 if equity > 0 else 0.0

        exposures[currency] = CurrencyExposure(
            currency=currency,
            net_exposure_lots=round(net_lots, 4),
            net_exposure_value=round(net_value, 2),
            exposure_pct=round(exp_pct, 1),
            contributing_positions=exposure_symbols.get(currency, []),
        )

    # Find dominant currency
    dominant = ""
    dominant_pct = 0.0
    total_gross = 0.0
    for ce in exposures.values():
        total_gross += ce.exposure_pct
        if ce.exposure_pct > dominant_pct:
            dominant_pct = ce.exposure_pct
            dominant = ce.currency

    # Generate warnings
    warnings: list[str] = []
    for ce in exposures.values():
        if ce.exposure_pct > 30.0:
            warnings.append(
                f"{ce.currency} exposure {ce.exposure_pct:.1f}% is high "
                f"(positions: {', '.join(ce.contributing_positions)})"
            )

    return CurrencyExposureReport(
        exposures=exposures,
        dominant_currency=dominant,
        dominant_exposure_pct=round(dominant_pct, 1),
        total_gross_exposure_pct=round(total_gross, 1),
        warnings=warnings,
    )
