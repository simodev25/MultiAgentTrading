"""Celery periodic task that monitors active strategies for new signals.

When a strategy has is_monitoring=True, this task:
1. Fetches latest market candles for the strategy's symbol/timeframe
2. Computes indicators based on the strategy template/params
3. Checks if a new signal (BUY/SELL) has appeared
4. If yes, creates a Run through the full agent workflow
"""
import asyncio
import logging

from app.core.config import get_settings
from app.db.models.run import AnalysisRun
from app.db.models.strategy import Strategy
from app.db.session import SessionLocal
from app.services.strategy.signal_engine import compute_strategy_overlays_and_signals
from app.tasks.celery_app import celery_app

settings = get_settings()
logger = logging.getLogger(__name__)


def _compute_latest_signal(candles: list[dict], template: str, params: dict) -> dict | None:
    """Compute the most recent signal from candle data. Returns {'time', 'price', 'side'} or None."""
    if not candles or len(candles) < 30:
        return None
    try:
        result = compute_strategy_overlays_and_signals(candles, template, params)
    except ValueError:
        logger.warning('strategy_monitor_unsupported_template template=%s', template)
        return None

    signals = result['signals']
    if signals:
        logger.info('strategy_monitor_signals_computed template=%s total=%d last=%s', template, len(signals), signals[-1]['side'])
    return signals[-1] if signals else None


@celery_app.task(
    name='app.tasks.strategy_monitor_task.check_all',
    soft_time_limit=120,
    time_limit=180,
)
def check_all() -> None:
    """Check all monitored strategies for new signals and create Runs when detected."""
    from app.tasks.run_analysis_task import execute as run_analysis_execute

    db = SessionLocal()
    try:
        monitored = db.query(Strategy).filter(Strategy.is_monitoring.is_(True)).all()
        if not monitored:
            return

        logger.info('strategy_monitor_check strategies=%d', len(monitored))

        for strategy in monitored:
            try:
                _check_strategy(db, strategy, run_analysis_execute)
            except Exception:
                logger.warning('strategy_monitor_error id=%s', strategy.strategy_id, exc_info=True)
    finally:
        db.close()


def _check_strategy(db, strategy: Strategy, run_analysis_execute) -> None:
    """Check a single strategy for new signals."""
    from app.services.trading.metaapi_client import MetaApiClient

    logger.info('strategy_monitor_checking id=%s symbol=%s tf=%s template=%s', strategy.strategy_id, strategy.symbol, strategy.timeframe, strategy.template)

    # Fetch candles
    client = MetaApiClient()
    try:
        async def _fetch():
            return await client.get_market_candles(
                pair=strategy.symbol,
                timeframe=strategy.timeframe,
                limit=200,
            )
        result_data = asyncio.run(_fetch())
        candles = result_data.get('candles', []) if isinstance(result_data, dict) else []
        logger.info('strategy_monitor_candles id=%s count=%d', strategy.strategy_id, len(candles))
    except Exception as exc:
        logger.warning('strategy_monitor_candle_fetch_failed id=%s: %s', strategy.strategy_id, str(exc)[:200], exc_info=True)
        return

    # Compute latest signal
    signal = _compute_latest_signal(candles, strategy.template, strategy.params or {})
    if not signal:
        logger.info('strategy_monitor_no_signal id=%s template=%s candles=%d', strategy.strategy_id, strategy.template, len(candles))
        return

    signal_key = f"{signal['time']}_{signal['side']}"

    # Check if this is a new signal (dedup)
    if signal_key == strategy.last_signal_key:
        logger.debug('strategy_monitor_same_signal id=%s key=%s', strategy.strategy_id, signal_key)
        return

    # New signal detected — create a Run
    logger.info(
        'strategy_monitor_new_signal id=%s signal=%s price=%.5f key=%s',
        strategy.strategy_id, signal['side'], signal['price'], signal_key,
    )

    # Update last_signal_key before creating run (to avoid double-trigger)
    strategy.last_signal_key = signal_key
    db.commit()

    # Create Run (same as POST /runs)
    run = AnalysisRun(
        pair=strategy.symbol,
        timeframe=strategy.timeframe,
        mode=strategy.monitoring_mode,
        status='pending',
        trace={
            'runtime_engine': 'agentscope_v1',
            'triggered_by': 'strategy_monitor',
            'strategy_id': strategy.strategy_id,
            'strategy_name': strategy.name,
            'strategy_template': strategy.template,
            'signal_side': signal['side'],
            'signal_price': signal['price'],
            'signal_time': signal['time'],
        },
        created_by_id=strategy.created_by_id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # Queue the agent workflow
    try:
        run_analysis_execute.apply_async(
            args=[run.id, strategy.monitoring_risk_percent, None],
            queue=settings.celery_analysis_queue,
            ignore_result=True,
        )
        run.status = 'queued'
        db.commit()
        logger.info('strategy_monitor_run_queued id=%s run_id=%d', strategy.strategy_id, run.id)
    except Exception:
        logger.warning('strategy_monitor_run_enqueue_failed id=%s', strategy.strategy_id, exc_info=True)
