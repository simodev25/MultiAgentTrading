import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models.connector_config import ConnectorConfig
from app.schemas.order_guardian import OrderGuardianStatusUpdate
from app.services.trading.order_guardian import OrderGuardianService


def _session() -> Session:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_order_guardian_status_defaults_and_updates() -> None:
    db = _session()
    service = OrderGuardianService()

    status = service.get_status(db)
    assert status['enabled'] is False
    assert status['timeframe'] == 'H1'
    assert status['max_positions_per_cycle'] >= 1

    updated = service.update_status(
        db,
        OrderGuardianStatusUpdate(
            enabled=True,
            timeframe='m15',
            risk_percent=1.5,
            max_positions_per_cycle=7,
            sl_tp_min_delta=0.0003,
        ),
    )
    assert updated['enabled'] is True
    assert updated['timeframe'] == 'M15'
    assert updated['risk_percent'] == 1.5
    assert updated['max_positions_per_cycle'] == 7
    assert updated['sl_tp_min_delta'] == 0.0003


def test_order_guardian_evaluate_skips_when_disabled() -> None:
    db = _session()
    service = OrderGuardianService()

    result = asyncio.run(service.evaluate(db))

    assert result['enabled'] is False
    assert result['analyzed_positions'] == 0
    assert result['skipped_reason'] == 'Order guardian disabled'


def test_order_guardian_evaluate_executes_exit_and_update_actions() -> None:
    db = _session()
    service = OrderGuardianService()
    service.update_status(db, OrderGuardianStatusUpdate(enabled=True, timeframe='H1', sl_tp_min_delta=0.0001))

    async def fake_get_positions(*args, **kwargs):
        return {
            'degraded': False,
            'provider': 'sdk',
            'positions': [
                {
                    'id': '101',
                    'symbol': 'EURUSD',
                    'type': 'POSITION_TYPE_BUY',
                    'volume': 0.2,
                    'stopLoss': 1.1000,
                    'takeProfit': 1.1300,
                },
                {
                    'id': '202',
                    'symbol': 'GBPUSD',
                    'type': 'POSITION_TYPE_SELL',
                    'volume': 0.15,
                    'stopLoss': 1.3200,
                    'takeProfit': 1.2800,
                },
            ],
        }

    async def fake_analyze_position(
        db_session,
        *,
        symbol: str,
        timeframe: str,
        risk_percent: float,
        llm_model_overrides: dict[str, str] | None = None,
    ):
        if symbol == 'EURUSD':
            return {
                'trader_decision': {
                    'decision': 'SELL',
                    'stop_loss': 1.1100,
                    'take_profit': 1.0900,
                    'confidence': 0.8,
                    'net_score': -0.6,
                }
            }
        return {
            'trader_decision': {
                'decision': 'SELL',
                'stop_loss': 1.3150,
                'take_profit': 1.2700,
                'confidence': 0.7,
                'net_score': -0.3,
            }
        }

    async def fake_close_position(*args, **kwargs):
        return {'executed': True, 'provider': 'sdk'}

    async def fake_modify_position(*args, **kwargs):
        return {'executed': True, 'provider': 'sdk'}

    service.metaapi.get_positions = fake_get_positions  # type: ignore[method-assign]
    service._analyze_position = fake_analyze_position  # type: ignore[method-assign]
    service.metaapi.close_position = fake_close_position  # type: ignore[method-assign]
    service.metaapi.modify_position = fake_modify_position  # type: ignore[method-assign]

    result = asyncio.run(service.evaluate(db))

    assert result['enabled'] is True
    assert result['analyzed_positions'] == 2
    assert result['actions_executed'] == 2
    assert [item['action'] for item in result['actions']] == ['EXIT', 'UPDATE_SL_TP']


def test_order_guardian_uses_dedicated_model_override_without_overwriting_agent_specific() -> None:
    db = _session()
    service = OrderGuardianService()

    db.add(
        ConnectorConfig(
            connector_name='ollama',
            enabled=True,
            settings={
                'agent_models': {
                    'order-guardian': 'gpt-oss:120b-cloud',
                    'trader-agent': 'qwen3.5:32b',
                },
            },
        )
    )
    db.commit()

    overrides = service._guardian_llm_model_overrides(db)

    assert overrides.get('news-analyst') == 'gpt-oss:120b-cloud'
    assert 'trader-agent' not in overrides


def test_order_guardian_generates_llm_report_when_enabled() -> None:
    db = _session()
    service = OrderGuardianService()

    service.model_selector.is_enabled = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    service.model_selector.resolve = lambda *_args, **_kwargs: 'gpt-oss:120b-cloud'  # type: ignore[method-assign]
    service.llm.chat = lambda *_args, **_kwargs: {'text': 'Rapport LLM guardian', 'degraded': False}  # type: ignore[method-assign]

    report = service._guardian_report_from_llm(
        db,
        account_label='Paper',
        timeframe='H1',
        dry_run=True,
        summary={'positions_seen': 2, 'actions_total': 1},
        actions=[
            {
                'position_id': '101',
                'symbol': 'EURUSD',
                'side': 'BUY',
                'decision': 'SELL',
                'action': 'EXIT',
                'executed': True,
                'reason': 'opposite signal',
            }
        ],
    )

    assert report['text'] == 'Rapport LLM guardian'
    assert report['prompt_meta']['llm_model'] == 'gpt-oss:120b-cloud'
