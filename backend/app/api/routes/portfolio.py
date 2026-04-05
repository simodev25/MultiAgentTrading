"""Portfolio dashboard API — real-time portfolio state, history, and stress test."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import Role, require_roles
from app.db.session import get_db

router = APIRouter(prefix='/portfolio', tags=['portfolio'])


@router.get('/state')
async def portfolio_state(
    account_ref: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.VIEWER)),
) -> dict:
    """Return current portfolio state with risk limits and currency exposure."""
    from app.services.risk.currency_exposure import (
        compute_currency_exposure,
        serialize_currency_exposure_report,
    )
    from app.services.risk.limits import get_risk_limits
    from app.services.risk.portfolio_state import PortfolioStateService
    from app.services.trading.account_selector import MetaApiAccountSelector

    account = MetaApiAccountSelector().resolve(db, account_ref)
    acct_id = str(account.account_id) if account else None
    region = account.region if account else None

    state = await PortfolioStateService.get_current_state(
        account_id=acct_id, region=region, db=db,
    )

    equity = state.equity if state.equity > 0 else 1.0
    limits = get_risk_limits("simulation")  # TODO: resolve from run mode

    # Currency exposure
    currency_exposure = {}
    try:
        report = compute_currency_exposure(state.open_positions, equity)
        currency_exposure = serialize_currency_exposure_report(report)
    except Exception:
        pass

    return {
        "state": {
            "balance": state.balance,
            "equity": state.equity,
            "free_margin": state.free_margin,
            "used_margin": state.used_margin,
            "leverage": state.leverage,
            "open_position_count": state.open_position_count,
            "open_risk_total_pct": state.open_risk_total_pct,
            "daily_realized_pnl": state.daily_realized_pnl,
            "daily_unrealized_pnl": state.daily_unrealized_pnl,
            "daily_drawdown_pct": state.daily_drawdown_pct,
            "weekly_drawdown_pct": state.weekly_drawdown_pct,
            "daily_high_equity": state.daily_high_equity,
            "weekly_high_equity": state.weekly_high_equity,
            "degraded": state.degraded,
            "degraded_reasons": state.degraded_reasons,
        },
        "limits": {
            "max_daily_loss_pct": limits.max_daily_loss_pct,
            "max_weekly_loss_pct": limits.max_weekly_loss_pct,
            "max_open_risk_pct": limits.max_open_risk_pct,
            "max_positions": limits.max_positions,
            "min_free_margin_pct": limits.min_free_margin_pct,
            "max_currency_notional_exposure_pct_warn": limits.max_currency_notional_exposure_pct_warn,
            "max_currency_notional_exposure_pct_block": limits.max_currency_notional_exposure_pct_block,
            "max_currency_open_risk_pct": limits.max_currency_open_risk_pct,
        },
        "currency_exposure": currency_exposure,
        "open_positions": [
            {
                "symbol": p.symbol,
                "side": p.side,
                "volume": p.volume,
                "entry_price": p.entry_price,
                "current_price": p.current_price,
                "pnl": p.unrealized_pnl,
                "risk_pct": p.risk_pct,
            }
            for p in state.open_positions
        ],
    }


@router.get('/history')
def portfolio_history(
    period: str = Query(default='7d'),
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.VIEWER)),
) -> dict:
    """Return portfolio snapshots for equity curve rendering."""
    from app.db.models.portfolio_snapshot import PortfolioSnapshot

    now = datetime.now(timezone.utc)
    period_map = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    delta = period_map.get(period, timedelta(days=7))
    since = now - delta

    rows = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.timestamp >= since)
        .order_by(PortfolioSnapshot.timestamp.asc())
        .all()
    )

    points = [
        {
            "timestamp": row.timestamp.isoformat(),
            "equity": row.equity,
            "balance": row.balance,
            "daily_high_equity": row.daily_high_equity,
            "drawdown_pct": round(
                ((row.daily_high_equity - row.equity) / row.daily_high_equity * 100)
                if row.daily_high_equity > 0 else 0.0,
                2,
            ),
        }
        for row in rows
    ]

    return {"period": period, "count": len(points), "points": points}


@router.get('/stress')
async def portfolio_stress(
    account_ref: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.VIEWER)),
) -> dict:
    """Run stress test on current portfolio positions."""
    from app.services.risk.portfolio_state import PortfolioStateService
    from app.services.risk.stress_test import run_stress_test
    from app.services.trading.account_selector import MetaApiAccountSelector

    account = MetaApiAccountSelector().resolve(db, account_ref)
    acct_id = str(account.account_id) if account else None
    region = account.region if account else None

    state = await PortfolioStateService.get_current_state(
        account_id=acct_id, region=region,
    )

    equity = state.equity if state.equity > 0 else 10000.0
    report = run_stress_test(
        positions=state.open_positions,
        equity=equity,
        used_margin=state.used_margin,
    )

    return {
        "worst_case_pnl_pct": report.worst_case_pnl_pct,
        "scenarios_surviving": report.scenarios_surviving,
        "scenarios_total": report.scenarios_total,
        "recommendation": report.recommendation,
        "results": [
            {
                "scenario": r.scenario,
                "description": r.description,
                "pnl": r.portfolio_pnl,
                "pnl_pct": r.portfolio_pnl_pct,
                "surviving": r.surviving,
                "margin_call": r.margin_call,
            }
            for r in report.results
        ],
    }
