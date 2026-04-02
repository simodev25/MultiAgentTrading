"""Risk engine — deterministic barrier before any trade execution.

Uses InstrumentDescriptor for correct pip/tick sizing per asset class.
Supports forex, crypto, indices, commodities, metals, equities.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.observability.metrics import risk_evaluation_total

if TYPE_CHECKING:
    from app.services.risk.limits import RiskLimits
    from app.services.risk.portfolio_state import PortfolioState

logger = logging.getLogger(__name__)


@dataclass
class ProposedTrade:
    """Describes a trade proposal to be validated against portfolio limits."""
    decision: str           # BUY | SELL | HOLD
    pair: str | None = None
    entry_price: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_percent: float = 1.0
    mode: str = "simulation"
    asset_class: str | None = None


@dataclass
class RiskAssessment:
    accepted: bool
    reasons: list[str]
    suggested_volume: float
    pip_size: float = 0.0001
    pip_value_per_lot: float = 10.0
    margin_required: float = 0.0
    asset_class: str = 'unknown'


# ---------------------------------------------------------------------------
# Per-asset-class contract specifications
# ---------------------------------------------------------------------------

_CONTRACT_SPECS: dict[str, dict[str, Any]] = {
    'unknown': {
        'default_pip_size': 0.01,
        'jpy_pip_size': 0.01,
        'pip_value_per_lot': 1.0,
        'contract_size': 1,
        'min_volume': 0.01,
        'max_volume': 100.0,
        'volume_step': 0.01,
    },
    'forex': {
        'default_pip_size': 0.0001,
        'jpy_pip_size': 0.01,
        'pip_value_per_lot': 10.0,
        'contract_size': 100_000,
        'min_volume': 0.01,
        'max_volume': 10.0,
        'volume_step': 0.01,
    },
    'crypto': {
        'pip_value_per_lot': 1.0,
        'contract_size': 1,
        'min_volume': 0.01,
        'max_volume': 100.0,
        'volume_step': 0.01,
    },
    'index': {
        'default_pip_size': 1.0,
        'pip_value_per_lot': 1.0,
        'contract_size': 1,
        'min_volume': 0.1,
        'max_volume': 50.0,
        'volume_step': 0.1,
    },
    'metal': {
        'default_pip_size': 0.01,
        'pip_value_per_lot': 10.0,
        'contract_size': 100,
        'min_volume': 0.01,
        'max_volume': 10.0,
        'volume_step': 0.01,
    },
    'energy': {
        'default_pip_size': 0.01,
        'pip_value_per_lot': 10.0,
        'contract_size': 1000,
        'min_volume': 0.01,
        'max_volume': 10.0,
        'volume_step': 0.01,
    },
    'commodity': {
        'default_pip_size': 0.01,
        'pip_value_per_lot': 10.0,
        'contract_size': 1000,
        'min_volume': 0.01,
        'max_volume': 10.0,
        'volume_step': 0.01,
    },
    'equity': {
        'default_pip_size': 0.01,
        'pip_value_per_lot': 1.0,
        'contract_size': 1,
        'min_volume': 1.0,
        'max_volume': 1000.0,
        'volume_step': 1.0,
    },
    'etf': {
        'default_pip_size': 0.01,
        'pip_value_per_lot': 1.0,
        'contract_size': 1,
        'min_volume': 1.0,
        'max_volume': 1000.0,
        'volume_step': 1.0,
    },
}


class RiskEngine:
    """Deterministic risk validation engine with multi-asset support."""

    @staticmethod
    def _is_fx_like_symbol(symbol: str | None) -> bool:
        normalized = str(symbol or '').upper().split('.', 1)[0]
        return re.fullmatch(r'[A-Z]{6}', normalized) is not None

    @classmethod
    def _resolve_asset_class(cls, pair: str | None, asset_class: str | None = None) -> str:
        """Determine asset class from explicit parameter or symbol heuristic."""
        if asset_class and asset_class.lower() in _CONTRACT_SPECS:
            return asset_class.lower()

        # Try to import InstrumentClassifier for accurate classification
        try:
            from app.services.market.instrument import InstrumentClassifier
            descriptor = InstrumentClassifier.classify(str(pair or ''))
            return descriptor.asset_class.value.lower()
        except Exception as exc:
            logger.warning("InstrumentClassifier.classify failed for %s: %s", pair, exc)

        # Fallback heuristic
        normalized = str(pair or '').upper().split('.', 1)[0]
        if cls._is_fx_like_symbol(normalized):
            return 'forex'
        return 'unknown'

    @classmethod
    def _pip_size(cls, pair: str | None, price: float | None = None, asset_class: str | None = None) -> float:
        ac = cls._resolve_asset_class(pair, asset_class)
        spec = _CONTRACT_SPECS.get(ac, {})

        if ac == 'forex':
            normalized = str(pair or '').upper().split('.', 1)[0]
            if normalized.endswith('JPY'):
                return spec.get('jpy_pip_size', 0.01)
            return spec.get('default_pip_size', 0.0001)

        if 'default_pip_size' in spec:
            return spec['default_pip_size']

        # Crypto: adaptive pip size based on price
        if ac == 'crypto':
            price_value = abs(float(price or 0.0))
            if price_value >= 10000:
                return 1.0
            if price_value >= 100:
                return 0.1
            if price_value >= 1:
                return 0.01
            if price_value >= 0.01:
                return 0.0001
            return 0.000001

        # Generic fallback based on price
        price_value = abs(float(price or 0.0))
        if price_value >= 1000:
            return 1.0
        if price_value >= 100:
            return 0.1
        if price_value >= 1:
            return 0.01
        if price_value >= 0.1:
            return 0.001
        return 0.0001

    @classmethod
    def _pip_value_per_lot(cls, pair: str | None, asset_class: str | None = None) -> float:
        ac = cls._resolve_asset_class(pair, asset_class)
        spec = _CONTRACT_SPECS.get(ac, {})
        return float(spec.get('pip_value_per_lot', 10.0))

    @classmethod
    def _volume_limits(cls, pair: str | None, asset_class: str | None = None) -> tuple[float, float]:
        ac = cls._resolve_asset_class(pair, asset_class)
        spec = _CONTRACT_SPECS.get(ac, _CONTRACT_SPECS['unknown'])
        return float(spec.get('min_volume', 0.01)), float(spec.get('max_volume', 10.0))

    @staticmethod
    def _round_to_step(volume: float, step: float) -> float:
        """Floor volume to the nearest valid broker step size."""
        if step <= 0:
            return volume
        return round(math.floor(volume / step) * step, 8)

    def evaluate(
        self,
        mode: str,
        decision: str,
        risk_percent: float,
        price: float,
        stop_loss: float | None,
        pair: str | None = None,
        equity: float = 10000.0,
        asset_class: str | None = None,
        leverage: float = 100.0,
    ) -> RiskAssessment:
        reasons: list[str] = []
        ac = self._resolve_asset_class(pair, asset_class)

        if decision == 'HOLD':
            return RiskAssessment(
                accepted=True,
                reasons=['No trade requested (HOLD).'],
                suggested_volume=0.0,
                asset_class=ac,
            )

        # Validate numeric inputs — reject NaN/Inf/negative
        if not (isinstance(price, (int, float)) and math.isfinite(price) and price > 0):
            return RiskAssessment(
                accepted=False,
                reasons=[f'Invalid price: {price}'],
                suggested_volume=0.0,
                asset_class=ac,
            )
        if not (isinstance(equity, (int, float)) and math.isfinite(equity) and equity > 0):
            return RiskAssessment(
                accepted=False,
                reasons=[f'Invalid equity: {equity}'],
                suggested_volume=0.0,
                asset_class=ac,
            )
        if not (isinstance(risk_percent, (int, float)) and math.isfinite(risk_percent) and risk_percent > 0):
            return RiskAssessment(
                accepted=False,
                reasons=[f'Invalid risk_percent: {risk_percent}'],
                suggested_volume=0.0,
                asset_class=ac,
            )

        if stop_loss is None:
            return RiskAssessment(
                accepted=False,
                reasons=['Stop loss is mandatory.'],
                suggested_volume=0.0,
                asset_class=ac,
            )
        if not (isinstance(stop_loss, (int, float)) and math.isfinite(stop_loss) and stop_loss > 0):
            return RiskAssessment(
                accepted=False,
                reasons=[f'Invalid stop_loss: {stop_loss}'],
                suggested_volume=0.0,
                asset_class=ac,
            )

        max_risk = {'simulation': 5.0, 'paper': 3.0, 'live': 2.0}.get(mode, 2.0)
        if risk_percent > max_risk:
            reasons.append(f'Risk percent {risk_percent}% exceeds mode limit {max_risk}% for {mode}.')

        stop_distance = abs(price - stop_loss)
        if stop_distance <= 0:
            reasons.append('Stop loss distance must be > 0.')

        min_sl_pct = 0.0005  # 0.05% minimum distance
        if price > 0 and stop_distance / price < min_sl_pct:
            reasons.append('Stop loss is too tight for current market volatility.')

        # Position sizing with correct pip/contract specs
        pip_size = self._pip_size(pair, price, asset_class)
        pip_value = self._pip_value_per_lot(pair, asset_class)
        min_vol, max_vol = self._volume_limits(pair, asset_class)

        risk_amount = equity * (risk_percent / 100)
        sl_pips = max(stop_distance / pip_size, 0.1) if pip_size > 0 else 0.1
        suggested_volume = risk_amount / (sl_pips * pip_value) if pip_value > 0 else min_vol
        suggested_volume = max(min(suggested_volume, max_vol), min_vol)

        # Align to broker volume step (floor to avoid exceeding risk budget)
        spec = _CONTRACT_SPECS.get(ac, {})
        volume_step = float(spec.get('volume_step', 0.01))
        suggested_volume = self._round_to_step(suggested_volume, volume_step)
        suggested_volume = max(suggested_volume, min_vol)  # ensure >= min after flooring
        contract_size = float(spec.get('contract_size', 100_000))
        effective_leverage = leverage if isinstance(leverage, (int, float)) and leverage > 0 else 100.0
        margin_required = (suggested_volume * contract_size * price) / effective_leverage

        accepted = len(reasons) == 0
        if accepted:
            reasons.append('Risk checks passed.')

        risk_evaluation_total.labels(
            accepted=str(accepted).lower(),
            asset_class=ac,
            mode=mode,
        ).inc()

        return RiskAssessment(
            accepted=accepted,
            reasons=reasons,
            suggested_volume=round(suggested_volume, 4),
            pip_size=pip_size,
            pip_value_per_lot=pip_value,
            margin_required=round(margin_required, 2),
            asset_class=ac,
        )

    def calculate_position_size(
        self,
        asset_class: str,
        entry_price: float,
        stop_loss: float,
        risk_percent: float,
        equity: float = 10000.0,
        leverage: float = 1.0,
        pair: str | None = None,
    ) -> dict[str, Any]:
        """Standalone position sizing using the canonical contract specs.

        This is the **single source of truth** for position sizing across the
        platform.  The MCP ``position_size_calculator`` tool delegates here so
        that specs are never duplicated.
        """
        ac = self._resolve_asset_class(pair, asset_class)
        spec = _CONTRACT_SPECS.get(ac, _CONTRACT_SPECS['unknown'])

        # Validate numeric inputs
        for label, val in [('entry_price', entry_price), ('stop_loss', stop_loss),
                           ('risk_percent', risk_percent), ('equity', equity)]:
            if not (isinstance(val, (int, float)) and math.isfinite(val) and val > 0):
                return {'error': f'invalid_{label}', 'suggested_volume': 0.0, 'detail': f'{label}={val}'}

        stop_distance = abs(entry_price - stop_loss)

        if stop_distance <= 0:
            return {'error': 'stop_loss_same_as_entry', 'suggested_volume': 0.0}

        pip_size = self._pip_size(pair, entry_price, ac)
        pip_value = self._pip_value_per_lot(pair, ac)
        min_vol, max_vol = self._volume_limits(pair, ac)
        contract_size = float(spec.get('contract_size', 100_000))

        risk_amount = equity * (risk_percent / 100.0)
        sl_pips = max(stop_distance / pip_size, 0.1) if pip_size > 0 else 0.1
        raw_volume = risk_amount / (sl_pips * pip_value) if pip_value > 0 else min_vol

        suggested = max(min(raw_volume, max_vol), min_vol)
        volume_step = float(spec.get('volume_step', 0.01))
        suggested = self._round_to_step(suggested, volume_step)
        suggested = max(suggested, min_vol)

        effective_leverage = leverage if leverage > 0 else 1.0
        margin_required = (suggested * contract_size * entry_price) / effective_leverage
        margin_ok = margin_required <= equity

        return {
            'suggested_volume': round(suggested, 4),
            'sl_pips': round(sl_pips, 2),
            'pip_size': pip_size,
            'pip_value_per_lot': round(pip_value, 4),
            'risk_amount': round(risk_amount, 2),
            'margin_required': round(margin_required, 2),
            'margin_ok': margin_ok,
            'asset_class': ac,
            'max_volume': max_vol,
            'min_volume': min_vol,
        }

    def validate_sl_tp_update(
        self,
        mode: str,
        side: str,
        current_price: float,
        new_stop_loss: float | None,
        new_take_profit: float | None,
        pair: str | None = None,
        asset_class: str | None = None,
    ) -> RiskAssessment:
        """Validate proposed SL/TP modification.

        Ensures the new levels are geometrically valid and within risk limits.
        """
        reasons: list[str] = []
        ac = self._resolve_asset_class(pair, asset_class)
        pip_size = self._pip_size(pair, current_price, asset_class)

        if new_stop_loss is not None and new_stop_loss > 0:
            sl_distance = abs(current_price - new_stop_loss)
            if current_price > 0 and sl_distance / current_price < 0.0005:
                reasons.append('Proposed stop loss is too tight.')
            # Verify SL is on correct side
            if side == 'BUY' and new_stop_loss >= current_price:
                reasons.append('Stop loss must be below entry for BUY.')
            elif side == 'SELL' and new_stop_loss <= current_price:
                reasons.append('Stop loss must be above entry for SELL.')

        if new_take_profit is not None and new_take_profit > 0:
            # Verify TP is on correct side
            if side == 'BUY' and new_take_profit <= current_price:
                reasons.append('Take profit must be above entry for BUY.')
            elif side == 'SELL' and new_take_profit >= current_price:
                reasons.append('Take profit must be below entry for SELL.')

        accepted = len(reasons) == 0
        if accepted:
            reasons.append('SL/TP update validated.')

        return RiskAssessment(
            accepted=accepted,
            reasons=reasons,
            suggested_volume=0.0,
            pip_size=pip_size,
            asset_class=ac,
        )

    def evaluate_portfolio(
        self,
        portfolio: PortfolioState,
        limits: RiskLimits,
        proposed_trade: ProposedTrade,
    ) -> RiskAssessment:
        """Evaluate a trade proposal against portfolio state and risk limits.

        Checks are sequential — rejects on first failure:
        1. Daily loss limit
        2. Risk budget (open risk + new trade risk)
        3. Max positions
        4. Max positions per symbol
        5. Free margin
        6. Trade-level checks (existing evaluate()) with real equity
        """
        ac = self._resolve_asset_class(proposed_trade.pair, proposed_trade.asset_class)

        if proposed_trade.decision == "HOLD":
            return RiskAssessment(
                accepted=True,
                reasons=["No trade requested (HOLD)."],
                suggested_volume=0.0,
                asset_class=ac,
            )

        # Check 1: Daily loss limit
        if portfolio.daily_drawdown_pct >= limits.max_daily_loss_pct:
            return RiskAssessment(
                accepted=False,
                reasons=[
                    f"REJECT: daily loss limit reached "
                    f"({portfolio.daily_drawdown_pct:.1f}% >= {limits.max_daily_loss_pct:.1f}%)"
                ],
                suggested_volume=0.0,
                asset_class=ac,
            )

        # Check 2: Risk budget
        trade_risk_pct = proposed_trade.risk_percent
        if portfolio.open_risk_total_pct + trade_risk_pct > limits.max_open_risk_pct:
            return RiskAssessment(
                accepted=False,
                reasons=[
                    f"REJECT: risk budget exceeded "
                    f"({portfolio.open_risk_total_pct:.1f}% + {trade_risk_pct:.1f}% "
                    f"> {limits.max_open_risk_pct:.1f}%)"
                ],
                suggested_volume=0.0,
                asset_class=ac,
            )

        # Check 3: Max positions
        if portfolio.open_position_count >= limits.max_positions:
            return RiskAssessment(
                accepted=False,
                reasons=[
                    f"REJECT: max positions reached "
                    f"({portfolio.open_position_count}/{limits.max_positions})"
                ],
                suggested_volume=0.0,
                asset_class=ac,
            )

        # Check 4: Max positions per symbol
        symbol = proposed_trade.pair or ""
        positions_on_symbol = sum(
            1 for p in portfolio.open_positions if p.symbol == symbol
        )
        if positions_on_symbol >= limits.max_positions_per_symbol:
            return RiskAssessment(
                accepted=False,
                reasons=[
                    f"REJECT: max positions on {symbol} reached "
                    f"({positions_on_symbol}/{limits.max_positions_per_symbol})"
                ],
                suggested_volume=0.0,
                asset_class=ac,
            )

        # Check 5: Free margin
        equity = portfolio.equity if portfolio.equity > 0 else 10000.0
        free_margin_pct = (portfolio.free_margin / equity) * 100 if equity > 0 else 0.0
        if free_margin_pct < limits.min_free_margin_pct:
            return RiskAssessment(
                accepted=False,
                reasons=[
                    f"REJECT: insufficient free margin "
                    f"({free_margin_pct:.1f}% < {limits.min_free_margin_pct:.1f}%)"
                ],
                suggested_volume=0.0,
                asset_class=ac,
            )

        # Check 7: Weekly loss limit (Tier 2)
        if portfolio.weekly_drawdown_pct >= limits.max_weekly_loss_pct:
            return RiskAssessment(
                accepted=False,
                reasons=[
                    f"REJECT: weekly loss limit reached "
                    f"({portfolio.weekly_drawdown_pct:.1f}% >= {limits.max_weekly_loss_pct:.1f}%)"
                ],
                suggested_volume=0.0,
                asset_class=ac,
            )

        # Check 8: Currency exposure (Tier 2)
        try:
            from app.services.risk.currency_exposure import compute_currency_exposure
            currency_report = compute_currency_exposure(portfolio.open_positions, equity)
            for ce in currency_report.exposures.values():
                if ce.exposure_pct >= limits.max_currency_exposure_pct:
                    return RiskAssessment(
                        accepted=False,
                        reasons=[
                            f"REJECT: {ce.currency} exposure {ce.exposure_pct:.1f}% "
                            f">= limit {limits.max_currency_exposure_pct:.1f}% "
                            f"(positions: {', '.join(ce.contributing_positions)})"
                        ],
                        suggested_volume=0.0,
                        asset_class=ac,
                    )

            # Check 9: Gross exposure (Tier 2)
            if currency_report.total_gross_exposure_pct >= limits.max_gross_exposure_pct:
                return RiskAssessment(
                    accepted=False,
                    reasons=[
                        f"REJECT: gross exposure {currency_report.total_gross_exposure_pct:.1f}% "
                        f">= limit {limits.max_gross_exposure_pct:.1f}%"
                    ],
                    suggested_volume=0.0,
                    asset_class=ac,
                )
        except Exception as exc:
            logger.warning("Currency exposure check failed (non-blocking): %s", exc)

        # Check 10: Correlation risk (Tier 2)
        try:
            from app.services.risk.correlation_exposure import compute_correlation_exposure
            corr_report = compute_correlation_exposure(
                portfolio.open_positions,
                portfolio.open_risk_total_pct,
                limits.max_correlation_risk_multiplier,
            )
            if corr_report.should_reduce:
                return RiskAssessment(
                    accepted=False,
                    reasons=[
                        f"REJECT: correlation risk multiplier {corr_report.effective_risk_multiplier:.1f}x "
                        f">= limit {limits.max_correlation_risk_multiplier:.1f}x "
                        f"(adjusted risk: {corr_report.adjusted_open_risk_pct:.1f}%)"
                    ],
                    suggested_volume=0.0,
                    asset_class=ac,
                )
        except Exception as exc:
            logger.warning("Correlation exposure check failed (non-blocking): %s", exc)

        # Check 6: Trade-level checks (existing logic) with REAL equity
        assessment = self.evaluate(
            mode=proposed_trade.mode,
            decision=proposed_trade.decision,
            risk_percent=proposed_trade.risk_percent,
            price=proposed_trade.entry_price,
            stop_loss=proposed_trade.stop_loss,
            pair=proposed_trade.pair,
            equity=equity,
            asset_class=proposed_trade.asset_class,
            leverage=portfolio.leverage,
        )

        # Volume adjustment: if near budget limit (>80%), reduce proportionally
        if assessment.accepted:
            budget_remaining = limits.max_open_risk_pct - portfolio.open_risk_total_pct
            budget_usage = trade_risk_pct / budget_remaining if budget_remaining > 0 else 1.0
            if budget_usage > 0.8:
                reduction = 1.0 - ((budget_usage - 0.8) / 0.2) * 0.5  # up to 50% reduction
                reduction = max(reduction, 0.5)
                assessment.suggested_volume = round(
                    assessment.suggested_volume * reduction, 4,
                )
                min_vol, _ = self._volume_limits(proposed_trade.pair, proposed_trade.asset_class)
                assessment.suggested_volume = max(assessment.suggested_volume, min_vol)
                assessment.reasons.append(
                    f"Volume reduced to {assessment.suggested_volume} "
                    f"(risk budget {budget_usage:.0%} utilized)"
                )

        return assessment
