# Risk Manager Level 2 — Portfolio Risk Management (Tier 1 MVP)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the risk-manager from a single-trade validator to a portfolio-aware risk manager that uses real equity, tracks drawdown, enforces risk budgets, and limits position exposure.

**Architecture:** The portfolio state is fetched live from MetaAPI (account info + positions) and enriched with daily snapshot data from a new `portfolio_snapshots` DB table. A new `evaluate_portfolio()` method on `RiskEngine` runs 6 sequential checks (daily loss, risk budget, max positions, max per symbol, free margin, trade-level) before the existing position sizing logic. The MCP tool `portfolio_risk_evaluation` replaces the simple `risk_evaluation` while keeping backward compatibility.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, Celery, MetaAPI SDK, pytest

**Spec:** `docs/superpowers/specs/2026-04-01-risk-manager-portfolio-design.md`

---

## TASK 1: Portfolio State Service

**File to create:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/risk/portfolio_state.py`

### Step 1.1: Create dataclasses (OpenPosition, PortfolioState)

Create the file with two dataclasses. Follow the existing pattern from `rules.py` (which uses `from dataclasses import dataclass`).

```python
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

    # Risk budget
    risk_budget_remaining_pct: float = 0.0
    trades_remaining_today: int = 0

    # Exposure
    exposure_by_symbol: dict[str, float] = field(default_factory=dict)

    # Meta
    degraded: bool = False
    degraded_reasons: list[str] = field(default_factory=list)
    fetched_at: str = ""
```

### Step 1.2: Create PortfolioStateService class

Add the async service class with `get_current_state()` to the same file. This method:
1. Calls `MetaApiClient.get_account_information()` and `MetaApiClient.get_positions()`.
2. Queries the latest `portfolio_snapshots` row for `daily_high_equity` and `daily_realized_pnl`.
3. Computes derived fields (drawdown, open risk, exposure by symbol).
4. Handles degraded mode (MetaAPI down -> use defaults or last snapshot).

```python
class PortfolioStateService:
    """Aggregates real-time account state from MetaAPI + daily snapshots from DB."""

    @staticmethod
    def _estimate_position_risk(
        pos: OpenPosition, equity: float,
    ) -> float:
        """Estimate risk % of a single position based on SL distance or fallback."""
        if equity <= 0:
            return 0.0
        if pos.stop_loss and pos.stop_loss > 0 and pos.entry_price > 0:
            sl_distance = abs(pos.entry_price - pos.stop_loss)
            risk_value = sl_distance * pos.volume * 100_000  # simplified for forex
            return min((risk_value / equity) * 100, 100.0)
        # Fallback: use 2% per position as conservative estimate
        return 2.0

    @staticmethod
    async def get_current_state(
        account_id: str | None = None,
        region: str | None = None,
        db=None,
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
            except Exception as exc:
                degraded_reasons.append(f"snapshot_query failed: {exc}")
                logger.warning("PortfolioStateService: snapshot query failed: %s", exc)

        # Update daily_high_equity if current equity is higher
        if equity > state.daily_high_equity:
            state.daily_high_equity = equity

        # Daily drawdown
        if state.daily_high_equity > 0:
            state.daily_drawdown_pct = round(
                ((state.daily_high_equity - equity) / state.daily_high_equity) * 100, 2
            )

        # Finalize degraded state
        state.degraded = len(degraded_reasons) > 0
        state.degraded_reasons = degraded_reasons

        return state
```

### Step 1.3: Add a `_build_defaults()` class method for fallback

Add a static factory for when MetaAPI is completely unreachable -- returns a PortfolioState with safe defaults for degraded operation.

```python
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
```

**Commit message:** "Add PortfolioState dataclasses and PortfolioStateService with MetaAPI aggregation"

---

## TASK 3: Risk Limits Configuration

**File to create:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/risk/limits.py`

### Step 3.1: Create RiskLimits dataclass and RISK_LIMITS dict

This is a pure-data file. Follow the existing pattern in `rules.py` (dataclass imports, dict constants).

```python
"""Risk limits configuration per trading mode."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    max_risk_per_trade_pct: float     # Max risk per single trade
    max_daily_loss_pct: float         # Max daily loss before halt
    max_open_risk_pct: float          # Max total open risk
    max_positions: int                # Max simultaneous positions
    max_positions_per_symbol: int     # Max positions per symbol
    min_free_margin_pct: float        # Min free margin percentage


RISK_LIMITS: dict[str, RiskLimits] = {
    "simulation": RiskLimits(
        max_risk_per_trade_pct=5.0,
        max_daily_loss_pct=10.0,
        max_open_risk_pct=15.0,
        max_positions=10,
        max_positions_per_symbol=3,
        min_free_margin_pct=20.0,
    ),
    "paper": RiskLimits(
        max_risk_per_trade_pct=3.0,
        max_daily_loss_pct=6.0,
        max_open_risk_pct=10.0,
        max_positions=5,
        max_positions_per_symbol=2,
        min_free_margin_pct=30.0,
    ),
    "live": RiskLimits(
        max_risk_per_trade_pct=2.0,
        max_daily_loss_pct=3.0,
        max_open_risk_pct=6.0,
        max_positions=3,
        max_positions_per_symbol=1,
        min_free_margin_pct=50.0,
    ),
}


def get_risk_limits(mode: str) -> RiskLimits:
    """Return RiskLimits for the given mode, defaulting to live (safest)."""
    return RISK_LIMITS.get(mode, RISK_LIMITS["live"])
```

**Commit message:** "Add RiskLimits dataclass and per-mode configuration"

---

## TASK 2: Table portfolio_snapshots

### Step 2.1: Create SQLAlchemy model

**File to create:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/db/models/portfolio_snapshot.py`

Follow the exact pattern from `metaapi_account.py` and `strategy.py` (SQLAlchemy 2.0 `Mapped[T]`, import `Base` from `app.db.base`).

```python
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    account_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True,
    )
    balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    equity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    free_margin: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    used_margin: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    open_position_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    open_risk_total_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    daily_realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    daily_high_equity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    snapshot_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="periodic",
    )  # "pre_trade" | "post_trade" | "periodic"
