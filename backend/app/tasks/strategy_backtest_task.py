import logging
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.db.models.strategy import Strategy
from app.db.session import SessionLocal
from app.services.backtest.engine import BacktestEngine
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(
    name='app.tasks.strategy_backtest_task.execute',
    soft_time_limit=settings.celery_backtest_soft_time_limit_seconds,
    time_limit=settings.celery_backtest_time_limit_seconds,
)
def execute(strategy_db_id: int) -> None:
    db = SessionLocal()
    try:
        strategy = db.get(Strategy, strategy_db_id)
        if not strategy or strategy.status != 'BACKTESTING':
            return

        engine = BacktestEngine()
        # Backtest on default pair/timeframe for last 30 days
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        start_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')

        try:
            result = engine.run(
                'EURUSD.PRO', 'H1',
                start_date, end_date,
                strategy=strategy.template,
                db=db,
                run_id=None,
            )

            metrics = result.metrics
            win_rate = metrics.get('win_rate_pct', 0)
            profit_factor = metrics.get('profit_factor', 0) or 0
            max_dd = abs(metrics.get('max_drawdown_pct', 0))
            total_return = metrics.get('total_return_pct', 0)

            # Score: weighted combination
            score = min(100, max(0,
                win_rate * 0.3 +
                min(profit_factor * 20, 40) +
                max(0, 30 - max_dd * 3)
            ))

            strategy.score = round(score, 1)
            strategy.metrics = {
                'win_rate': round(win_rate, 1),
                'profit_factor': round(profit_factor, 2),
                'max_drawdown': round(max_dd, 2),
                'total_return': round(total_return, 2),
                'trades': metrics.get('total_trades', 0),
            }
            strategy.status = 'VALIDATED' if score >= 50 else 'REJECTED'
            db.commit()
            logger.info('strategy_validated id=%s score=%.1f status=%s', strategy.strategy_id, score, strategy.status)
        except Exception as exc:
            strategy.status = 'REJECTED'
            strategy.score = 0
            strategy.metrics = {'error': str(exc)}
            db.commit()
            logger.exception('strategy_backtest_failed id=%s', strategy.strategy_id)
    finally:
        db.close()
