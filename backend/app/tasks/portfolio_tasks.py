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


def _acquire_snapshot_lock() -> bool:
    """Acquire a Redis lock to prevent parallel snapshot executions."""
    try:
        import redis
        r = redis.from_url(settings.redis_url)
        # Lock for 55s (less than 60s schedule, prevents overlap)
        return bool(r.set("portfolio_snapshot_lock", "1", nx=True, ex=55))
    except Exception:
        return True  # If Redis is down, proceed anyway


@celery_app.task(
    name='app.tasks.portfolio_tasks.snapshot_portfolio',
    soft_time_limit=45,
    time_limit=60,
)
def snapshot_portfolio() -> None:
    """Capture a periodic portfolio snapshot."""
    if not _is_market_hours():
        logger.debug("portfolio_snapshot skipped: outside market hours")
        return

    if not _acquire_snapshot_lock():
        logger.debug("portfolio_snapshot skipped: another worker is already running it")
        return

    db = SessionLocal()
    try:
        import httpx
        from app.db.models.metaapi_account import MetaApiAccount
        from app.db.models.portfolio_snapshot import PortfolioSnapshot
        from app.services.connectors.runtime_settings import RuntimeConnectorSettings

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
        region = (account.region or 'new-york').strip().lower() or 'default'

        # Resolve MetaAPI token (runtime > env)
        token = RuntimeConnectorSettings.get_string('metaapi', ('METAAPI_TOKEN',), default='')
        if not token:
            token = settings.metaapi_token
        if not token:
            logger.debug("portfolio_snapshot skipped: no MetaAPI token")
            return

        # ── Redis cache: try cache first, REST fallback, then write back ──
        import json as _json
        import redis
        _r = None
        try:
            _r = redis.from_url(settings.redis_url)
        except Exception:
            pass

        _acct_cache_key = f"metaapi:account-info:{account_id}:{region}"
        _pos_cache_key = f"metaapi:positions:{account_id}:{region}"

        info = None
        positions = []

        # Try cache for account info
        if _r:
            try:
                _cached_acct = _r.get(_acct_cache_key)
                if _cached_acct:
                    _parsed = _json.loads(_cached_acct)
                    if isinstance(_parsed, dict) and not _parsed.get("degraded"):
                        info = _parsed.get("account_info", _parsed)
            except Exception:
                pass

        # Try cache for positions
        if _r:
            try:
                _cached_pos = _r.get(_pos_cache_key)
                if _cached_pos:
                    _parsed = _json.loads(_cached_pos)
                    if isinstance(_parsed, dict) and not _parsed.get("degraded"):
                        positions = _parsed.get("positions", [])
            except Exception:
                pass

        # If cache miss, fetch via REST (no SDK)
        if info is None:
            base_url = f"https://mt-client-api-v1.{account.region or 'new-york'}.agiliumtrade.ai"
            headers = {"auth-token": token}

            async def _fetch_rest():
                async with httpx.AsyncClient(timeout=10.0) as http:
                    acct_resp = await http.get(
                        f"{base_url}/users/current/accounts/{account_id}/account-information",
                        headers=headers,
                    )
                    pos_resp = await http.get(
                        f"{base_url}/users/current/accounts/{account_id}/positions",
                        headers=headers,
                    )
                    return acct_resp, pos_resp

            acct_resp, pos_resp = asyncio.run(_fetch_rest())

            if acct_resp.status_code != 200:
                logger.warning("portfolio_snapshot REST failed: account=%d positions=%d",
                               acct_resp.status_code, pos_resp.status_code)
                return

            info = acct_resp.json()
            raw_positions = pos_resp.json() if pos_resp.status_code == 200 else []
            if isinstance(raw_positions, dict):
                positions = raw_positions.get("positions", [])
            else:
                positions = raw_positions if isinstance(raw_positions, list) else []

            # Write back to cache for other consumers
            if _r:
                try:
                    _r.setex(_acct_cache_key, 5,
                             _json.dumps({"degraded": False, "account_info": info, "provider": "rest"}))
                    _r.setex(_pos_cache_key, 3,
                             _json.dumps({"degraded": False, "positions": positions, "provider": "rest"}))
                except Exception:
                    pass

        balance = float(info.get("balance", 0))
        equity = float(info.get("equity", balance))
        free_margin = float(info.get("freeMargin", 0))
        used_margin = float(info.get("margin", 0))
        position_count = len(positions)

        # Estimate open risk using pip-based calculation
        open_risk_total = 0.0
        if equity > 0:
            from app.services.risk.portfolio_state import OpenPosition, PortfolioStateService
            for p in positions:
                op = OpenPosition(
                    symbol=p.get("symbol", ""),
                    side="BUY" if "BUY" in str(p.get("type", "")).upper() else "SELL",
                    volume=float(p.get("volume", 0)),
                    entry_price=float(p.get("openPrice", 0)),
                    current_price=float(p.get("currentPrice", 0)),
                    unrealized_pnl=float(p.get("profit", 0)),
                    stop_loss=float(p.get("stopLoss", 0)) or None,
                )
                open_risk_total += PortfolioStateService._estimate_position_risk(op, equity)

        # Get daily high from existing snapshots
        from sqlalchemy import func
        today_start = datetime.combine(
            datetime.now(timezone.utc).date(), datetime.min.time(), tzinfo=timezone.utc,
        )
        high_row = db.query(func.max(PortfolioSnapshot.daily_high_equity)).filter(
            PortfolioSnapshot.timestamp >= today_start,
        ).scalar()
        daily_high = max(float(high_row or 0), equity)

        snapshot = PortfolioSnapshot(
            account_id=account_id,
            balance=balance,
            equity=equity,
            free_margin=free_margin,
            used_margin=used_margin,
            open_position_count=position_count,
            open_risk_total_pct=round(open_risk_total, 2),
            daily_realized_pnl=0.0,
            daily_high_equity=daily_high,
            snapshot_type="periodic",
        )
        db.add(snapshot)
        db.commit()

        logger.info(
            "portfolio_snapshot saved account=%s equity=%.2f positions=%d risk=%.1f%%",
            account_id, equity, position_count, open_risk_total,
        )
    except Exception:
        logger.warning("portfolio_snapshot failed", exc_info=True)
    finally:
        db.close()


