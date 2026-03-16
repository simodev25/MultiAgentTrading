from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Role, require_roles
from app.db.models.scheduled_run import ScheduledRun
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.run import RunOut
from app.schemas.schedule import ScheduledRunCreate, ScheduledRunOut, ScheduledRunUpdate
from app.services.scheduler.cron import next_run_after, validate_cron_expression
from app.services.scheduler.runner import create_and_enqueue_run, validate_schedule_target

router = APIRouter(prefix='/schedules', tags=['schedules'])

LIVE_ROLES = {Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR}


def _ensure_live_permissions(mode: str, user: User) -> None:
    if mode == 'live' and user.role not in LIVE_ROLES:
        raise HTTPException(status_code=403, detail='Live mode requires elevated trading role')


@router.get('', response_model=list[ScheduledRunOut])
def list_schedules(
    limit: int = Query(default=100, ge=1, le=300),
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> list[ScheduledRunOut]:
    rows = db.query(ScheduledRun).order_by(ScheduledRun.created_at.desc()).limit(limit).all()
    return [ScheduledRunOut.model_validate(row) for row in rows]


@router.post('', response_model=ScheduledRunOut)
def create_schedule(
    payload: ScheduledRunCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST)),
) -> ScheduledRunOut:
    settings = get_settings()
    _ensure_live_permissions(payload.mode, user)

    try:
        normalized_cron = validate_cron_expression(payload.cron_expression)
        normalized_pair, normalized_timeframe = validate_schedule_target(
            db,
            settings,
            pair=payload.pair,
            timeframe=payload.timeframe,
            mode=payload.mode,
            metaapi_account_ref=payload.metaapi_account_ref,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    now = datetime.utcnow()
    next_run_at = next_run_after(normalized_cron, now) if payload.is_active else None
    row = ScheduledRun(
        name=payload.name.strip(),
        pair=normalized_pair,
        timeframe=normalized_timeframe,
        mode=payload.mode,
        risk_percent=float(payload.risk_percent),
        metaapi_account_ref=payload.metaapi_account_ref,
        cron_expression=normalized_cron,
        is_active=bool(payload.is_active),
        next_run_at=next_run_at,
        created_by_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ScheduledRunOut.model_validate(row)


@router.patch('/{schedule_id}', response_model=ScheduledRunOut)
def update_schedule(
    schedule_id: int,
    payload: ScheduledRunUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST)),
) -> ScheduledRunOut:
    row = db.get(ScheduledRun, schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail='Schedule not found')

    updates = payload.model_dump(exclude_unset=True)
    next_pair = updates.get('pair', row.pair)
    next_timeframe = updates.get('timeframe', row.timeframe)
    next_mode = updates.get('mode', row.mode)
    next_meta_ref = updates.get('metaapi_account_ref', row.metaapi_account_ref)
    next_cron = updates.get('cron_expression', row.cron_expression)
    next_active = updates.get('is_active', row.is_active)

    _ensure_live_permissions(next_mode, user)
    settings = get_settings()
    try:
        normalized_cron = validate_cron_expression(next_cron)
        normalized_pair, normalized_timeframe = validate_schedule_target(
            db,
            settings,
            pair=next_pair,
            timeframe=next_timeframe,
            mode=next_mode,
            metaapi_account_ref=next_meta_ref,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if 'name' in updates:
        row.name = updates['name'].strip()
    row.pair = normalized_pair
    row.timeframe = normalized_timeframe
    row.mode = next_mode
    if 'risk_percent' in updates:
        row.risk_percent = float(updates['risk_percent'])
    row.metaapi_account_ref = next_meta_ref
    row.cron_expression = normalized_cron
    row.is_active = bool(next_active)

    if row.is_active:
        row.next_run_at = next_run_after(row.cron_expression, datetime.utcnow())
    else:
        row.next_run_at = None

    db.commit()
    db.refresh(row)
    return ScheduledRunOut.model_validate(row)


@router.delete('/{schedule_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST)),
) -> Response:
    row = db.get(ScheduledRun, schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail='Schedule not found')
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post('/{schedule_id}/run-now', response_model=RunOut)
def run_schedule_now(
    schedule_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST)),
) -> RunOut:
    row = db.get(ScheduledRun, schedule_id)
    if not row:
        raise HTTPException(status_code=404, detail='Schedule not found')

    _ensure_live_permissions(row.mode, user)
    settings = get_settings()
    try:
        normalized_pair, normalized_timeframe = validate_schedule_target(
            db,
            settings,
            pair=row.pair,
            timeframe=row.timeframe,
            mode=row.mode,
            metaapi_account_ref=row.metaapi_account_ref,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run = create_and_enqueue_run(
        db,
        pair=normalized_pair,
        timeframe=normalized_timeframe,
        mode=row.mode,
        risk_percent=row.risk_percent,
        metaapi_account_ref=row.metaapi_account_ref,
        created_by_id=row.created_by_id,
        trace_context={
            'trigger': 'schedule-manual',
            'schedule_id': row.id,
            'schedule_name': row.name,
        },
    )

    row.last_run_at = datetime.utcnow()
    row.last_error = run.error if run.status == 'failed' else None
    if row.is_active:
        row.next_run_at = next_run_after(row.cron_expression, datetime.utcnow())
    db.commit()

    return RunOut.model_validate(run)
