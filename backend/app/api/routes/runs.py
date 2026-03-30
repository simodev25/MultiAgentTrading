import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Role, get_current_user, require_roles
from app.db.models.metaapi_account import MetaApiAccount
from app.db.models.run import AnalysisRun
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.run import CreateRunRequest, RunDetailOut, RunOut
from app.services.agentscope.registry import AgentScopeRegistry
from app.services.market.symbols import canonical_symbol, get_market_symbols_config
from app.tasks.run_analysis_task import execute as run_analysis_task

router = APIRouter(prefix='/runs', tags=['runs'])
logger = logging.getLogger(__name__)


def _serialize_run(
    run: AnalysisRun,
    *,
    include_steps: bool = False,
    hydrate_runtime: bool = False,
) -> RunOut | RunDetailOut:
    trace = run.trace if isinstance(run.trace, dict) else {}

    payload = {
        'id': run.id,
        'pair': run.pair,
        'timeframe': run.timeframe,
        'mode': run.mode,
        'status': run.status,
        'progress': run.progress,
        'decision': run.decision if isinstance(run.decision, dict) else {},
        'trace': trace,
        'error': run.error,
        'created_by_id': run.created_by_id,
        'created_at': run.created_at,
        'started_at': run.started_at,
        'updated_at': run.updated_at,
    }
    if include_steps:
        payload['steps'] = list(run.steps)
        return RunDetailOut.model_validate(payload)
    return RunOut.model_validate(payload)


@router.get('', response_model=list[RunOut])
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[RunOut]:
    runs = db.query(AnalysisRun).order_by(AnalysisRun.created_at.desc()).limit(limit).all()
    return [_serialize_run(run) for run in runs]


@router.post('', response_model=RunOut)
async def create_run(
    payload: CreateRunRequest,
    async_execution: bool = Query(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST)),
) -> RunOut:
    settings = get_settings()
    pair = canonical_symbol(payload.pair)
    timeframe = payload.timeframe.upper()
    symbols_config = get_market_symbols_config(db, settings)
    preferred_pairs = {canonical_symbol(item) for item in symbols_config['tradeable_pairs']}

    if preferred_pairs and pair not in preferred_pairs:
        logger.info(
            'run_symbol_outside_preferred_universe pair=%s preferred_universe_size=%s',
            pair,
            len(preferred_pairs),
        )
    if timeframe not in settings.default_timeframes:
        raise HTTPException(status_code=400, detail=f'Unsupported timeframe {timeframe}')
    if payload.mode == 'live' and user.role not in {Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR}:
        raise HTTPException(status_code=403, detail='Live mode requires elevated trading role')
    if payload.metaapi_account_ref is not None:
        account = db.get(MetaApiAccount, payload.metaapi_account_ref)
        if not account or not account.enabled:
            raise HTTPException(status_code=400, detail='Invalid or disabled metaapi_account_ref')

    run = AnalysisRun(
        pair=pair,
        timeframe=timeframe,
        mode=payload.mode,
        status='pending',
        trace={
            'requested_metaapi_account_ref': payload.metaapi_account_ref,
            'runtime_engine': 'agentscope_v1',
        },
        created_by_id=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    if async_execution:
        try:
            run_analysis_task.apply_async(
                args=[run.id, payload.risk_percent, payload.metaapi_account_ref],
                queue=settings.celery_analysis_queue,
                ignore_result=True,
            )
            run.status = 'queued'
            db.commit()
            db.refresh(run)
            return _serialize_run(run, hydrate_runtime=True)
        except Exception:
            logger.warning('run enqueue failed; falling back to in-request execution run_id=%s', run.id, exc_info=True)

    run = await AgentScopeRegistry().execute(
        db,
        run,
        pair=run.pair,
        timeframe=run.timeframe,
        risk_percent=payload.risk_percent,
        metaapi_account_ref=payload.metaapi_account_ref,
    )
    return _serialize_run(run, hydrate_runtime=True)


@router.get('/{run_id}', response_model=RunDetailOut)
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> RunDetailOut:
    run = db.get(AnalysisRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail='Run not found')
    return _serialize_run(run, include_steps=True, hydrate_runtime=True)
