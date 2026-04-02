"""Portfolio state aggregation — real-time account + positions from MetaAPI."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class OpenPosition:
    symbol: str
    side: str                # BUY | SELL
    volume: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_pct: float = 0.0   # Risk of this position in % of equity


@dataclass
class PortfolioState:
    # Account
    balance: float = 0.0
    equity: float = 0.0
    free_margin: float = 0.0
    used_margin: float = 0.0
    leverage: float = 100.0

    # Open positions
    open_positions: list[OpenPosition] = field(default_factory=list)
    open_position_count: int = 0
    open_risk_total_pct: float = 0.0

    # PnL & Drawdown
    daily_realized_pnl: float = 0.0
    daily_unrealized_pnl: float = 0.0
    daily_drawdown_pct: float = 0.0
    daily_high_equity: float = 0.0

    # Weekly (Tier 2)
    weekly_realized_pnl: float = 0.0
    weekly_drawdown_pct: float = 0.0
    weekly_high_equity: float = 0.0

    # Risk budget
    risk_budget_remaining_pct: float = 0.0
    trades_remaining_today: int = 0

    # Exposure
    exposure_by_symbol: dict[str, float] = field(default_factory=dict)

    # Meta
    degraded: bool = False
    degraded_reasons: list[str] = field(default_factory=list)
    fetched_at: str = ""


class PortfolioStateService:
    """Aggregates real-time account state from MetaAPI + daily snapshots from DB."""

    @staticmethod
    def _resolve_contract_size(symbol: str) -> float:
        """Resolve contract size for a symbol using InstrumentClassifier + RiskEngine specs."""
        try:
            from app.services.risk.rules import _CONTRACT_SPECS
            from app.services.market.instrument import InstrumentClassifier
            descriptor = InstrumentClassifier.classify(symbol)
            ac = descriptor.asset_class.value.lower()
            spec = _CONTRACT_SPECS.get(ac, _CONTRACT_SPECS.get("unknown", {}))
            return float(spec.get("contract_size", 100_000))
        except Exception:
            return 100_000  # safe default for forex

    @staticmethod
    def _estimate_position_risk(pos: OpenPosition, equity: float) -> float:
        """Estimate risk % of a single position based on SL distance or fallback.

        Uses the real contract size per asset class (forex=100k, crypto=1, metal=100, etc.)
        and pip value to compute the actual monetary risk.
        """
        if equity <= 0:
            return 0.0
        if pos.stop_loss and pos.stop_loss > 0 and pos.entry_price > 0:
            sl_distance = abs(pos.entry_price - pos.stop_loss)
            # Use real pip value from RiskEngine for accurate risk calculation
            try:
                from app.services.risk.rules import RiskEngine
                engine = RiskEngine()
                pip_size = engine._pip_size(pos.symbol, pos.entry_price)
                pip_value = engine._pip_value_per_lot(pos.symbol)
                sl_pips = sl_distance / pip_size if pip_size > 0 else 0
                risk_value = sl_pips * pip_value * pos.volume
            except Exception:
                # Fallback: use contract size directly
                contract_size = PortfolioStateService._resolve_contract_size(pos.symbol)
                risk_value = sl_distance * pos.volume * contract_size
            return min(round((risk_value / equity) * 100, 2), 100.0)
        # Fallback: use 2% per position as conservative estimate
        return 2.0

    @staticmethod
    async def get_current_state(
        account_id: str | None = None,
        region: str | None = None,
        db: Any = None,
    ) -> PortfolioState:
        """Fetch live portfolio state from MetaAPI, enriched with daily snapshot data."""
        from app.services.trading.metaapi_client import MetaApiClient

        state = PortfolioState(fetched_at=datetime.now(timezone.utc).isoformat())
        degraded_reasons: list[str] = []

        # 1. Account information
        try:
            client = MetaApiClient()
            acct_result = await client.get_account_information(
                account_id=account_id, region=region,
            )
            if acct_result.get("degraded"):
                degraded_reasons.append(
                    f"account_info degraded: {acct_result.get('reason', 'unknown')}"
                )
            else:
                info = acct_result.get("account_info", {})
                state.balance = float(info.get("balance", 0))
                state.equity = float(info.get("equity", state.balance))
                state.free_margin = float(info.get("freeMargin", 0))
                state.used_margin = float(info.get("margin", 0))
                state.leverage = float(info.get("leverage", 100))
        except Exception as exc:
            degraded_reasons.append(f"account_info failed: {exc}")
            logger.warning("PortfolioStateService: account_info failed: %s", exc)

        # 2. Open positions
        try:
            client = MetaApiClient()
            pos_result = await client.get_positions(
                account_id=account_id, region=region,
            )
            if pos_result.get("degraded"):
                degraded_reasons.append(
                    f"positions degraded: {pos_result.get('reason', 'unknown')}"
                )
            else:
                raw_positions = pos_result.get("positions", [])
                positions: list[OpenPosition] = []
                for p in raw_positions:
                    op = OpenPosition(
                        symbol=p.get("symbol", ""),
                        side="BUY" if p.get("type", "").upper() in ("POSITION_TYPE_BUY", "BUY") else "SELL",
                        volume=float(p.get("volume", 0)),
                        entry_price=float(p.get("openPrice", 0)),
                        current_price=float(p.get("currentPrice", 0)),
                        unrealized_pnl=float(p.get("profit", 0)),
                        stop_loss=float(p.get("stopLoss", 0)) or None,
                        take_profit=float(p.get("takeProfit", 0)) or None,
                    )
                    positions.append(op)
                state.open_positions = positions
                state.open_position_count = len(positions)
        except Exception as exc:
            degraded_reasons.append(f"positions failed: {exc}")
            logger.warning("PortfolioStateService: positions failed: %s", exc)

        # 3. Compute derived fields
        equity = state.equity if state.equity > 0 else state.balance
        if equity <= 0:
            equity = 10000.0  # Last-resort fallback
            degraded_reasons.append("equity_fallback_to_default_10000")

        # Open risk total
        total_risk = 0.0
        exposure_by_symbol: dict[str, float] = {}
        for pos in state.open_positions:
            pos.risk_pct = PortfolioStateService._estimate_position_risk(pos, equity)
            total_risk += pos.risk_pct
            exposure_by_symbol[pos.symbol] = (
                exposure_by_symbol.get(pos.symbol, 0) + pos.volume
            )
        state.open_risk_total_pct = round(total_risk, 2)
        state.exposure_by_symbol = exposure_by_symbol

        # Daily unrealized PnL
        state.daily_unrealized_pnl = sum(p.unrealized_pnl for p in state.open_positions)

        # 4. Daily snapshot data (daily_high_equity, daily_realized_pnl)
        if db is not None:
            try:
                from app.db.models.portfolio_snapshot import PortfolioSnapshot
                from sqlalchemy import func
                from datetime import date

                today_start = datetime.combine(
                    date.today(), datetime.min.time(), tzinfo=timezone.utc,
                )
                row = (
                    db.query(
                        func.max(PortfolioSnapshot.daily_high_equity).label("high"),
                        func.sum(PortfolioSnapshot.daily_realized_pnl).label("pnl"),
                    )
                    .filter(PortfolioSnapshot.timestamp >= today_start)
                    .first()
                )
                if row and row.high is not None:
                    state.daily_high_equity = float(row.high)
                if row and row.pnl is not None:
                    state.daily_realized_pnl = float(row.pnl)

                # Weekly snapshot data (Tier 2)
                from datetime import timedelta
                week_start = today_start - timedelta(days=7)
                week_row = (
                    db.query(
                        func.max(PortfolioSnapshot.daily_high_equity).label("high"),
                        func.sum(PortfolioSnapshot.daily_realized_pnl).label("pnl"),
                    )
                    .filter(PortfolioSnapshot.timestamp >= week_start)
                    .first()
                )
                if week_row and week_row.high is not None:
                    state.weekly_high_equity = float(week_row.high)
                if week_row and week_row.pnl is not None:
                    state.weekly_realized_pnl = float(week_row.pnl)
            except Exception as exc:
                degraded_reasons.append(f"snapshot_query failed: {exc}")
                logger.warning("PortfolioStateService: snapshot query failed: %s", exc)

        # Update daily_high_equity if current equity is higher
        if equity > state.daily_high_equity:
            state.daily_high_equity = equity

        # Daily drawdown
        if state.daily_high_equity > 0:
            state.daily_drawdown_pct = round(
                ((state.daily_high_equity - equity) / state.daily_high_equity) * 100, 2,
            )

        # Weekly drawdown (Tier 2)
        if equity > state.weekly_high_equity:
            state.weekly_high_equity = equity
        if state.weekly_high_equity > 0:
            state.weekly_drawdown_pct = round(
                ((state.weekly_high_equity - equity) / state.weekly_high_equity) * 100, 2,
            )

        # Finalize degraded state
        state.degraded = len(degraded_reasons) > 0
        state.degraded_reasons = degraded_reasons

        return state

    @staticmethod
    def build_defaults() -> PortfolioState:
        """Return a degraded PortfolioState with conservative defaults."""
        return PortfolioState(
            balance=10000.0,
            equity=10000.0,
            free_margin=10000.0,
            leverage=100.0,
            daily_high_equity=10000.0,
            degraded=True,
            degraded_reasons=["using_default_values"],
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
