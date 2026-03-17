from __future__ import annotations

from datetime import datetime

from app.core.config import get_settings
from app.db.models.scheduled_run import ScheduledRun
from app.db.session import SessionLocal
from app.services.scheduler.cron import next_run_after
from app.services.scheduler.runner import create_and_enqueue_run, validate_schedule_target
from app.tasks.celery_app import celery_app

settings = get_settings()

@celery_app.task(
    name='app.tasks.scheduler_task.dispatch_due_schedules',
    soft_time_limit=settings.celery_scheduler_soft_time_limit_seconds,
    time_limit=settings.celery_scheduler_time_limit_seconds,
)
def dispatch_due_schedules() -> dict:
    if not settings.scheduler_enabled:
        return {'enabled': False, 'processed': 0, 'triggered': 0, 'failed': 0}

    db = SessionLocal()
    now = datetime.utcnow()
    processed = 0
    triggered = 0
    failed = 0

    try:
        due_rows = (
            db.query(ScheduledRun)
            .filter(
                ScheduledRun.is_active.is_(True),
                ScheduledRun.next_run_at.is_not(None),
                ScheduledRun.next_run_at <= now,
            )
            .order_by(ScheduledRun.next_run_at.asc(), ScheduledRun.id.asc())
            .limit(settings.scheduler_batch_size)
            .all()
        )

        for row in due_rows:
            processed += 1
            run_error: str | None = None
            try:
                normalized_pair, normalized_timeframe = validate_schedule_target(
                    db,
                    settings,
                    pair=row.pair,
                    timeframe=row.timeframe,
                    mode=row.mode,
                    metaapi_account_ref=row.metaapi_account_ref,
                )
                run = create_and_enqueue_run(
                    db,
                    pair=normalized_pair,
                    timeframe=normalized_timeframe,
                    mode=row.mode,
                    risk_percent=row.risk_percent,
                    metaapi_account_ref=row.metaapi_account_ref,
                    created_by_id=row.created_by_id,
                    trace_context={
                        'trigger': 'schedule-cron',
                        'schedule_id': row.id,
                        'schedule_name': row.name,
                    },
                )
                row.last_run_at = datetime.utcnow()
                if run.status == 'queued':
                    triggered += 1
                else:
                    failed += 1
                    run_error = run.error or 'Schedule run enqueue failed'
            except Exception as exc:  # pragma: no cover
                failed += 1
                run_error = str(exc)

            row.last_error = run_error
            try:
                row.next_run_at = next_run_after(row.cron_expression, datetime.utcnow())
            except Exception as exc:  # pragma: no cover
                row.is_active = False
                row.next_run_at = None
                row.last_error = f'Invalid cron expression disabled automatically: {exc}'

        db.commit()
        return {'enabled': True, 'processed': processed, 'triggered': triggered, 'failed': failed}
    finally:
        db.close()