```

### Step 2.2: Register model in `__init__.py`

**File to modify:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/db/models/__init__.py`

Add at line 14 (after the `Strategy` import):
```python
from app.db.models.portfolio_snapshot import PortfolioSnapshot
```

Add `'PortfolioSnapshot'` to the `__all__` list.

### Step 2.3: Create Alembic migration

**File to create:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/alembic/versions/0009_portfolio_snapshots.py`

Follow the exact pattern from `0008_strategy_monitoring.py`:

```python
"""Add portfolio_snapshots table

Revision ID: 0009_portfolio_snapshots
Revises: 0008_strategy_monitoring
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa

revision = '0009_portfolio_snapshots'
down_revision = '0008_strategy_monitoring'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'portfolio_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('account_id', sa.String(120), nullable=False, index=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False, index=True),
        sa.Column('balance', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('equity', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('free_margin', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('used_margin', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('open_position_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('open_risk_total_pct', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('daily_realized_pnl', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('daily_high_equity', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('snapshot_type', sa.String(20), nullable=False, server_default='periodic'),
    )


def downgrade() -> None:
    op.drop_table('portfolio_snapshots')
```

### Step 2.4: Run migration to verify

```bash
cd /Users/mbensass/projetPreso/MultiAgentTrading/backend && alembic upgrade head
```

**Commit message:** "Add portfolio_snapshots table with model and Alembic migration 0009"

---

## TASK 4: Extend RiskEngine with Portfolio Checks

**File to modify:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/risk/rules.py`

### Step 4.1: Add ProposedTrade dataclass

Add at the top of the file, after the `RiskAssessment` dataclass (around line 28):

```python
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
```

### Step 4.2: Add `evaluate_portfolio()` method to `RiskEngine`

Add this method after the existing `validate_sl_tp_update()` method (after line 416). This method implements 6 sequential checks that reject on first failure:

```python
    def evaluate_portfolio(
        self,
        portfolio: 'PortfolioState',
        limits: 'RiskLimits',
        proposed_trade: 'ProposedTrade',
    ) -> RiskAssessment:
        """Evaluate a trade proposal against portfolio state and risk limits.

        Checks are sequential — rejects on first failure:
        1. Daily loss limit
        2. Risk budget (open risk + new trade risk)
        3. Max positions
        4. Max positions per symbol
        5. Free margin
        6. Trade-level checks (existing evaluate())
        """
        from app.services.risk.portfolio_state import PortfolioState
        from app.services.risk.limits import RiskLimits

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
                    assessment.suggested_volume * reduction, 4
                )
                min_vol, _ = self._volume_limits(proposed_trade.pair, proposed_trade.asset_class)
                assessment.suggested_volume = max(assessment.suggested_volume, min_vol)
                assessment.reasons.append(
                    f"Volume reduced to {assessment.suggested_volume} "
                    f"(risk budget {budget_usage:.0%} utilized)"
                )

        return assessment
```

### Step 4.3: Add imports for forward references

At the top of `rules.py`, add TYPE_CHECKING import block (after line 7):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.risk.portfolio_state import PortfolioState
    from app.services.risk.limits import RiskLimits
```

**Commit message:** "Extend RiskEngine with evaluate_portfolio() -- 6 sequential portfolio checks"

---

## TASK 9: Unit Tests

**File to create:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/tests/unit/test_risk_engine_portfolio.py`

### Step 9.1: Write all 10 test cases

Follow the existing test style from `test_risk_engine.py` (direct instantiation, no fixtures, function-level tests):

```python
"""Unit tests for RiskEngine.evaluate_portfolio() — portfolio-level risk checks."""

from app.services.risk.limits import RiskLimits, get_risk_limits
from app.services.risk.portfolio_state import OpenPosition, PortfolioState
from app.services.risk.rules import ProposedTrade, RiskEngine


def _make_portfolio(**overrides) -> PortfolioState:
    """Helper: build a PortfolioState with sensible defaults."""
    defaults = dict(
        balance=10000.0,
        equity=10000.0,
        free_margin=8000.0,
        used_margin=2000.0,
        leverage=100.0,
        open_positions=[],
        open_position_count=0,
        open_risk_total_pct=0.0,
        daily_realized_pnl=0.0,
        daily_unrealized_pnl=0.0,
        daily_drawdown_pct=0.0,
        daily_high_equity=10000.0,
        risk_budget_remaining_pct=6.0,
        trades_remaining_today=3,
        exposure_by_symbol={},
        degraded=False,
        degraded_reasons=[],
        fetched_at="2026-04-01T12:00:00Z",
    )
    defaults.update(overrides)
    return PortfolioState(**defaults)


def _make_trade(**overrides) -> ProposedTrade:
    """Helper: build a ProposedTrade with sensible defaults."""
    defaults = dict(
        decision="BUY",
        pair="EURUSD.PRO",
        entry_price=1.1000,
        stop_loss=1.0950,
        risk_percent=1.0,
        mode="live",
        asset_class="forex",
    )
    defaults.update(overrides)
    return ProposedTrade(**defaults)


LIVE_LIMITS = get_risk_limits("live")


def test_reject_daily_loss_exceeded() -> None:
    """Drawdown 3.5% with limit 3% -> REJECT."""
    engine = RiskEngine()
    portfolio = _make_portfolio(daily_drawdown_pct=3.5)
    trade = _make_trade()
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is False
    assert "daily loss limit reached" in result.reasons[0]


def test_reject_risk_budget_exceeded() -> None:
    """Open risk 5% + new 2% > max 6% -> REJECT."""
    engine = RiskEngine()
    portfolio = _make_portfolio(open_risk_total_pct=5.0)
    trade = _make_trade(risk_percent=2.0)
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is False
    assert "risk budget exceeded" in result.reasons[0]


def test_reject_max_positions() -> None:
    """3 positions open, max 3 -> REJECT."""
    engine = RiskEngine()
    positions = [
        OpenPosition(symbol=f"PAIR{i}", side="BUY", volume=0.1,
                     entry_price=1.1, current_price=1.1, unrealized_pnl=0)
        for i in range(3)
    ]
    portfolio = _make_portfolio(
        open_positions=positions,
        open_position_count=3,
    )
    trade = _make_trade()
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is False
    assert "max positions reached" in result.reasons[0]


def test_reject_max_per_symbol() -> None:
    """1 position EURUSD.PRO, max 1 per symbol for live -> REJECT."""
    engine = RiskEngine()
    positions = [
        OpenPosition(symbol="EURUSD.PRO", side="BUY", volume=0.1,
                     entry_price=1.1, current_price=1.1, unrealized_pnl=0)
    ]
    portfolio = _make_portfolio(
        open_positions=positions,
        open_position_count=1,
    )
    trade = _make_trade(pair="EURUSD.PRO")
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is False
    assert "max positions on EURUSD.PRO reached" in result.reasons[0]


def test_reject_insufficient_margin() -> None:
    """Free margin 15% < min 50% for live -> REJECT."""
    engine = RiskEngine()
    portfolio = _make_portfolio(
        equity=10000.0,
        free_margin=1500.0,  # 15%
    )
    trade = _make_trade()
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is False
    assert "insufficient free margin" in result.reasons[0]


def test_accept_within_limits() -> None:
    """All checks pass -> ACCEPT with a valid volume."""
    engine = RiskEngine()
    portfolio = _make_portfolio(
        equity=10000.0,
        free_margin=8000.0,
        open_risk_total_pct=1.0,
        daily_drawdown_pct=0.5,
    )
    trade = _make_trade(risk_percent=1.0)
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is True
    assert result.suggested_volume > 0


def test_volume_reduction_near_limit() -> None:
    """Risk budget at >80% usage -> volume reduced."""
    engine = RiskEngine()
    # Budget is 6%, open risk is 5%, new trade is 0.9% -> usage = 0.9/1.0 = 90% > 80%
    portfolio = _make_portfolio(
        equity=10000.0,
        free_margin=8000.0,
        open_risk_total_pct=5.0,
        daily_drawdown_pct=0.5,
    )
    trade = _make_trade(risk_percent=0.9)
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is True
    # The volume should be reduced compared to a trade with plenty of budget
    portfolio_fresh = _make_portfolio(
        equity=10000.0,
        free_margin=8000.0,
        open_risk_total_pct=0.0,
        daily_drawdown_pct=0.0,
    )
    result_fresh = engine.evaluate_portfolio(portfolio_fresh, LIVE_LIMITS, trade)
    assert result.suggested_volume <= result_fresh.suggested_volume


def test_real_equity_used() -> None:
    """Verify that real equity (not hardcoded 10k) is used for sizing."""
    engine = RiskEngine()
    portfolio_small = _make_portfolio(equity=5000.0, free_margin=4000.0)
    portfolio_large = _make_portfolio(equity=50000.0, free_margin=40000.0)
    trade = _make_trade(risk_percent=1.0)
    result_small = engine.evaluate_portfolio(portfolio_small, LIVE_LIMITS, trade)
    result_large = engine.evaluate_portfolio(portfolio_large, LIVE_LIMITS, trade)
    assert result_small.accepted is True
    assert result_large.accepted is True
    # Larger equity -> larger position size
    assert result_large.suggested_volume > result_small.suggested_volume


def test_hold_bypasses_portfolio_checks() -> None:
    """HOLD decision -> no portfolio checks, immediate accept."""
    engine = RiskEngine()
    # Portfolio in terrible shape — should still accept HOLD
    portfolio = _make_portfolio(
        daily_drawdown_pct=99.0,
        open_risk_total_pct=99.0,
        open_position_count=999,
    )
    trade = _make_trade(decision="HOLD")
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    assert result.accepted is True
    assert result.suggested_volume == 0.0


def test_degraded_data_handling() -> None:
    """Degraded portfolio state is passed through without crashing."""
    engine = RiskEngine()
    portfolio = _make_portfolio(
        degraded=True,
        degraded_reasons=["metaapi_down"],
        equity=10000.0,
        free_margin=8000.0,
    )
    trade = _make_trade()
    result = engine.evaluate_portfolio(portfolio, LIVE_LIMITS, trade)
    # Should still evaluate (may accept or reject based on data)
    assert isinstance(result.accepted, bool)
    assert len(result.reasons) > 0
```

### Step 9.2: Run tests

```bash
cd /Users/mbensass/projetPreso/MultiAgentTrading/backend && python -m pytest tests/unit/test_risk_engine_portfolio.py -v
```

**Commit message:** "Add unit tests for portfolio-level risk evaluation (10 scenarios)"

---

## TASK 5: New MCP tool `portfolio_risk_evaluation`

**File to modify:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/mcp/trading_server.py`

### Step 5.1: Add the `portfolio_risk_evaluation` function

Add after the existing `risk_evaluation` function (after line 1332). This is NOT decorated with `@mcp.tool()` (matching the existing `risk_evaluation` pattern -- the toolkit wrapper in `toolkit.py` handles registration).

```python
def portfolio_risk_evaluation(
    trader_decision: dict | None = None,
    risk_percent: float = 1.0,
    mode: str = "simulation",
    account_id: str | None = None,
    region: str | None = None,
) -> dict:
    """Evaluate trade risk against live portfolio state and risk limits.

    Replaces the simple risk_evaluation with full portfolio awareness.
    Returns accepted, suggested_volume, reasons, and portfolio_summary.
    """
    import asyncio
    from app.services.risk.rules import RiskEngine, ProposedTrade
    from app.services.risk.portfolio_state import PortfolioStateService
    from app.services.risk.limits import get_risk_limits

    trader_decision = trader_decision or {}
    decision = trader_decision.get("decision", "HOLD")

    if decision == "HOLD":
        return {
            "accepted": False,
            "suggested_volume": 0.0,
            "reasons": ["HOLD decision"],
            "portfolio_summary": {},
            "degraded": False,
        }

    # Fetch portfolio state (async -> sync bridge for MCP tool)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                state = pool.submit(
                    asyncio.run,
                    PortfolioStateService.get_current_state(
                        account_id=account_id, region=region,
                    ),
                ).result(timeout=10)
        else:
            state = asyncio.run(
                PortfolioStateService.get_current_state(
                    account_id=account_id, region=region,
                )
            )
    except Exception as exc:
        logger.warning("portfolio_risk_evaluation: state fetch failed: %s", exc)
        state = PortfolioStateService.build_defaults()

    limits = get_risk_limits(mode)

    proposed = ProposedTrade(
        decision=decision,
        pair=trader_decision.get("pair"),
        entry_price=trader_decision.get("entry", 0.0),
        stop_loss=trader_decision.get("stop_loss"),
        take_profit=trader_decision.get("take_profit"),
        risk_percent=risk_percent,
        mode=mode,
        asset_class=trader_decision.get("asset_class"),
    )

    engine = RiskEngine()
    assessment = engine.evaluate_portfolio(state, limits, proposed)

    portfolio_summary = {
        "balance": state.balance,
        "equity": state.equity,
        "free_margin_pct": round(
            (state.free_margin / state.equity * 100) if state.equity > 0 else 0, 1
        ),
        "open_risk_pct": state.open_risk_total_pct,
        "daily_drawdown_pct": state.daily_drawdown_pct,
        "risk_budget_remaining_pct": round(
            limits.max_open_risk_pct - state.open_risk_total_pct, 1
        ),
        "open_positions": state.open_position_count,
        "max_positions": limits.max_positions,
    }

    return {
        "accepted": assessment.accepted,
        "suggested_volume": assessment.suggested_volume,
        "reasons": assessment.reasons,
        "portfolio_summary": portfolio_summary,
        "degraded": state.degraded,
        "degraded_reasons": state.degraded_reasons,
    }
```

### Step 5.2: Update old `risk_evaluation` to delegate

Modify the existing `risk_evaluation` function (line 1309) to delegate to `portfolio_risk_evaluation` when account info is not explicitly provided, preserving backward compatibility:

```python
def risk_evaluation(
    trader_decision: dict | None = None,
    risk_percent: float = 1.0,
    account_info: dict | None = None,
) -> dict:
    """Evaluate risk using RiskEngine.

    Delegates to portfolio_risk_evaluation for portfolio-aware checks.
    Falls back to single-trade evaluation if account_info is explicitly provided.
    """
    if account_info:
        # Legacy path: explicit account_info provided, use single-trade evaluation
        from app.services.risk.rules import RiskEngine
        trader_decision = trader_decision or {}
        decision = trader_decision.get("decision", "HOLD")
        if decision == "HOLD":
            return {"accepted": False, "suggested_volume": 0.0, "reasons": ["HOLD decision"]}
        engine = RiskEngine()
        assessment = engine.evaluate(
            mode=trader_decision.get("mode", "balanced"),
            decision=decision,
            risk_percent=risk_percent,
            price=trader_decision.get("entry", 0.0),
            stop_loss=trader_decision.get("stop_loss"),
            pair=trader_decision.get("pair"),
            equity=account_info.get("equity", 10000.0),
            asset_class=trader_decision.get("asset_class"),
        )
        return {"accepted": assessment.accepted, "suggested_volume": assessment.suggested_volume, "reasons": assessment.reasons}

    # New path: delegate to portfolio-aware evaluation
    mode = (trader_decision or {}).get("mode", "simulation")
    result = portfolio_risk_evaluation(
        trader_decision=trader_decision,
        risk_percent=risk_percent,
        mode=mode,
    )
    # Return only the fields that the old risk_evaluation returned, for backward compat
    return {
        "accepted": result["accepted"],
        "suggested_volume": result["suggested_volume"],
        "reasons": result["reasons"],
    }
```

**Commit message:** "Add portfolio_risk_evaluation MCP tool, delegate from existing risk_evaluation"

---

## TASK 6: Update risk-manager prompt

**File to modify:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/agentscope/prompts.py`

### Step 6.1: Update system and user prompts (lines 193-216)

Replace the risk-manager entry:

```python
    "risk-manager": {
        "system": (
            "You are a multi-asset risk manager and portfolio guardian. Your absolute priority is capital preservation.\n\n"
            "Rules:\n"
            "- You have access to real-time portfolio state via the portfolio_risk_evaluation tool.\n"
            "- Before approving any trade, verify:\n"
            "  1. Daily loss limit not breached\n"
            "  2. Risk budget remaining is sufficient\n"
            "  3. Position count within limits\n"
            "  4. No over-exposure on the same symbol\n"
            "  5. Sufficient free margin\n"
            "- Validate or reject based on provided parameters only. Never invent context.\n"
            "- Refuse if stop_loss, take_profit, entry, or volume are absent or incoherent.\n"
            "- Never reinterpret the trader's strategy — control risk compliance only.\n"
            "- In case of ambiguity, prefer REJECT.\n"
            "- No trade should be accepted if risk cannot be simply explained and quantitatively justified.\n"
            "- Your response must include portfolio context in the reasons.\n"
            "- Use portfolio_risk_evaluation and position_size_calculator tools to validate BUY/SELL.\n"
            "- STRICT: For HOLD decisions, immediately return the minimal response without calling any tool.\n"
        ),
        "user": (
            "Instrument: {pair}\nTimeframe: {timeframe}\nMode: {mode}\n\n"
            "Trader decision: {trader_decision}\n"
            "Entry: {entry}\nStop loss: {stop_loss}\nTake profit: {take_profit}\n"
            "Risk %: {risk_percent}\n\n"
            "Portfolio state is fetched automatically by the tool — do not assume equity is 10000.\n\n"
            "STRICT CONTRACT:\n"
            "- If trader_decision is HOLD: immediately generate_response with accepted=true, suggested_volume=0, reasons=[\"HOLD decision — no trade requested\"]. Do NOT call any tool. Do NOT add commentary.\n"
            "- If trader_decision is BUY or SELL: call portfolio_risk_evaluation tool, then generate_response with:\n"
            "  - accepted=true|false\n"
            "  - suggested_volume=<lots from tool result>\n"
            "  - reasons=<list from tool result, including portfolio context>\n"
        ),
    },
```

### Step 6.2: Update AGENT_TOOL_MAP in toolkit.py

**File to modify:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/agentscope/toolkit.py`

Change line 38:
```python
    "risk-manager": ["position_size_calculator", "portfolio_risk_evaluation"],
```

### Step 6.3: Update `_build_tool_kwargs` in registry.py

**File to modify:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/agentscope/registry.py`

Add a new branch for `portfolio_risk_evaluation` in `_build_tool_kwargs()` (after the `risk_evaluation` branch at line 1175):

```python
        if tool_id == "portfolio_risk_evaluation":
            td = (trader_out or {}).get("metadata", {})
            return {
                "trader_decision": td,
                "risk_percent": risk_percent,
                "mode": td.get("mode", "simulation"),
            }
```

**Commit message:** "Update risk-manager prompt and toolkit for portfolio_risk_evaluation"

---

## TASK 7: Inject PortfolioState into Pipeline

**File to modify:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/agentscope/registry.py`

### Step 7.1: Fetch portfolio state before Phase 4

In the `execute()` method, before Phase 4 starts (around line 1509, before `logger.info("Phase 4: Trader -> Risk -> Execution")`), add:

```python
            # ── Fetch portfolio state for Phase 4 ──
            portfolio_state = None
            try:
                from app.services.risk.portfolio_state import PortfolioStateService
                from app.services.trading.account_selector import MetaApiAccountSelector
                account = MetaApiAccountSelector().resolve(db, metaapi_account_ref)
                _acct_id = str(account.account_id) if account else None
                _region = (account.region if account else None)
                portfolio_state = await PortfolioStateService.get_current_state(
                    account_id=_acct_id, region=_region, db=db,
                )
            except Exception as exc:
                logger.warning("Failed to fetch portfolio state: %s", exc)
                from app.services.risk.portfolio_state import PortfolioStateService
                portfolio_state = PortfolioStateService.build_defaults()
```

### Step 7.2: Save pre_trade snapshot

After fetching portfolio state (still before Phase 4 loop), add:

```python
            # Save pre_trade snapshot
            if portfolio_state and not portfolio_state.degraded:
                try:
                    from app.db.models.portfolio_snapshot import PortfolioSnapshot
                    snapshot_row = PortfolioSnapshot(
                        account_id=_acct_id or "unknown",
                        balance=portfolio_state.balance,
                        equity=portfolio_state.equity,
                        free_margin=portfolio_state.free_margin,
                        used_margin=portfolio_state.used_margin,
                        open_position_count=portfolio_state.open_position_count,
                        open_risk_total_pct=portfolio_state.open_risk_total_pct,
                        daily_realized_pnl=portfolio_state.daily_realized_pnl,
                        daily_high_equity=portfolio_state.daily_high_equity,
                        snapshot_type="pre_trade",
                    )
                    db.add(snapshot_row)
                    db.flush()  # Don't commit yet — will batch with agent steps
                except Exception as exc:
                    logger.warning("Failed to save pre_trade snapshot: %s", exc)
```

### Step 7.3: Inject portfolio state into base_vars

After the pre_trade snapshot code:

```python
            # Inject portfolio context into base_vars for risk-manager prompt
            if portfolio_state:
                base_vars["portfolio_equity"] = str(portfolio_state.equity)
                base_vars["portfolio_balance"] = str(portfolio_state.balance)
                base_vars["portfolio_open_positions"] = str(portfolio_state.open_position_count)
                base_vars["portfolio_degraded"] = str(portfolio_state.degraded)
```

### Step 7.4: Save post_trade snapshot after execution manager

In the Phase 4 loop, after execution-manager completes (around line 1588, after `_risk_out = d`), add conditional post_trade snapshot:

```python
                # Save post_trade snapshot after execution
                if name == "execution-manager" and portfolio_state and _risk_out:
                    risk_meta = _risk_out.get("metadata", {})
                    if risk_meta.get("accepted") and not portfolio_state.degraded:
                        try:
                            from app.db.models.portfolio_snapshot import PortfolioSnapshot
                            post_snapshot = PortfolioSnapshot(
                                account_id=_acct_id or "unknown",
                                balance=portfolio_state.balance,
                                equity=portfolio_state.equity,
                                free_margin=portfolio_state.free_margin,
                                used_margin=portfolio_state.used_margin,
                                open_position_count=portfolio_state.open_position_count,
                                open_risk_total_pct=portfolio_state.open_risk_total_pct,
                                daily_realized_pnl=portfolio_state.daily_realized_pnl,
                                daily_high_equity=portfolio_state.daily_high_equity,
                                snapshot_type="post_trade",
                            )
                            db.add(post_snapshot)
                        except Exception as exc:
                            logger.warning("Failed to save post_trade snapshot: %s", exc)
```

**Commit message:** "Inject PortfolioState into pipeline with pre/post trade snapshots"

---

## TASK 8: Enrich Debug Traces

**File to modify:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/agentscope/registry.py`

### Step 8.1: Add portfolio_summary to run.decision

In the `execute()` method, where `run.decision` is built (around line 1634), add portfolio context:

```python
            # Add portfolio context to decision
            risk_meta = risk_out.get("metadata", {})
            portfolio_context = {}
            if portfolio_state:
                from app.services.risk.limits import get_risk_limits
                _mode = getattr(run, "mode", "simulation")
                _limits = get_risk_limits(_mode)
                portfolio_context = {
                    "balance": portfolio_state.balance,
                    "equity": portfolio_state.equity,
                    "free_margin_pct": round(
                        (portfolio_state.free_margin / portfolio_state.equity * 100)
                        if portfolio_state.equity > 0 else 0, 1
                    ),
                    "open_risk_pct": portfolio_state.open_risk_total_pct,
                    "daily_drawdown_pct": portfolio_state.daily_drawdown_pct,
                    "risk_budget_remaining_pct": round(
                        _limits.max_open_risk_pct - portfolio_state.open_risk_total_pct, 1
                    ),
                    "open_positions": portfolio_state.open_position_count,
                    "max_positions": _limits.max_positions,
                    "degraded": portfolio_state.degraded,
                    "degraded_reasons": portfolio_state.degraded_reasons,
                }
```

Then add `"portfolio": portfolio_context,` inside the `run.decision = { ... }` dict.

### Step 8.2: Add portfolio context to run.trace

In the trace dict (around line 1661), add:

```python
                "portfolio_state": portfolio_context,
```

**Commit message:** "Enrich debug traces and run.decision with portfolio context"

---

## TASK 10: Celery Periodic Snapshots

**File to create:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/tasks/portfolio_tasks.py`

### Step 10.1: Create the Celery task

Follow the exact pattern from `strategy_monitor_task.py`:

```python
"""Celery periodic task for portfolio snapshot capture.

