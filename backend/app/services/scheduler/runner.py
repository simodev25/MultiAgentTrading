from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.metaapi_account import MetaApiAccount
from app.db.models.run import AnalysisRun
from app.services.market.symbols import canonical_symbol, get_market_symbols_config

logger = logging.getLogger(__name__)

SUPPORTED_MODES = {'simulation', 'paper', 'live'}


def validate_schedule_target(
    db: Session,
    settings: Settings,
    *,
    pair: str,
    timeframe: str,
    mode: str,
    metaapi_account_ref: int | None,
) -> tuple[str, str]:
    normalized_pair = canonical_symbol(pair)
    normalized_timeframe = timeframe.upper()
    normalized_mode = str(mode or '').strip().lower()

    symbols_config = get_market_symbols_config(db, settings)
    supported_pairs = {canonical_symbol(item) for item in symbols_config['tradeable_pairs']}
    if normalized_pair not in supported_pairs:
        raise ValueError(f'Unsupported pair {normalized_pair} for V1 scope')
    if normalized_timeframe not in settings.default_timeframes:
        raise ValueError(f'Unsupported timeframe {normalized_timeframe} for V1 scope')
    if normalized_mode not in SUPPORTED_MODES:
        raise ValueError(f'Unsupported mode {mode}')

    if metaapi_account_ref is not None:
        account = db.get(MetaApiAccount, metaapi_account_ref)
        if not account or not account.enabled:
            raise ValueError('Invalid or disabled metaapi_account_ref')

    return normalized_pair, normalized_timeframe


def create_and_enqueue_run(
    db: Session,
    *,
    pair: str,
    timeframe: str,
    mode: str,
    risk_percent: float,
    metaapi_account_ref: int | None,
    created_by_id: int,
    trace_context: dict[str, Any] | None = None,
) -> AnalysisRun:
    from app.tasks.run_analysis_task import execute as run_analysis_task

    base_trace = {'requested_metaapi_account_ref': metaapi_account_ref}
    if trace_context:
        base_trace.update(trace_context)

    run = AnalysisRun(
        pair=pair,
        timeframe=timeframe,
        mode=mode,
        status='pending',
        trace=base_trace,
        created_by_id=created_by_id,
        created_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        run_analysis_task.apply_async(
            args=[run.id, float(risk_percent), metaapi_account_ref],
            queue='analysis',
            ignore_result=True,
        )
        run.status = 'queued'
        db.commit()
        db.refresh(run)
    except Exception as exc:  # pragma: no cover
        logger.exception('run enqueue failed run_id=%s', run.id)
        run.status = 'failed'
        run.error = f'enqueue failed: {exc}'
        db.commit()
        db.refresh(run)

    return run
