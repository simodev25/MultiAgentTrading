import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.agent_runtime_event import AgentRuntimeEvent
from app.db.models.agent_runtime_message import AgentRuntimeMessage
from app.db.models.agent_runtime_session import AgentRuntimeSession
from app.db.models.connector_config import ConnectorConfig
from app.db.models.run import AnalysisRun
from app.db.models.user import User
from app.services.agent_runtime.constants import AGENTIC_V2_RUNTIME
from app.services.agent_runtime.models import RuntimeSessionState
from app.services.agent_runtime.planner import PlannerDecision
from app.services.agent_runtime.runtime import AgenticTradingRuntime
from app.services.agent_runtime.session_store import RuntimeSessionStore
from app.services.agent_runtime.tool_registry import RuntimeToolRegistry
from app.services.llm.provider_client import LlmClient
from app.observability.metrics import (
    agentic_runtime_planner_calls_total,
    agentic_runtime_execution_outcomes_total,
    agentic_runtime_final_decisions_total,
    agentic_runtime_runs_total,
    agentic_runtime_tool_calls_total,
    agentic_runtime_tool_selections_total,
)


def _seed_run(db: Session) -> AnalysisRun:
    user = User(email='agentic@local.dev', hashed_password='x', role='admin', is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    run = AnalysisRun(
        pair='EURUSD',
        timeframe='H1',
        mode='simulation',
        status='pending',
        trace={},
        created_by_id=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _counter_value(metric, **labels: str) -> float:
    return float(metric.labels(**labels)._value.get())


def test_runtime_tool_registry_supports_sync_and_async_handlers() -> None:
    registry = RuntimeToolRegistry()
    registry.register('sync-tool', lambda **_kwargs: {'value': 'sync'})

    async def async_tool(**_kwargs):
        return {'value': 'async'}

    registry.register('async-tool', async_tool)

    sync_result = asyncio.run(registry.call('sync-tool'))
    async_result = asyncio.run(registry.call('async-tool'))

    assert sync_result == {'value': 'sync'}
    assert async_result == {'value': 'async'}


def test_runtime_tool_registry_enforces_scoped_allowlist() -> None:
    registry = RuntimeToolRegistry()
    registry.register('allowed-tool', lambda **_kwargs: {'status': 'ok'})
    registry.register('blocked-tool', lambda **_kwargs: {'status': 'ok'})

    allowed_result = asyncio.run(registry.call('allowed-tool', allowed_tools=['allowed-tool']))
    assert allowed_result == {'status': 'ok'}

    with pytest.raises(PermissionError):
        asyncio.run(registry.call('blocked-tool', allowed_tools=['allowed-tool']))


def test_runtime_session_store_appends_monotonic_events() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        store = RuntimeSessionStore(event_limit=2)
        state = RuntimeSessionState(
            objective={'kind': 'trade-analysis'},
            max_turns=8,
            plan=['resolve_market_context', 'run_trader_agent'],
        )

        store.initialize(
            db,
            run,
            runtime_engine=AGENTIC_V2_RUNTIME,
            objective=state.objective,
            plan=state.plan,
            max_turns=state.max_turns,
        )
        store.append_event(db, run, state=state, event_type='lifecycle', name='started')
        state.turn = 1
        store.append_event(db, run, state=state, event_type='tool_called', name='resolve_market_context')
        state.turn = 2
        store.append_event(db, run, state=state, event_type='tool_result', name='resolve_market_context')
        db.refresh(run)

        runtime_trace = run.trace['agentic_runtime']
        event_rows = (
            db.query(AgentRuntimeEvent)
            .filter(AgentRuntimeEvent.run_id == run.id)
            .order_by(AgentRuntimeEvent.seq.asc())
            .all()
        )
        assert runtime_trace['last_event_id'] == 3
        assert runtime_trace['event_count'] == 3
        assert [item['id'] for item in runtime_trace['events']] == [2, 3]
        assert [row.seq for row in event_rows] == [1, 2, 3]


def test_agentic_runtime_selects_memory_refresh_after_follow_up_hold() -> None:
    runtime = AgenticTradingRuntime()
    state = RuntimeSessionState(
        objective={'kind': 'trade-analysis'},
        max_turns=16,
        plan=list(runtime.PLAN),
    )
    state.context.update(
        {
            'market': {'trend': 'bullish'},
            'news': {'news': []},
            'memory_context': [],
            'memory_context_enabled': True,
            'memory_limit': 5,
            'memory_refresh_count': 0,
        }
    )
    state.artifacts['analysis_outputs'] = {
        'technical-analyst': {'signal': 'bullish', 'score': 0.2},
        'news-analyst': {'signal': 'neutral', 'score': 0.0},
        'market-context-analyst': {'signal': 'bullish', 'score': 0.15},
    }
    state.artifacts['bullish'] = {'confidence': 0.6}
    state.artifacts['bearish'] = {'confidence': 0.2}
    state.artifacts['trader_decision'] = {
        'decision': 'HOLD',
        'needs_follow_up': True,
        'follow_up_reason': 'insufficient_evidence',
    }
    state.artifacts['risk'] = {'accepted': True, 'suggested_volume': 0.0}

    assert runtime._next_tool(state) == 'refresh_memory_context'


def test_agentic_runtime_records_subagent_sessions() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        runtime = AgenticTradingRuntime()
        state = RuntimeSessionState(
            objective={'kind': 'trade-analysis'},
            max_turns=12,
            plan=list(runtime.PLAN),
        )

        runtime.session_store.initialize(
            db,
            run,
            runtime_engine=AGENTIC_V2_RUNTIME,
            objective=state.objective,
            plan=state.plan,
            max_turns=state.max_turns,
        )

        output = asyncio.run(
            runtime._tool_spawn_subagent(
                db=db,
                run=run,
                state=state,
                name='technical-analyst',
                label='Technical analyst',
                source_tool='run_technical_analyst',
                objective={'kind': 'technical-analysis'},
                input_payload={'pair': run.pair, 'timeframe': run.timeframe},
                fn=lambda: {'signal': 'bullish', 'score': 0.8},
            )
        )

        assert output == {'signal': 'bullish', 'score': 0.8}
        db.refresh(run)

        runtime_trace = run.trace['agentic_runtime']
        root_session_key = runtime_trace['session_key']
        sessions = runtime_trace['sessions']
        child_session_keys = [key for key in sessions.keys() if key != root_session_key]

        assert len(child_session_keys) == 1
        child_session = sessions[child_session_keys[0]]
        assert child_session['parent_session_key'] == root_session_key
        assert child_session['source_tool'] == 'run_technical_analyst'
        assert child_session['status'] == 'completed'

        assert any(
            item['stream'] == 'sessions' and item['name'] == 'subagent_spawned'
            for item in runtime_trace['events']
        )
        assert any(
            item['stream'] == 'lifecycle'
            and item['sessionKey'] == child_session_keys[0]
            and item['payload'].get('phase') == 'end'
            for item in runtime_trace['events']
        )


def test_llm_client_chat_json_extracts_fenced_json(monkeypatch) -> None:
    client = LlmClient()

    def fake_chat(*_args, **_kwargs):
        return {
            'provider': 'openai',
            'text': '```json\n{"tool":"run_news_analyst","reason":"fresh headlines first"}\n```',
            'degraded': False,
        }

    monkeypatch.setattr(client, 'chat', fake_chat)
    payload = client.chat_json('system', 'user')

    assert payload['json'] == {'tool': 'run_news_analyst', 'reason': 'fresh headlines first'}
    assert payload['json_error'] is None


def test_agentic_runtime_uses_planner_choice(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        runtime = AgenticTradingRuntime()
        state = RuntimeSessionState(
            objective={'kind': 'trade-analysis'},
            max_turns=12,
            plan=list(runtime.PLAN),
        )
        state.context.update(
            {
                'market': {'trend': 'bullish'},
                'news': {'news': []},
                'memory_context': [],
                'memory_context_enabled': True,
                'memory_limit': 5,
                'memory_refresh_count': 0,
            }
        )
        runtime.session_store.initialize(
            db,
            run,
            runtime_engine=AGENTIC_V2_RUNTIME,
            objective=state.objective,
            plan=state.plan,
            max_turns=state.max_turns,
        )
        selections_before = _counter_value(
            agentic_runtime_tool_selections_total,
            tool='run_news_analyst',
            source='llm',
            degraded='false',
        )

        def fake_choose_tool(**_kwargs):
            return PlannerDecision(
                tool_name='run_news_analyst',
                reason='News should be analyzed first.',
                source='llm',
                degraded=False,
                llm_model='test-model',
            )

        monkeypatch.setattr(runtime.planner, 'choose_tool', fake_choose_tool)

        selected_tool = runtime._select_next_tool(db, run, state)

        assert selected_tool == 'run_news_analyst'
        db.refresh(run)
        runtime_trace = run.trace['agentic_runtime']
        assert any(
            item['stream'] == 'assistant'
            and item['name'] == 'agentic-runtime-planner'
            and item['payload'].get('selectedTool') == 'run_news_analyst'
            for item in runtime_trace['events']
        )
        assert _counter_value(
            agentic_runtime_tool_selections_total,
            tool='run_news_analyst',
            source='llm',
            degraded='false',
        ) == selections_before + 1.0


def test_agentic_runtime_planner_falls_back_on_invalid_tool(monkeypatch) -> None:
    runtime = AgenticTradingRuntime()
    state = RuntimeSessionState(
        objective={'kind': 'trade-analysis'},
        max_turns=12,
        plan=list(runtime.PLAN),
    )
    state.context.update(
        {
            'market': {'trend': 'bullish'},
            'news': {'news': []},
            'memory_context': [],
            'memory_context_enabled': True,
            'memory_limit': 5,
            'memory_refresh_count': 0,
        }
    )
    candidate_tools = [
        {'name': 'run_technical_analyst', 'description': 'Technical'},
        {'name': 'run_news_analyst', 'description': 'News'},
    ]

    monkeypatch.setattr(runtime.planner.model_selector, 'is_enabled', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runtime.planner.model_selector, 'resolve', lambda *_args, **_kwargs: 'test-model')
    monkeypatch.setattr(runtime.planner.model_selector, 'resolve_skills', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        runtime.planner.llm,
        'chat_json',
        lambda *_args, **_kwargs: {
            'json': {
                'decision_type': 'select_tool',
                'selected_tool': 'run_unknown_tool',
                'why_now': 'invalid',
                'required_preconditions': [],
                'expected_output_contract': {'summary': 'x'},
                'confidence': 0.5,
                'needs_followup': False,
                'abort_reason': None,
            },
            'degraded': False,
        },
    )

    before = _counter_value(
        agentic_runtime_planner_calls_total,
        status='fallback',
        source='deterministic',
    )
    decision = runtime.planner.choose_tool(db=None, state=state, candidate_tools=candidate_tools)

    assert decision.tool_name == 'run_technical_analyst'
    assert decision.contract_valid is False
    assert decision.degraded is True
    assert _counter_value(
        agentic_runtime_planner_calls_total,
        status='fallback',
        source='deterministic',
    ) == before + 1.0


def test_agentic_runtime_planner_accepts_legacy_contract_as_degraded(monkeypatch) -> None:
    runtime = AgenticTradingRuntime()
    state = RuntimeSessionState(
        objective={'kind': 'trade-analysis'},
        max_turns=12,
        plan=list(runtime.PLAN),
    )
    state.context.update(
        {
            'market': {'trend': 'bullish'},
            'news': {'news': []},
            'memory_context': [],
        }
    )
    candidate_tools = [
        {'name': 'run_technical_analyst', 'description': 'Technical'},
        {'name': 'run_news_analyst', 'description': 'News'},
    ]

    monkeypatch.setattr(runtime.planner.model_selector, 'is_enabled', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runtime.planner.model_selector, 'resolve', lambda *_args, **_kwargs: 'test-model')
    monkeypatch.setattr(runtime.planner.model_selector, 'resolve_skills', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        runtime.planner.llm,
        'chat_json',
        lambda *_args, **_kwargs: {
            'json': {'tool': 'run_news_analyst', 'reason': 'legacy contract'},
            'degraded': False,
        },
    )

    decision = runtime.planner.choose_tool(db=None, state=state, candidate_tools=candidate_tools)

    assert decision.tool_name == 'run_news_analyst'
    assert decision.source == 'llm_legacy'
    assert decision.contract_valid is False
    assert decision.degraded is True


def test_agentic_runtime_can_resume_existing_subagent_session() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        runtime = AgenticTradingRuntime()
        state = RuntimeSessionState(
            objective={'kind': 'trade-analysis'},
            max_turns=12,
            plan=list(runtime.PLAN),
        )
        runtime.session_store.initialize(
            db,
            run,
            runtime_engine=AGENTIC_V2_RUNTIME,
            objective=state.objective,
            plan=state.plan,
            max_turns=state.max_turns,
        )

        first_output = asyncio.run(
            runtime._tool_spawn_subagent(
                db=db,
                run=run,
                state=state,
                name='technical-analyst',
                label='Technical analyst',
                source_tool='run_technical_analyst',
                objective={'kind': 'technical-analysis'},
                input_payload={'pair': run.pair, 'timeframe': run.timeframe},
                fn=lambda: {'signal': 'bullish', 'score': 0.8},
            )
        )
        assert first_output['signal'] == 'bullish'

        db.refresh(run)
        runtime_trace = run.trace['agentic_runtime']
        root_session_key = runtime_trace['session_key']
        child_session_key = next(key for key in runtime_trace['sessions'].keys() if key != root_session_key)

        resumed_output = asyncio.run(
            runtime._tool_spawn_subagent(
                db=db,
                run=run,
                state=state,
                session_key=child_session_key,
                name='technical-analyst',
                label='Technical analyst',
                source_tool='run_technical_analyst',
                objective={'kind': 'technical-analysis'},
                input_payload={'pair': run.pair, 'timeframe': run.timeframe},
                fn=lambda: {'signal': 'bearish', 'score': 0.6},
            )
        )
        assert resumed_output['signal'] == 'bearish'

        db.refresh(run)
        resumed_trace = run.trace['agentic_runtime']
        resumed_session = resumed_trace['sessions'][child_session_key]
        assert resumed_session['resume_count'] == 1
        assert resumed_session['status'] == 'completed'
        assert resumed_session['summary']['signal'] == 'bearish'
        assert resumed_session['last_resumed_at'] is not None
        assert any(
            item['stream'] == 'sessions'
            and item['name'] == 'subagent_resumed'
            and item['payload'].get('childSessionKey') == child_session_key
            for item in resumed_trace['events']
        )


def test_agentic_runtime_call_tool_emits_success_metrics() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        runtime = AgenticTradingRuntime()
        state = RuntimeSessionState(
            objective={'kind': 'trade-analysis'},
            max_turns=12,
            plan=list(runtime.PLAN),
        )
        runtime.session_store.initialize(
            db,
            run,
            runtime_engine=AGENTIC_V2_RUNTIME,
            objective=state.objective,
            plan=state.plan,
            max_turns=state.max_turns,
        )
        runtime.registry.register(
            'test-runtime-tool',
            lambda **_kwargs: {'status': 'ok'},
            description='Synthetic test tool.',
            section='tests',
            profiles=('agentic_v2',),
        )
        calls_before = _counter_value(
            agentic_runtime_tool_calls_total,
            tool='test-runtime-tool',
            status='success',
        )

        result = asyncio.run(
            runtime._call_tool(
                db,
                run,
                state,
                tool_name='test-runtime-tool',
                risk_percent=1.0,
                metaapi_account_ref=None,
            )
        )

        assert result == {'status': 'ok'}
        assert _counter_value(
            agentic_runtime_tool_calls_total,
            tool='test-runtime-tool',
            status='success',
        ) == calls_before + 1.0


def test_agentic_runtime_sessions_resume_tool(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        runtime = AgenticTradingRuntime()
        state = RuntimeSessionState(
            objective={'kind': 'trade-analysis'},
            max_turns=12,
            plan=list(runtime.PLAN),
        )
        runtime.session_store.initialize(
            db,
            run,
            runtime_engine=AGENTIC_V2_RUNTIME,
            objective=state.objective,
            plan=state.plan,
            max_turns=state.max_turns,
        )
        child_session = runtime.session_store.create_subagent_session(
            db,
            run,
            parent_session_key=runtime.session_store.root_session_key(run),
            name='news-analyst',
            label='News analyst',
            objective={'kind': 'news-analysis'},
            source_tool='run_news_analyst',
        )

        async def fake_registry_call(name, **kwargs):
            assert name == 'run_news_analyst'
            assert kwargs['existing_session_key'] == child_session['session_key']
            runtime.session_store.reopen_subagent_session(
                db,
                run,
                session_key=child_session['session_key'],
                metadata={'resumed_via': 'sessions_resume'},
            )
            runtime.session_store.finalize_subagent_session(
                db,
                run,
                session_key=child_session['session_key'],
                status='completed',
                summary={'resumed': True},
            )
            return {'resumed': True}

        monkeypatch.setattr(runtime.registry, 'call', fake_registry_call)

        output = asyncio.run(
            runtime._tool_sessions_resume(
                db=db,
                run=run,
                state=state,
                session_key=child_session['session_key'],
                risk_percent=1.0,
                metaapi_account_ref=None,
            )
        )

        assert output == {'resumed': True}
        db.refresh(run)
        runtime_trace = run.trace['agentic_runtime']
        session_entry = runtime_trace['sessions'][child_session['session_key']]
        assert session_entry['resume_count'] == 1
        assert session_entry['summary'] == {'resumed': True}


def test_runtime_session_store_restores_state_snapshot() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        store = RuntimeSessionStore(event_limit=10, history_limit=10)
        state = RuntimeSessionState(
            objective={'kind': 'trade-analysis'},
            turn=3,
            max_turns=12,
            status='running',
            current_phase='run_news_analyst',
            plan=['resolve_market_context', 'run_news_analyst'],
        )
        state.completed_tools = ['resolve_market_context']
        state.context = {'market': {'trend': 'bullish'}}
        state.artifacts = {'analysis_outputs': {'technical-analyst': {'signal': 'bullish'}}}
        state.history = [{'turn': 1, 'tool': 'resolve_market_context'}]
        state.notes = ['checkpoint']

        store.initialize(
            db,
            run,
            runtime_engine=AGENTIC_V2_RUNTIME,
            objective=state.objective,
            plan=state.plan,
            max_turns=state.max_turns,
        )
        store.persist_session(db, run, state=state)
        restored = store.restore_state(run)
        session_row = db.query(AgentRuntimeSession).filter(AgentRuntimeSession.run_id == run.id).one()

        assert restored is not None
        assert restored.turn == 3
        assert restored.current_phase == 'run_news_analyst'
        assert restored.context['market']['trend'] == 'bullish'
        assert restored.artifacts['analysis_outputs']['technical-analyst']['signal'] == 'bullish'
        assert restored.notes == ['checkpoint']
        assert isinstance(session_row.state_snapshot, dict)
        assert 'state_snapshot' not in run.trace['agentic_runtime']

        hydrated_trace = store.hydrate_trace(run, include_state_snapshot=True)
        assert hydrated_trace['agentic_runtime']['state_snapshot']['turn'] == 3


def test_agentic_runtime_sessions_send_and_history() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        runtime = AgenticTradingRuntime()
        state = RuntimeSessionState(
            objective={'kind': 'trade-analysis'},
            max_turns=12,
            plan=list(runtime.PLAN),
        )
        runtime.session_store.initialize(
            db,
            run,
            runtime_engine=AGENTIC_V2_RUNTIME,
            objective=state.objective,
            plan=state.plan,
            max_turns=state.max_turns,
        )
        child_session = runtime.session_store.create_subagent_session(
            db,
            run,
            parent_session_key=runtime.session_store.root_session_key(run),
            name='trader-agent',
            label='Trader agent',
            objective={'kind': 'trade-decision'},
            source_tool='run_trader_agent',
        )

        output = asyncio.run(
            runtime._tool_sessions_send(
                db=db,
                run=run,
                state=state,
                session_key=child_session['session_key'],
                message='Continue with updated evidence.',
                risk_percent=1.0,
                metaapi_account_ref=None,
                resume=False,
            )
        )

        assert output['delivered'] is True
        history = asyncio.run(
            runtime._tool_sessions_history(
                run=run,
                session_key=child_session['session_key'],
                limit=10,
            )
        )
        db.refresh(run)
        message_rows = db.query(AgentRuntimeMessage).filter(AgentRuntimeMessage.run_id == run.id).all()

        assert history['count'] == 1
        assert history['messages'][0]['content'] == 'Continue with updated evidence.'
        assert len(message_rows) == 1
        assert 'session_history' not in run.trace['agentic_runtime']

        hydrated_trace = runtime.session_store.hydrate_trace(run)
        assert hydrated_trace['agentic_runtime']['session_history'][child_session['session_key']][0]['content'] == 'Continue with updated evidence.'


def test_agentic_runtime_writes_debug_trade_trace_json(monkeypatch, tmp_path: Path) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        runtime = AgenticTradingRuntime()
        runtime.settings.debug_trade_json_enabled = True
        runtime.settings.debug_trade_json_dir = str(tmp_path)
        runtime.settings.debug_trade_json_include_price_history = True
        runtime.settings.debug_trade_json_price_history_limit = 20
        runtime.settings.debug_trade_json_inline_in_run_trace = False

        monkeypatch.setattr(runtime.orchestrator.prompt_service, 'seed_defaults', lambda _db: None)
        runs_before = _counter_value(
            agentic_runtime_runs_total,
            status='completed',
            mode='simulation',
            resumed='false',
        )
        decisions_before = _counter_value(
            agentic_runtime_final_decisions_total,
            decision='BUY',
            mode='simulation',
        )
        executions_before = _counter_value(
            agentic_runtime_execution_outcomes_total,
            status='skipped',
            mode='simulation',
        )

        selection_state = {'done': False}

        def fake_select_next_tool(_db, _run, _state):
            if not selection_state['done']:
                return 'resolve_market_context'
            return None

        async def fake_call_tool(_db, _run, state, *, tool_name, risk_percent, metaapi_account_ref):
            assert tool_name == 'resolve_market_context'
            assert risk_percent == 1.0
            assert metaapi_account_ref is None
            state.context['market'] = {
                'degraded': False,
                'pair': run.pair,
                'timeframe': run.timeframe,
                'last_price': 1.102,
                'atr': 0.001,
                'trend': 'bullish',
            }
            state.context['news'] = {
                'degraded': False,
                'pair': run.pair,
                'news': [{'title': 'ECB keeps rates unchanged'}],
            }
            state.context['memory_context'] = [{'summary': 'Memo context'}]
            state.context['memory_context_enabled'] = True
            state.context['memory_signal'] = {'used': True, 'signal': 'supportive'}
            state.context['memory_runtime'] = {'sources': {'vector': 1, 'memori': 0}}
            state.context['memory_retrieval_context'] = {'trend': 'bullish'}
            state.artifacts['analysis_outputs'] = {
                'technical-analyst': {'signal': 'bullish', 'score': 0.4},
                'news-analyst': {'signal': 'neutral', 'score': 0.0},
            }
            state.artifacts['bullish'] = {'arguments': ['Trend aligned'], 'confidence': 0.61}
            state.artifacts['bearish'] = {'arguments': ['Resistance overhead'], 'confidence': 0.22}
            state.artifacts['trader_decision'] = {
                'decision': 'BUY',
                'confidence': 0.61,
                'entry': 1.102,
                'stop_loss': 1.099,
                'take_profit': 1.108,
            }
            state.artifacts['risk'] = {'accepted': True, 'reasons': ['Risk checks passed.'], 'suggested_volume': 0.2}
            state.artifacts['execution_manager'] = {'decision': 'BUY', 'should_execute': False, 'status': 'skipped'}
            state.artifacts['execution_result'] = {'status': 'skipped', 'executed': False}
            runtime.orchestrator._record_step(
                _db,
                _run,
                'resolve_market_context',
                {'tool': tool_name},
                {'status': 'ok'},
            )
            selection_state['done'] = True
            return {'status': 'ok'}

        async def fake_recent_candles(_db, *, pair, timeframe, limit, metaapi_account_ref):
            assert pair == run.pair
            assert timeframe == run.timeframe
            assert limit == 20
            assert metaapi_account_ref is None
            return [{'ts': '2026-03-21T19:00:00Z', 'close': 1.102}]

        monkeypatch.setattr(runtime, '_select_next_tool', fake_select_next_tool)
        monkeypatch.setattr(runtime, '_candidate_tools', lambda _state: [] if selection_state['done'] else ['resolve_market_context'])
        monkeypatch.setattr(runtime, '_call_tool', fake_call_tool)
        monkeypatch.setattr(runtime.orchestrator, 'resolve_recent_candles', fake_recent_candles)
        monkeypatch.setattr(runtime.orchestrator.memory_service, 'add_run_memory', lambda *_args, **_kwargs: None)
        monkeypatch.setattr(runtime.orchestrator.memori_memory_service, 'store_run_memory', lambda *_args, **_kwargs: {'stored': False})

        completed_run = asyncio.run(runtime.execute(db, run, risk_percent=1.0))

        assert completed_run.status == 'completed'
        assert completed_run.trace['debug_trace_meta']['enabled'] is True
        assert completed_run.trace['debug_trace_meta']['file_written'] is True
        debug_path = Path(completed_run.trace['debug_trace_file'])
        assert debug_path.exists()

        payload = json.loads(debug_path.read_text(encoding='utf-8'))
        assert payload['run']['id'] == run.id
        assert payload['context']['price_history'][0]['close'] == 1.102
        assert payload['analysis_bundle']['trader_decision']['decision'] == 'BUY'
        assert _counter_value(
            agentic_runtime_runs_total,
            status='completed',
            mode='simulation',
            resumed='false',
        ) == runs_before + 1.0
        assert _counter_value(
            agentic_runtime_final_decisions_total,
            decision='BUY',
            mode='simulation',
        ) == decisions_before + 1.0
        assert _counter_value(
            agentic_runtime_execution_outcomes_total,
            status='skipped',
            mode='simulation',
        ) == executions_before + 1.0


def test_agentic_runtime_news_tool_toggle_is_applied_in_runtime_v2() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_llm_enabled': {'news-analyst': False},
                    'agent_tools': {
                        'news-analyst': {
                            'news_search': False,
                            'macro_calendar_or_event_feed': True,
                            'symbol_relevance_filter': True,
                            'sentiment_or_event_impact_parser': True,
                        }
                    },
                },
            )
        )
        db.commit()

        run = _seed_run(db)
        runtime = AgenticTradingRuntime()
        state = RuntimeSessionState(
            objective={'kind': 'trade-analysis'},
            max_turns=12,
            plan=list(runtime.PLAN),
        )
        state.context.update(
            {
                'market': {'trend': 'neutral', 'last_price': 1.1, 'atr': 0.001},
                'news': {
                    'news': [{'title': 'EUR stays mixed into session open'}],
                    'macro_events': [],
                },
                'memory_context': [],
                'memory_signal': {},
                'memory_context_enabled': False,
            }
        )

        runtime.session_store.initialize(
            db,
            run,
            runtime_engine=AGENTIC_V2_RUNTIME,
            objective=state.objective,
            plan=state.plan,
            max_turns=state.max_turns,
        )

        output = asyncio.run(
            runtime._tool_run_news_analyst(
                db=db,
                run=run,
                state=state,
                risk_percent=1.0,
                metaapi_account_ref=None,
            )
        )

        invocations = ((output.get('tooling') or {}).get('invocations') or {})
        assert invocations.get('news_search', {}).get('status') == 'disabled'
        assert invocations.get('macro_calendar_or_event_feed', {}).get('status') == 'ok'


def test_agentic_runtime_writes_debug_trade_trace_json_on_failure(monkeypatch, tmp_path: Path) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        runtime = AgenticTradingRuntime()
        runtime.settings.debug_trade_json_enabled = True
        runtime.settings.debug_trade_json_dir = str(tmp_path)
        runtime.settings.debug_trade_json_include_price_history = True
        runtime.settings.debug_trade_json_price_history_limit = 20
        runtime.settings.debug_trade_json_inline_in_run_trace = False

        monkeypatch.setattr(runtime.orchestrator.prompt_service, 'seed_defaults', lambda _db: None)

        def fake_select_next_tool(_db, _run, _state):
            return 'resolve_market_context'

        async def fake_call_tool(_db, _run, state, *, tool_name, risk_percent, metaapi_account_ref):
            assert tool_name == 'resolve_market_context'
            assert risk_percent == 1.0
            assert metaapi_account_ref is None
            state.context['market'] = {
                'degraded': False,
                'pair': run.pair,
                'timeframe': run.timeframe,
                'last_price': 1.102,
                'atr': 0.001,
                'trend': 'bullish',
            }
            state.context['news'] = {
                'degraded': False,
                'pair': run.pair,
                'news': [{'title': 'ECB keeps rates unchanged'}],
            }
            runtime.orchestrator._record_step(
                _db,
                _run,
                'resolve_market_context',
                {'tool': tool_name},
                {'status': 'error', 'reason': 'synthetic failure'},
            )
            raise RuntimeError('synthetic failure')

        async def fake_recent_candles(_db, *, pair, timeframe, limit, metaapi_account_ref):
            assert pair == run.pair
            assert timeframe == run.timeframe
            assert limit == 20
            assert metaapi_account_ref is None
            return [{'ts': '2026-03-21T19:00:00Z', 'close': 1.102}]

        monkeypatch.setattr(runtime, '_select_next_tool', fake_select_next_tool)
        monkeypatch.setattr(runtime, '_call_tool', fake_call_tool)
        monkeypatch.setattr(runtime.orchestrator, 'resolve_recent_candles', fake_recent_candles)

        with pytest.raises(RuntimeError, match='synthetic failure'):
            asyncio.run(runtime.execute(db, run, risk_percent=1.0))

        db.refresh(run)
        assert run.status == 'failed'
        assert run.trace['debug_trace_meta']['enabled'] is True
        assert run.trace['debug_trace_meta']['file_written'] is True
        debug_path = Path(run.trace['debug_trace_file'])
        assert debug_path.exists()

        payload = json.loads(debug_path.read_text(encoding='utf-8'))
        assert payload['run']['id'] == run.id
        assert payload['run']['status'] == 'failed'
        assert payload['context']['price_history'][0]['close'] == 1.102
        assert payload['error']['message'] == 'synthetic failure'


def test_agentic_runtime_blocks_invalid_execution_side(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        runtime = AgenticTradingRuntime()
        state = RuntimeSessionState(
            objective={'kind': 'trade-analysis'},
            max_turns=12,
            plan=list(runtime.PLAN),
        )
        state.artifacts['trader_decision'] = {
            'decision': 'BUY',
            'execution_allowed': True,
            'entry': 1.102,
            'stop_loss': 1.099,
            'take_profit': 1.108,
        }
        state.artifacts['risk'] = {'accepted': True, 'suggested_volume': 0.2}
        monkeypatch.setattr(
            runtime.orchestrator.execution_manager_agent,
            'run',
            lambda *_args, **_kwargs: {
                'decision': 'SELL',
                'should_execute': True,
                'side': 'SELL',
                'volume': 0.2,
                'reason': 'bad side flip',
            },
        )

        output = asyncio.run(
            runtime._tool_run_execution_manager(
                db=db,
                run=run,
                state=state,
                risk_percent=1.0,
                metaapi_account_ref=None,
            )
        )

        assert output['status'] == 'blocked'
        assert output['should_execute'] is False
        assert output['side'] is None
        assert 'does not match trader decision' in output['reason']


def test_agentic_runtime_aborts_live_on_degraded_execution_manager(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        run.mode = 'live'
        db.commit()
        db.refresh(run)
        runtime = AgenticTradingRuntime()
        state = RuntimeSessionState(
            objective={'kind': 'trade-analysis'},
            max_turns=12,
            plan=list(runtime.PLAN),
        )
        state.artifacts['analysis_outputs'] = {
            'technical-analyst': {'signal': 'bullish', 'score': 0.4},
            'news-analyst': {'signal': 'neutral', 'score': 0.0},
            'market-context-analyst': {'signal': 'bullish', 'score': 0.2},
        }
        state.artifacts['bullish'] = {'confidence': 0.7}
        state.artifacts['bearish'] = {'confidence': 0.2}
        state.artifacts['trader_decision'] = {
            'decision': 'BUY',
            'execution_allowed': True,
            'entry': 1.102,
            'stop_loss': 1.099,
            'take_profit': 1.108,
        }
        state.artifacts['risk'] = {'accepted': True, 'suggested_volume': 0.2}

        monkeypatch.setattr(
            runtime.orchestrator.execution_manager_agent,
            'run',
            lambda *_args, **_kwargs: {
                'decision': 'BUY',
                'should_execute': True,
                'side': 'BUY',
                'volume': 0.2,
                'reason': 'degraded confirmation',
                'degraded': True,
            },
        )

        with pytest.raises(RuntimeError, match='degraded LLM response from execution-manager'):
            asyncio.run(
                runtime._tool_run_execution_manager(
                    db=db,
                    run=run,
                    state=state,
                    risk_percent=1.0,
                    metaapi_account_ref=None,
                )
            )
