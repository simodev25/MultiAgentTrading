from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.security import Role, require_roles
from app.db.models.execution_order import ExecutionOrder
from app.db.models.metaapi_account import MetaApiAccount
from app.db.session import get_db
from app.schemas.metaapi_account import MetaApiAccountCreate, MetaApiAccountOut, MetaApiAccountUpdate
from app.schemas.order_guardian import (
    OrderGuardianEvaluateRequest,
    OrderGuardianEvaluationOut,
    OrderGuardianStatusOut,
    OrderGuardianStatusUpdate,
)
from app.schemas.order import ExecutionOrderOut
from app.services.trading.metaapi_client import MetaApiClient
from app.services.trading.order_guardian import OrderGuardianService

router = APIRouter(prefix='/trading', tags=['trading'])
metaapi_client = MetaApiClient()
order_guardian_service = OrderGuardianService()


@router.get('/orders', response_model=list[ExecutionOrderOut])
def list_orders(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> list[ExecutionOrderOut]:
    orders = (
        db.query(ExecutionOrder)
        .options(joinedload(ExecutionOrder.run))
        .order_by(ExecutionOrder.created_at.desc())
        .limit(limit)
        .all()
    )
    return [ExecutionOrderOut.model_validate(order) for order in orders]


@router.get('/market-candles')
async def market_candles(
    account_ref: int | None = Query(default=None),
    pair: str = Query(min_length=3, max_length=20),
    timeframe: str = Query(default='H1', min_length=2, max_length=5),
    limit: int = Query(default=240, ge=20, le=2000),
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> dict:
    account = _get_account_or_none(db, account_ref)
    account_id = account.account_id if account else None
    region = account.region if account else None
    normalized_pair = pair.strip()
    normalized_timeframe = timeframe.strip().upper()
    result = await metaapi_client.get_market_candles(
        account_id=account_id,
        region=region,
        pair=normalized_pair,
        timeframe=normalized_timeframe,
        limit=limit,
    )
    if result.get('degraded'):
        return {
            'pair': normalized_pair,
            'timeframe': normalized_timeframe,
            'symbol': result.get('symbol'),
            'requested_symbol': result.get('requested_symbol'),
            'tried_symbols': result.get('tried_symbols', []),
            'provider': result.get('provider', 'sdk'),
            'candles': result.get('candles', []),
            'reason': result.get('reason', 'No market data available for selected symbol/timeframe'),
        }
    return {
        'pair': normalized_pair,
        'timeframe': normalized_timeframe,
        'symbol': result.get('symbol'),
        'requested_symbol': result.get('requested_symbol'),
        'tried_symbols': result.get('tried_symbols', []),
        'provider': result.get('provider', 'sdk'),
        'candles': result.get('candles', []),
    }


@router.get('/accounts', response_model=list[MetaApiAccountOut])
def list_accounts(
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> list[MetaApiAccountOut]:
    accounts = db.query(MetaApiAccount).order_by(MetaApiAccount.is_default.desc(), MetaApiAccount.created_at.asc()).all()
    return [MetaApiAccountOut.model_validate(account) for account in accounts]


@router.post('/accounts', response_model=MetaApiAccountOut)
def create_account(
    payload: MetaApiAccountCreate,
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)),
) -> MetaApiAccountOut:
    existing = db.query(MetaApiAccount).filter(MetaApiAccount.account_id == payload.account_id).first()
    if existing:
        raise HTTPException(status_code=400, detail='MetaApi account_id already exists')

    account = MetaApiAccount(
        label=payload.label,
        account_id=payload.account_id,
        region=payload.region,
        enabled=payload.enabled,
        is_default=payload.is_default,
    )
    db.add(account)
    db.flush()

    if payload.is_default:
        db.query(MetaApiAccount).filter(MetaApiAccount.id != account.id).update({'is_default': False})

    db.commit()
    db.refresh(account)
    return MetaApiAccountOut.model_validate(account)


@router.patch('/accounts/{account_ref}', response_model=MetaApiAccountOut)
def update_account(
    account_ref: int,
    payload: MetaApiAccountUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)),
) -> MetaApiAccountOut:
    account = db.get(MetaApiAccount, account_ref)
    if not account:
        raise HTTPException(status_code=404, detail='MetaApi account not found')

    updates = payload.model_dump(exclude_none=True)
    for key, value in updates.items():
        setattr(account, key, value)

    if payload.is_default:
        db.query(MetaApiAccount).filter(MetaApiAccount.id != account.id).update({'is_default': False})

    db.commit()
    db.refresh(account)
    return MetaApiAccountOut.model_validate(account)