Runs every 15 minutes during market hours:
1. Fetches account info + positions from MetaAPI
2. Saves a 'periodic' snapshot to DB
3. Updates daily_high_equity if new high
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.tasks.celery_app import celery_app

settings = get_settings()
logger = logging.getLogger(__name__)


def _is_market_hours() -> bool:
    """Check if current UTC time is within broad forex market hours (Sun 22:00 - Fri 22:00)."""
    now = datetime.now(timezone.utc)
    weekday = now.weekday()  # 0=Mon, 6=Sun
    hour = now.hour
    # Forex closes Friday 22:00 UTC, reopens Sunday 22:00 UTC
    if weekday == 5:  # Saturday
        return False
    if weekday == 4 and hour >= 22:  # Friday after 22:00
        return False
    if weekday == 6 and hour < 22:  # Sunday before 22:00
        return False
    return True


@celery_app.task(
    name='app.tasks.portfolio_tasks.snapshot_portfolio',
    soft_time_limit=30,
    time_limit=60,
)
def snapshot_portfolio() -> None:
    """Capture a periodic portfolio snapshot."""
    if not _is_market_hours():
        logger.debug("portfolio_snapshot skipped: outside market hours")
        return

    db = SessionLocal()
    try:
        from app.db.models.metaapi_account import MetaApiAccount
        from app.db.models.portfolio_snapshot import PortfolioSnapshot
        from app.services.risk.portfolio_state import PortfolioStateService

        # Find the default/active MetaAPI account
        account = (
            db.query(MetaApiAccount)
            .filter(MetaApiAccount.enabled.is_(True))
            .order_by(MetaApiAccount.is_default.desc(), MetaApiAccount.id.asc())
            .first()
        )
        if not account:
            logger.debug("portfolio_snapshot skipped: no MetaAPI account configured")
            return

        account_id = str(account.account_id)
        region = account.region

        # Fetch portfolio state
        state = asyncio.run(
            PortfolioStateService.get_current_state(
                account_id=account_id,
                region=region,
                db=db,
            )
        )

        if state.degraded:
            logger.warning(
                "portfolio_snapshot degraded: %s", state.degraded_reasons,
            )
            # Still save the snapshot even if degraded — records the fact
            # that we tried and what partial data we got

        snapshot = PortfolioSnapshot(
            account_id=account_id,
            balance=state.balance,
            equity=state.equity,
            free_margin=state.free_margin,
            used_margin=state.used_margin,
            open_position_count=state.open_position_count,
            open_risk_total_pct=state.open_risk_total_pct,
            daily_realized_pnl=state.daily_realized_pnl,
            daily_high_equity=state.daily_high_equity,
            snapshot_type="periodic",
        )
        db.add(snapshot)
        db.commit()

        logger.info(
            "portfolio_snapshot saved account=%s equity=%.2f positions=%d drawdown=%.2f%%",
            account_id, state.equity, state.open_position_count,
            state.daily_drawdown_pct,
        )
    except Exception:
        logger.warning("portfolio_snapshot failed", exc_info=True)
    finally:
        db.close()
