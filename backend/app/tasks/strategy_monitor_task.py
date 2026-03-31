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
from app.tasks.celery_app import celery_app

settings = get_settings()
logger = logging.getLogger(__name__)


def _compute_latest_signal(candles: list[dict], template: str, params: dict) -> dict | None:
    """Compute the most recent signal from candle data. Returns {'time', 'price', 'side'} or None."""
    if not candles or len(candles) < 30:
        return None

    import numpy as np
    import pandas as pd
    from ta.momentum import RSIIndicator
    from ta.trend import MACD
    from ta.volatility import BollingerBands

    df = pd.DataFrame(candles)
    close = df['close'].astype(float)
    times = df['time'].tolist()

    signals: list[dict] = []

    if template == 'ema_crossover':
        fast_period = params.get('ema_fast', 9)
        slow_period = params.get('ema_slow', 21)
        rsi_filter = params.get('rsi_filter', 30)
        ema_fast = close.ewm(span=fast_period, adjust=False).mean()
        ema_slow = close.ewm(span=slow_period, adjust=False).mean()
        rsi = RSIIndicator(close=close, window=14).rsi()
        for i in range(1, len(df)):
            if pd.isna(ema_fast.iloc[i]) or pd.isna(rsi.iloc[i]):
                continue
            if ema_fast.iloc[i] > ema_slow.iloc[i] and ema_fast.iloc[i - 1] <= ema_slow.iloc[i - 1] and rsi.iloc[i] < (100 - rsi_filter):
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif ema_fast.iloc[i] < ema_slow.iloc[i] and ema_fast.iloc[i - 1] >= ema_slow.iloc[i - 1] and rsi.iloc[i] > rsi_filter:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'rsi_mean_reversion':
        rsi_period = params.get('rsi_period', 14)
        oversold = params.get('oversold', 30)
        overbought = params.get('overbought', 70)
        rsi = RSIIndicator(close=close, window=rsi_period).rsi()
        for i in range(1, len(df)):
            if pd.isna(rsi.iloc[i]):
                continue
            if rsi.iloc[i] < oversold and rsi.iloc[i - 1] >= oversold:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif rsi.iloc[i] > overbought and rsi.iloc[i - 1] <= overbought:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'bollinger_breakout':
        bb_period = params.get('bb_period', 20)
        bb_std = params.get('bb_std', 2.0)
        bb = BollingerBands(close=close, window=bb_period, window_dev=bb_std)
        upper = bb.bollinger_hband()
        lower = bb.bollinger_lband()
        for i in range(1, len(df)):
            if pd.isna(lower.iloc[i]):
                continue
            if close.iloc[i] <= lower.iloc[i] and close.iloc[i - 1] > lower.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif close.iloc[i] >= upper.iloc[i] and close.iloc[i - 1] < upper.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

    elif template == 'macd_divergence':
        fast = params.get('fast', 12)
        slow = params.get('slow', 26)
        signal_period = params.get('signal', 9)
        macd_ind = MACD(close=close, window_fast=fast, window_slow=slow, window_sign=signal_period)
        macd_line = macd_ind.macd()
        signal_line = macd_ind.macd_signal()
        for i in range(1, len(df)):
            if pd.isna(macd_line.iloc[i]) or pd.isna(signal_line.iloc[i]):
                continue
            if macd_line.iloc[i] > signal_line.iloc[i] and macd_line.iloc[i - 1] <= signal_line.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'BUY'})
            elif macd_line.iloc[i] < signal_line.iloc[i] and macd_line.iloc[i - 1] >= signal_line.iloc[i - 1]:
                signals.append({'time': times[i], 'price': float(close.iloc[i]), 'side': 'SELL'})

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
