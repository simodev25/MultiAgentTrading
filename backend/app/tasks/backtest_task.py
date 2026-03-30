import logging

from app.core.config import get_settings
from app.db.models.backtest_run import BacktestRun
from app.db.models.backtest_trade import BacktestTrade
from app.db.session import SessionLocal
from app.services.backtest.engine import BacktestEngine
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(
    name='app.tasks.backtest_task.execute',
    soft_time_limit=settings.celery_backtest_soft_time_limit_seconds,
    time_limit=settings.celery_backtest_time_limit_seconds,
)
def execute(run_id: int, llm_enabled: bool = False, agent_config: dict | None = None) -> None:
    db = SessionLocal()
    try:
        run = db.get(BacktestRun, run_id)
        if not run:
            return
        if run.status in {'completed', 'failed'}:
            return

        run.status = 'running'
        run.error = None
        db.commit()
        db.refresh(run)

        engine = BacktestEngine()
        normalized_strategy = engine.normalize_strategy(run.strategy)
        if not normalized_strategy:
            raise ValueError(f'Unsupported strategy {run.strategy}')

        result = engine.run(
            run.pair,
            run.timeframe,
            run.start_date.isoformat(),
            run.end_date.isoformat(),
            strategy=normalized_strategy,
            db=db,
            llm_enabled=llm_enabled,
            agent_config=agent_config,
        )

        run.status = 'completed'
        run.metrics = result.metrics
        run.equity_curve = result.equity_curve
        db.query(BacktestTrade).filter(BacktestTrade.run_id == run.id).delete()
        for trade in result.trades:
            db.add(
                BacktestTrade(
                    run_id=run.id,
                    side=trade['side'],
                    entry_time=trade['entry_time'],
                    exit_time=trade['exit_time'],
                    entry_price=trade['entry_price'],
                    exit_price=trade['exit_price'],
                    pnl_pct=trade['pnl_pct'],
                    outcome=trade['outcome'],
                )
            )
        db.commit()
    except Exception as exc:  # pragma: no cover - depends on external providers
        logger.exception('backtest task failed run_id=%s', run_id)
        db.rollback()
        run = db.get(BacktestRun, run_id)
        if run is not None:
            run.status = 'failed'
            run.error = str(exc)
            db.commit()
    finally:
        db.close()