@celery_app.task(
    name='app.tasks.portfolio_tasks.refresh_correlation_matrix',
    soft_time_limit=120,
    time_limit=180,
)
def refresh_correlation_matrix() -> None:
    """Compute and cache the correlation matrix for all tradeable symbols.

    Runs once daily after the main trading session closes.
    Fetches H4 close prices for the last 30 days, computes pairwise correlations,
    and stores the result in Redis with 24h TTL.
    """
    try:
        from app.core.config import get_settings
        from app.services.market.symbols import get_market_symbols_config
        from app.services.risk.correlation_matrix import (
            compute_correlation_matrix,
            save_to_redis,
        )

        settings = get_settings()
        symbols_config = get_market_symbols_config(settings)
        all_symbols = symbols_config.get("tradeable_pairs", [])

        if not all_symbols:
            logger.debug("refresh_correlation_matrix skipped: no symbols configured")
            return

        # Fetch close prices for each symbol
        close_prices: dict[str, list[float]] = {}
        for symbol in all_symbols:
            try:
                from app.services.market.data_provider import MarketProvider
                provider = MarketProvider()
                closes = provider.get_close_prices(symbol, timeframe="H4", bars=180)
                if closes and len(closes) >= 30:
                    close_prices[symbol] = closes
            except Exception as exc:
                logger.debug("Failed to fetch closes for %s: %s", symbol, exc)

        if len(close_prices) < 2:
            logger.info(
                "refresh_correlation_matrix: insufficient data (%d symbols)",
                len(close_prices),
            )
            return

        matrix = compute_correlation_matrix(close_prices, lookback_days=30)
        save_to_redis(matrix)

        logger.info(
            "refresh_correlation_matrix completed: %d symbols, %d valid",
            len(all_symbols), len(matrix.symbols),
        )
    except Exception:
        logger.warning("refresh_correlation_matrix failed", exc_info=True)