```

### Step 10.2: Register in celery_app.py

**File to modify:** `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/tasks/celery_app.py`

1. Add to `include` list (line 19):
   ```python
   include=['app.tasks.run_analysis_task', 'app.tasks.backtest_task', 'app.tasks.strategy_backtest_task', 'app.tasks.strategy_monitor_task', 'app.tasks.portfolio_tasks'],
   ```

2. Add to `task_routes` (line 21-26):
   ```python
       'app.tasks.portfolio_tasks.*': {'queue': settings.celery_analysis_queue},
   ```

3. Add to `beat_schedule` (line 43-48):
   ```python
       'portfolio-snapshot': {
           'task': 'app.tasks.portfolio_tasks.snapshot_portfolio',
           'schedule': 900.0,  # 15 minutes
       },
   ```

4. Add the import at line 40:
   ```python
   import app.tasks.portfolio_tasks  # noqa: E402,F401
   ```

**Commit message:** "Add Celery periodic task for portfolio snapshots every 15 minutes"

---

## Summary of All Files Modified/Created

| Task | File | Action |
|------|------|--------|
| 1 | `backend/app/services/risk/portfolio_state.py` | CREATE |
| 2 | `backend/app/db/models/portfolio_snapshot.py` | CREATE |
| 2 | `backend/app/db/models/__init__.py` | MODIFY (add import) |
| 2 | `backend/alembic/versions/0009_portfolio_snapshots.py` | CREATE |
| 3 | `backend/app/services/risk/limits.py` | CREATE |
| 4 | `backend/app/services/risk/rules.py` | MODIFY (add ProposedTrade + evaluate_portfolio) |
| 5 | `backend/app/services/mcp/trading_server.py` | MODIFY (add portfolio_risk_evaluation, update risk_evaluation) |
| 6 | `backend/app/services/agentscope/prompts.py` | MODIFY (update risk-manager prompt) |
| 6 | `backend/app/services/agentscope/toolkit.py` | MODIFY (update AGENT_TOOL_MAP) |
| 6 | `backend/app/services/agentscope/registry.py` | MODIFY (add _build_tool_kwargs branch) |
| 7 | `backend/app/services/agentscope/registry.py` | MODIFY (inject portfolio state, snapshots) |
| 8 | `backend/app/services/agentscope/registry.py` | MODIFY (enrich traces) |
| 9 | `backend/tests/unit/test_risk_engine_portfolio.py` | CREATE |
| 10 | `backend/app/tasks/portfolio_tasks.py` | CREATE |
| 10 | `backend/app/tasks/celery_app.py` | MODIFY (register task + beat schedule) |

## Key Design Decisions

1. **Async/sync bridge in MCP tool:** The `portfolio_risk_evaluation` function uses `asyncio.run()` or a thread pool to call the async `PortfolioStateService.get_current_state()`, since MCP tool functions are synchronous in the `InProcessMCPClient` pattern (the client awaits synchronous handlers).

2. **Backward compatibility:** The old `risk_evaluation` function delegates to `portfolio_risk_evaluation` when no explicit `account_info` is passed, but preserves the legacy code path for callers that pass `account_info` directly.

3. **Degraded mode:** When MetaAPI is unreachable, the system falls back to `PortfolioStateService.build_defaults()` which uses conservative defaults (10k equity, no open positions). The `degraded` flag propagates through to the final output so the LLM and debug traces show that data quality was compromised.

4. **HOLD bypass preserved:** The existing optimization that skips LLM entirely for HOLD decisions (line 1537 in registry.py) is unchanged. The `evaluate_portfolio()` method also short-circuits on HOLD.

5. **Snapshot timing:** Pre-trade snapshots are saved via `db.flush()` (not commit) so they batch with the final agent step commit. Post-trade snapshots are also added to the session but committed in the final batch.

6. **Volume reduction near budget limit:** When the trade would consume more than 80% of the remaining risk budget, volume is reduced proportionally (up to 50% reduction), ensuring no cliff-edge behavior.

## Verification Checklist

After completing all 10 tasks, run:

```bash
cd /Users/mbensass/projetPreso/MultiAgentTrading/backend