def _get_account_or_none(db: Session, account_ref: int | None) -> MetaApiAccount | None:
    if account_ref is None:
        return None
    account = db.get(MetaApiAccount, account_ref)
    if not account:
        raise HTTPException(status_code=404, detail='MetaApi account not found')
    return account


@router.get('/order-guardian', response_model=OrderGuardianStatusOut)
def order_guardian_status(
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> OrderGuardianStatusOut:
    return OrderGuardianStatusOut.model_validate(order_guardian_service.get_status(db))


@router.patch('/order-guardian', response_model=OrderGuardianStatusOut)
def update_order_guardian_status(
    payload: OrderGuardianStatusUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR)),
) -> OrderGuardianStatusOut:
    return OrderGuardianStatusOut.model_validate(order_guardian_service.update_status(db, payload))


@router.post('/order-guardian/evaluate', response_model=OrderGuardianEvaluationOut)
async def evaluate_order_guardian(
    payload: OrderGuardianEvaluateRequest,
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR)),
) -> OrderGuardianEvaluationOut:
    result = await order_guardian_service.evaluate(
        db,
        account_ref=payload.account_ref,
        dry_run=payload.dry_run,
    )
    return OrderGuardianEvaluationOut.model_validate(result)


@router.get('/account')
async def account_info(
    account_ref: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR)),
) -> dict:
    account = _get_account_or_none(db, account_ref)
    account_id = account.account_id if account else None
    region = account.region if account else None
    return await metaapi_client.get_account_information(
        account_id=account_id,
        region=region,
    )


@router.get('/positions')
async def positions(
    account_ref: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> dict:
    account = _get_account_or_none(db, account_ref)
    account_id = account.account_id if account else None
    region = account.region if account else None
    return await metaapi_client.get_positions(
        account_id=account_id,
        region=region,
    )


@router.get('/open-orders')
async def open_orders(
    account_ref: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> dict:
    account = _get_account_or_none(db, account_ref)
    account_id = account.account_id if account else None
    region = account.region if account else None
    return await metaapi_client.get_open_orders(
        account_id=account_id,
        region=region,
    )


@router.get('/deals')
async def deals(
    account_ref: int | None = Query(default=None),
    days: int = Query(default=30, ge=0, le=365),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> dict:
    settings = get_settings()
    if not settings.enable_metaapi_real_trades_dashboard:
        raise HTTPException(
            status_code=403,
            detail='MetaApi real trades dashboard disabled (ENABLE_METAAPI_REAL_TRADES_DASHBOARD=false)',
        )
    account = _get_account_or_none(db, account_ref)
    account_id = account.account_id if account else None
    region = account.region if account else None
    return await metaapi_client.get_deals(
        account_id=account_id,
        region=region,
        days=days,
        limit=limit,
        offset=offset,
    )


@router.get('/history-orders')
async def history_orders(
    account_ref: int | None = Query(default=None),
    days: int = Query(default=30, ge=0, le=365),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> dict:
    settings = get_settings()
    if not settings.enable_metaapi_real_trades_dashboard:
        raise HTTPException(
            status_code=403,
            detail='MetaApi real trades dashboard disabled (ENABLE_METAAPI_REAL_TRADES_DASHBOARD=false)',
        )
    account = _get_account_or_none(db, account_ref)
    account_id = account.account_id if account else None
    region = account.region if account else None
    return await metaapi_client.get_history_orders(
        account_id=account_id,
        region=region,
        days=days,
        limit=limit,
        offset=offset,
    )
