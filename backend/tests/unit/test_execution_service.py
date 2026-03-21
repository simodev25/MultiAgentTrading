import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.execution_order import ExecutionOrder
from app.db.models.run import AnalysisRun
from app.db.models.user import User
from app.services.execution.executor import ExecutionService


def test_json_safe_serializes_datetime() -> None:
    payload = {'ts': datetime(2026, 3, 12, 22, 0, tzinfo=timezone.utc), 'value': 1}
    safe = ExecutionService._json_safe(payload)
    assert isinstance(safe['ts'], str)
    assert safe['value'] == 1


def test_normalized_result_contains_status_and_executed() -> None:
    payload = {'simulated': True}
    normalized = ExecutionService._normalized_result(payload, status='simulated', executed=False, reason='Simulation mode')

    assert normalized['status'] == 'simulated'
    assert normalized['executed'] is False
    assert normalized['reason'] == 'Simulation mode'
    assert normalized['simulated'] is True


def test_normalized_result_keeps_existing_reason() -> None:
    payload = {'reason': 'Upstream reason'}
    normalized = ExecutionService._normalized_result(payload, status='failed', executed=False, reason='Fallback reason')

    assert normalized['status'] == 'failed'
    assert normalized['executed'] is False
    assert normalized['reason'] == 'Upstream reason'


def _seed_run(db: Session) -> AnalysisRun:
    user = User(email='exec-test@local.dev', hashed_password='x', role='admin', is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    run = AnalysisRun(
        pair='EURUSD',
        timeframe='M5',
        mode='simulation',
        status='pending',
        decision={},
        trace={},
        created_by_id=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_execution_service_replays_idempotent_request_in_simulation() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = ExecutionService()
    with Session(engine) as db:
        run = _seed_run(db)

        first = asyncio.run(
            service.execute(
                db=db,
                run_id=run.id,
                mode='simulation',
                symbol='EURUSD',
                side='BUY',
                volume=0.3,
                stop_loss=1.09,
                take_profit=1.11,
                metaapi_account_ref=None,
            )
        )
        second = asyncio.run(
            service.execute(
                db=db,
                run_id=run.id,
                mode='simulation',
                symbol='EURUSD',
                side='BUY',
                volume=0.3,
                stop_loss=1.09,
                take_profit=1.11,
                metaapi_account_ref=None,
            )
        )

        orders = db.query(ExecutionOrder).filter(ExecutionOrder.run_id == run.id).all()
        assert len(orders) == 1
        assert first['status'] == 'simulated'
        assert first['idempotency_key']
        assert second['status'] == 'simulated'
        assert second['idempotent_replay'] is True
        assert second['idempotency_key'] == first['idempotency_key']


def test_execution_service_classifies_failed_live_execution(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = ExecutionService()
    service.settings.allow_live_trading = True
    async def _fake_place_order(**_kwargs):
        return {'executed': False, 'reason': 'Timeout while contacting broker gateway'}

    monkeypatch.setattr(service.metaapi, 'place_order', _fake_place_order)

    with Session(engine) as db:
        run = _seed_run(db)
        result = asyncio.run(
            service.execute(
                db=db,
                run_id=run.id,
                mode='live',
                symbol='EURUSD',
                side='BUY',
                volume=0.2,
                stop_loss=1.09,
                take_profit=1.11,
                metaapi_account_ref=None,
            )
        )

        assert result['status'] == 'failed'
        assert result['executed'] is False
        assert result['error_class'] == 'transient_network'
        assert result['retryable'] is True