# Run all risk engine tests
python -m pytest tests/unit/test_risk_engine.py tests/unit/test_risk_engine_portfolio.py tests/unit/test_risk_engine_multiproduct.py -v

# Run all unit tests to check for regressions
python -m pytest tests/unit/ -v

# Verify migration
alembic upgrade head
alembic downgrade -1
alembic upgrade head

# Verify imports
python -c "from app.services.risk.portfolio_state import PortfolioState, PortfolioStateService; print('OK')"
python -c "from app.services.risk.limits import RiskLimits, get_risk_limits; print('OK')"
python -c "from app.services.risk.rules import ProposedTrade; print('OK')"
python -c "from app.db.models.portfolio_snapshot import PortfolioSnapshot; print('OK')"
python -c "from app.services.mcp.trading_server import portfolio_risk_evaluation; print('OK')"
python -c "from app.tasks.portfolio_tasks import snapshot_portfolio; print('OK')"
```

### Critical Files for Implementation
- `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/risk/portfolio_state.py`
- `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/risk/rules.py`
- `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/mcp/trading_server.py`
- `/Users/mbensass/projetPreso/MultiAgentTrading/backend/app/services/agentscope/registry.py`
- `/Users/mbensass/projetPreso/MultiAgentTrading/backend/tests/unit/test_risk_engine_portfolio.py`