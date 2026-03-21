import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.agent_runtime_message import AgentRuntimeMessage
from app.db.models.agent_runtime_session import AgentRuntimeSession
from app.db.models.run import AnalysisRun
from app.db.models.user import User
from app.services.agent_runtime.constants import AGENTIC_V2_RUNTIME
from app.services.agent_runtime.models import RuntimeSessionState
from app.services.agent_runtime.planner import PlannerDecision
from app.services.agent_runtime.runtime import AgenticTradingRuntime
from app.services.agent_runtime.session_store import RuntimeSessionStore
from app.services.agent_runtime.tool_registry import RuntimeToolRegistry
from app.services.llm.provider_client import LlmClient


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
        assert runtime_trace['last_event_id'] == 3
        assert runtime_trace['event_count'] == 3
        assert [item['id'] for item in runtime_trace['events']] == [2, 3]


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
