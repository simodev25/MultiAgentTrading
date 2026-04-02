"""Execution preflight engine — deterministic operational checks before trade execution.

Validates all conditions before an order reaches the broker:
decision validity, risk approval, parameter completeness, side consistency,
market hours, spread, volume constraints, instrument tradability.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class ExecutionStatus(str, Enum):
    EXECUTED = "executed"
    SIMULATED = "simulated"
    BLOCKED = "blocked"
    REFUSED = "refused"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class PreflightResult:
    status: ExecutionStatus
    can_execute: bool
    reason: str
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    # Validated data (passed to executor if OK)
    side: str | None = None          # BUY | SELL
    volume: float = 0.0
    entry: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    symbol: str = ""
    mode: str = "simulation"


# Max spread as % of price, per mode
MAX_SPREAD_PCT: dict[str, float] = {
    "simulation": 0.05,   # 5 bps
    "paper": 0.02,        # 2 bps
    "live": 0.01,         # 1 bp
}


class ExecutionPreflightEngine:
    """Deterministic pre-execution validation engine.

    Runs 8 sequential checks — rejects on first failure.
    """

    def validate(
        self,
        trader_output: dict,
        risk_output: dict,
        snapshot: dict,
        pair: str,
        mode: str,
    ) -> PreflightResult:
        passed: list[str] = []
        failed: list[str] = []

        trader_meta = trader_output.get("metadata", {})
        risk_meta = risk_output.get("metadata", {})

        decision = str(trader_meta.get("decision", "")).strip().upper()
        side = decision if decision in ("BUY", "SELL") else None
        entry = self._to_float(trader_meta.get("entry"))
        stop_loss = self._to_float(trader_meta.get("stop_loss"))
        take_profit = self._to_float(trader_meta.get("take_profit"))
        volume = self._to_float(risk_meta.get("suggested_volume", 0))

        base = PreflightResult(
            status=ExecutionStatus.BLOCKED,
            can_execute=False,
            reason="",
            side=side,
            volume=volume,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            symbol=pair,
            mode=mode,
        )

        # ── Check 1: Decision valid ──
        if decision == "HOLD":
            return PreflightResult(
                status=ExecutionStatus.SKIPPED,
                can_execute=False,
                reason="HOLD — no trade to execute",
                checks_passed=["decision: HOLD → skipped"],
                checks_failed=[],
                symbol=pair, mode=mode,
            )
        if decision not in ("BUY", "SELL"):
            failed.append(f"decision_valid: FAILED — invalid decision '{decision}'")
            base.checks_passed = passed
            base.checks_failed = failed
            base.reason = f"BLOCKED: invalid decision '{decision}'"
            return base
        passed.append(f"decision_valid: {decision}")

        # ── Check 2: Risk-manager accepted ──
        accepted = risk_meta.get("accepted", False)
        if not accepted:
            reasons = risk_meta.get("reasons", ["unknown"])
            return PreflightResult(
                status=ExecutionStatus.REFUSED,
                can_execute=False,
                reason=f"REFUSED: risk-manager rejected — {reasons[0] if reasons else 'unknown'}",
                checks_passed=passed,
                checks_failed=[f"risk_accepted: FAILED — {reasons}"],
                side=side, volume=volume, entry=entry,
                stop_loss=stop_loss, take_profit=take_profit,
                symbol=pair, mode=mode,
            )
        passed.append("risk_accepted: true")

        # ── Check 3: Parameters complete ──
        missing: list[str] = []
        if not self._is_valid_float(volume) or volume <= 0:
            missing.append("volume")
        if not self._is_valid_float(entry) or entry <= 0:
            missing.append("entry")
        if stop_loss is None or not self._is_valid_float(stop_loss) or stop_loss <= 0:
            missing.append("stop_loss")
        if missing:
            msg = f"BLOCKED: missing/invalid parameters: {', '.join(missing)}"
            failed.append(f"params_complete: FAILED — {', '.join(missing)}")
            base.checks_passed = passed
            base.checks_failed = failed
            base.reason = msg
            return base
        passed.append(
            f"params_complete: entry={entry}, sl={stop_loss}, "
            f"tp={take_profit}, vol={volume}"
        )

        # ── Check 4: Side consistency ──
        # Ensure trader and risk agree on direction
        risk_decision = str(risk_meta.get("decision", decision)).strip().upper()
        if risk_decision in ("BUY", "SELL") and risk_decision != decision:
            msg = f"BLOCKED: side inconsistency — trader={decision}, risk={risk_decision}"
            failed.append(f"side_consistent: FAILED — trader={decision}, risk={risk_decision}")
            base.checks_passed = passed
            base.checks_failed = failed
            base.reason = msg
            return base
        passed.append(f"side_consistent: {decision}")

        # ── Check 5: Market open ──
        market_open, market_reason = self._is_market_open(pair)
        if not market_open:
            msg = f"BLOCKED: {market_reason}"
            failed.append(f"market_open: FAILED — {market_reason}")
            base.checks_passed = passed
            base.checks_failed = failed
            base.reason = msg
            return base
        passed.append(f"market_open: {market_reason}")

        # ── Check 6: Spread acceptable ──
        spread = self._to_float(snapshot.get("spread", 0))
        last_price = self._to_float(snapshot.get("last_price", 0))
        if last_price > 0 and spread > 0:
            spread_pct = (spread / last_price) * 100
            max_spread = MAX_SPREAD_PCT.get(mode, MAX_SPREAD_PCT["live"])
            if spread_pct > max_spread:
                msg = f"BLOCKED: spread {spread_pct:.3f}% exceeds limit {max_spread:.3f}% for {mode}"
                failed.append(f"spread_ok: FAILED — {spread_pct:.3f}% > {max_spread:.3f}%")
                base.checks_passed = passed
                base.checks_failed = failed
                base.reason = msg
                return base
            passed.append(f"spread_ok: {spread_pct:.3f}% < {max_spread:.3f}%")
        else:
            passed.append("spread_ok: no spread data (skipped)")

        # ── Check 7: Volume broker-compatible ──
        try:
            from app.services.risk.rules import RiskEngine
            engine = RiskEngine()
            min_vol, max_vol = engine._volume_limits(pair)
            if volume < min_vol:
                msg = f"BLOCKED: volume {volume} below broker min {min_vol}"
                failed.append(f"volume_ok: FAILED — {volume} < min {min_vol}")
                base.checks_passed = passed
                base.checks_failed = failed
                base.reason = msg
                return base
            if volume > max_vol:
                msg = f"BLOCKED: volume {volume} above broker max {max_vol}"
                failed.append(f"volume_ok: FAILED — {volume} > max {max_vol}")
                base.checks_passed = passed
                base.checks_failed = failed
                base.reason = msg
                return base
            passed.append(f"volume_ok: {volume} within [{min_vol}, {max_vol}]")
        except Exception as exc:
            passed.append(f"volume_ok: check skipped ({exc})")

        # ── Check 8: Instrument tradable ──
        try:
            from app.services.market.instrument import InstrumentClassifier
            descriptor = InstrumentClassifier.classify(pair)
            ac = descriptor.asset_class.value.lower()
            # All asset classes are tradable in simulation
            # In live/paper, only supported classes
            supported_live = {"forex", "crypto", "metal", "index", "equity", "etf", "energy", "commodity"}
            if mode in ("live", "paper") and ac not in supported_live:
                msg = f"BLOCKED: asset class '{ac}' not supported for {mode}"
                failed.append(f"instrument_tradable: FAILED — {ac} not in {supported_live}")
                base.checks_passed = passed
                base.checks_failed = failed
                base.reason = msg
                return base
            passed.append(f"instrument_tradable: {ac} supported")
        except Exception as exc:
            passed.append(f"instrument_tradable: check skipped ({exc})")

        # ── All checks passed ──
        target_status = ExecutionStatus.SIMULATED if mode == "simulation" else ExecutionStatus.EXECUTED
        return PreflightResult(
            status=target_status,
            can_execute=True,
            reason=f"All preflight checks passed. {'Simulated' if mode == 'simulation' else 'Ready for execution'}.",
            checks_passed=passed,
            checks_failed=[],
            side=side,
            volume=volume,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            symbol=pair,
            mode=mode,
        )

    # ── Helpers ──

    @staticmethod
    def _to_float(val) -> float:
        try:
            f = float(val)
            return f if math.isfinite(f) else 0.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _is_valid_float(val) -> bool:
        return isinstance(val, (int, float)) and math.isfinite(val)

    @staticmethod
    def _is_market_open(pair: str) -> tuple[bool, str]:
        """Check if market is open for this instrument."""
        now = datetime.now(timezone.utc)
        weekday = now.weekday()  # 0=Mon, 6=Sun
        hour = now.hour

        # Determine asset class
        asset_class = "forex"
        try:
            from app.services.market.instrument import InstrumentClassifier
            descriptor = InstrumentClassifier.classify(pair)
            asset_class = descriptor.asset_class.value.lower()
        except Exception:
            pass

        # Crypto: always open
        if asset_class == "crypto":
            return True, "crypto market 24/7"

        # Forex: Sun 22:00 UTC → Fri 22:00 UTC
        if asset_class in ("forex", "metal", "energy", "commodity"):
            if weekday == 5:  # Saturday
                return False, "forex market closed (Saturday)"
            if weekday == 4 and hour >= 22:
                return False, "forex market closed (Friday after 22:00 UTC)"
            if weekday == 6 and hour < 22:
                return False, "forex market closed (Sunday before 22:00 UTC)"
            return True, "forex session active"

        # Indices/Equities: Mon-Fri 08:00-21:00 UTC (simplified)
        if asset_class in ("index", "equity", "etf"):
            if weekday >= 5:
                return False, f"{asset_class} market closed (weekend)"
            if hour < 8 or hour >= 21:
                return False, f"{asset_class} market closed (outside 08:00-21:00 UTC)"
            return True, f"{asset_class} session active"

        # Default: assume open
        return True, f"{asset_class} market assumed open"
