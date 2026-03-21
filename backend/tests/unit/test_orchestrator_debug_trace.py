import asyncio
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.run import AnalysisRun
from app.db.models.user import User
from app.services.orchestrator.engine import ForexOrchestrator


def _seed_run(db: Session, *, mode: str = 'simulation') -> AnalysisRun:
    user = User(email='debug@local.dev', hashed_password='x', role='admin', is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    run = AnalysisRun(
        pair='EURUSD',
        timeframe='H1',
        mode=mode,
        status='pending',
        trace={},
        created_by_id=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_compact_analysis_outputs_for_debate_drops_prompt_noise() -> None:
    payload = {
        'technical-analyst': {
            'signal': 'bullish',
            'score': 0.25,
            'reason': 'trend aligned',
            'indicators': {
                'trend': 'bullish',
                'rsi': 62.0,
                'macd_diff': 0.001,
                'last_price': 1.12,
                'atr': 0.0008,
                'ema_fast': 1.11,
            },
            'prompt_meta': {'system_prompt': 'very long', 'user_prompt': 'very long too'},
        },
        'news-analyst': {
            'signal': 'neutral',
            'score': 0.0,
            'reason': 'No Yahoo Finance news',
            'prompt_meta': {'prompt_id': 1},
        },
    }

    compact = ForexOrchestrator._compact_analysis_outputs_for_debate(payload)

    assert compact['technical-analyst']['signal'] == 'bullish'
    assert compact['technical-analyst']['score'] == 0.25
    assert compact['technical-analyst']['indicators']['trend'] == 'bullish'
    assert 'ema_fast' not in compact['technical-analyst']['indicators']
    assert 'prompt_meta' not in compact['technical-analyst']
    assert 'prompt_meta' not in compact['news-analyst']


def test_orchestrator_writes_debug_trade_trace_json(monkeypatch, tmp_path: Path) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db)
        orchestrator = ForexOrchestrator()

        orchestrator.settings.debug_trade_json_enabled = True
        orchestrator.settings.debug_trade_json_dir = str(tmp_path)
        orchestrator.settings.debug_trade_json_include_price_history = True
        orchestrator.settings.debug_trade_json_price_history_limit = 50
        orchestrator.settings.debug_trade_json_inline_in_run_trace = False

        monkeypatch.setattr(orchestrator.prompt_service, 'seed_defaults', lambda _db: None)
        monkeypatch.setattr(orchestrator.market_provider, 'get_market_snapshot', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'timeframe': 'H1',
            'last_price': 1.102,
            'atr': 0.001,
            'trend': 'bullish',
        })
        monkeypatch.setattr(orchestrator.market_provider, 'get_news_context', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'news': [{'title': 'ECB keeps rates unchanged'}],
        })
        monkeypatch.setattr(orchestrator.market_provider, 'get_recent_candles', lambda *_args, **_kwargs: [
            {'ts': '2026-03-18T12:00:00+00:00', 'open': 1.101, 'high': 1.103, 'low': 1.1, 'close': 1.102, 'volume': 1000}
        ])
        monkeypatch.setattr(orchestrator.memory_service, 'search', lambda **_kwargs: [{'summary': 'Memo context'}])
        monkeypatch.setattr(orchestrator.memory_service, 'add_run_memory', lambda *_args, **_kwargs: None)

        def fake_analyze_context(*_args, **kwargs):
            local_db = kwargs.get('db')
            local_run = kwargs.get('run')
            if local_db is not None and local_run is not None:
                orchestrator._record_step(
                    local_db,
                    local_run,
                    'technical-analyst',
                    {'pair': 'EURUSD', 'timeframe': 'H1'},
                    {
                        'signal': 'bullish',
                        'score': 0.2,
                        'prompt_meta': {
                            'llm_enabled': False,
                            'skills_count': 1,
                            'skills': ['Convergence technique stricte'],
                        },
                    },
                )
            return {
                'analysis_outputs': {'technical-analyst': {'signal': 'bullish', 'score': 0.2}},
                'bullish': {'arguments': ['Trend aligns'], 'confidence': 0.7},
                'bearish': {'arguments': ['No strong bearish trigger'], 'confidence': 0.2},
                'trader_decision': {
                    'decision': 'BUY',
                    'entry': 1.102,
                    'stop_loss': 1.1,
                    'take_profit': 1.106,
                    'confidence': 0.6,
                },
                'risk': {
                    'accepted': True,
                    'suggested_volume': 0.1,
                    'reasons': ['Risk checks passed'],
                },
            }

        monkeypatch.setattr(orchestrator, 'analyze_context', fake_analyze_context)
        monkeypatch.setattr(
            orchestrator.execution_manager_agent,
            'run',
            lambda *_args, **_kwargs: {
                'decision': 'HOLD',
                'should_execute': False,
                'side': None,
                'volume': 0.0,
                'reason': 'Execution deferred by policy',
                'prompt_meta': {'llm_enabled': False, 'skills_count': 0, 'skills': []},
            },
        )

        completed_run = asyncio.run(orchestrator.execute(db, run, risk_percent=1.0))

        assert completed_run.status == 'completed'
        assert completed_run.trace['debug_trace_meta']['enabled'] is True
        debug_path = Path(completed_run.trace['debug_trace_file'])
        assert debug_path.exists()

        payload = json.loads(debug_path.read_text(encoding='utf-8'))
        assert payload['run']['id'] == completed_run.id
        assert payload['run']['status'] == 'completed'
        assert payload['context']['price_history'][0]['close'] == 1.102
        assert 'memory_signal' in payload['context']
        assert payload['analysis_bundle']['execution_result']['status'] == 'skipped'
        assert completed_run.decision['execution']['status'] == 'skipped'
        assert completed_run.decision['execution_manager']['status'] == 'skipped'
        assert completed_run.decision['execution_manager']['execution']['status'] == 'skipped'
        assert completed_run.decision['execution_manager']['decision'] == 'HOLD'
        assert completed_run.decision['execution_manager']['should_execute'] is False
        assert any(step['agent_name'] == 'execution-manager' for step in payload['agent_steps'])
        assert 'memory_signal' in completed_run.trace


def test_orchestrator_fails_live_run_when_llm_output_is_degraded(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db, mode='live')
        orchestrator = ForexOrchestrator()

        monkeypatch.setattr(orchestrator.prompt_service, 'seed_defaults', lambda _db: None)
        monkeypatch.setattr(orchestrator.market_provider, 'get_market_snapshot', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'timeframe': 'H1',
            'last_price': 1.102,
            'atr': 0.001,
            'trend': 'bullish',
        })
        monkeypatch.setattr(orchestrator.market_provider, 'get_news_context', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'news': [],
        })
        monkeypatch.setattr(orchestrator.memory_service, 'search', lambda **_kwargs: [])
        monkeypatch.setattr(orchestrator.memory_service, 'add_run_memory', lambda *_args, **_kwargs: None)

        execution_called = {'value': False}

        def fake_analyze_context(*_args, **_kwargs):
            return {
                'analysis_outputs': {
                    'technical-analyst': {'signal': 'bullish', 'score': 0.2, 'degraded': True},
                    'news-analyst': {'signal': 'neutral', 'score': 0.0},
                    'market-context-analyst': {'signal': 'bullish', 'score': 0.2},
                },
                'bullish': {'arguments': ['Trend aligns'], 'confidence': 0.7},
                'bearish': {'arguments': [], 'confidence': 0.0},
                'trader_decision': {
                    'decision': 'BUY',
                    'entry': 1.102,
                    'stop_loss': 1.1,
                    'take_profit': 1.106,
                    'confidence': 0.6,
                },
                'risk': {
                    'accepted': True,
                    'suggested_volume': 0.1,
                    'reasons': ['Risk checks passed'],
                },
            }

        def fake_execution_manager(*_args, **_kwargs):
            execution_called['value'] = True
            return {
                'decision': 'BUY',
                'should_execute': True,
                'side': 'BUY',
                'volume': 0.1,
                'reason': 'Should never be reached',
            }

        monkeypatch.setattr(orchestrator, 'analyze_context', fake_analyze_context)
        monkeypatch.setattr(orchestrator.execution_manager_agent, 'run', fake_execution_manager)

        failed_run = asyncio.run(orchestrator.execute(db, run, risk_percent=1.0))

        assert failed_run.status == 'failed'
        assert 'degraded LLM response from technical-analyst' in str(failed_run.error)
        assert execution_called['value'] is False


def test_orchestrator_allows_live_hold_when_no_trade_candidate_despite_degradation(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db, mode='live')
        orchestrator = ForexOrchestrator()

        monkeypatch.setattr(orchestrator.prompt_service, 'seed_defaults', lambda _db: None)
        monkeypatch.setattr(orchestrator.market_provider, 'get_market_snapshot', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'timeframe': 'H1',
            'last_price': 1.102,
            'atr': 0.001,
            'trend': 'bullish',
        })
        monkeypatch.setattr(orchestrator.market_provider, 'get_news_context', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'news': [],
        })
        monkeypatch.setattr(orchestrator.memory_service, 'search', lambda **_kwargs: [])
        monkeypatch.setattr(orchestrator.memory_service, 'add_run_memory', lambda *_args, **_kwargs: None)

        def fake_analyze_context(*_args, **_kwargs):
            return {
                'analysis_outputs': {
                    'technical-analyst': {'signal': 'neutral', 'score': 0.0, 'degraded': True},
                    'news-analyst': {'signal': 'neutral', 'score': 0.0},
                    'market-context-analyst': {'signal': 'neutral', 'score': 0.0},
                },
                'bullish': {'arguments': ['No edge'], 'confidence': 0.0, 'degraded': True},
                'bearish': {'arguments': ['No edge'], 'confidence': 0.0, 'degraded': True},
                'trader_decision': {
                    'decision': 'HOLD',
                    'entry': 1.102,
                    'stop_loss': None,
                    'take_profit': None,
                    'confidence': 0.1,
                    'degraded': True,
                },
                'risk': {
                    'accepted': True,
                    'suggested_volume': 0.0,
                    'reasons': ['No trade requested (HOLD).'],
                    'degraded': False,
                },
            }

        monkeypatch.setattr(orchestrator, 'analyze_context', fake_analyze_context)
        monkeypatch.setattr(
            orchestrator.execution_manager_agent,
            'run',
            lambda *_args, **_kwargs: {
                'decision': 'HOLD',
                'should_execute': False,
                'side': None,
                'volume': 0.0,
                'reason': 'No execution for decision=HOLD.',
                'degraded': False,
            },
        )

        completed_run = asyncio.run(orchestrator.execute(db, run, risk_percent=1.0))

        assert completed_run.status == 'completed'
        assert completed_run.error is None


def test_orchestrator_skips_memory_context_search_when_disabled(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db, mode='simulation')
        orchestrator = ForexOrchestrator()

        monkeypatch.setattr(orchestrator.prompt_service, 'seed_defaults', lambda _db: None)
        monkeypatch.setattr(orchestrator.model_selector, 'resolve_memory_context_enabled', lambda *_args, **_kwargs: False)
        monkeypatch.setattr(orchestrator.market_provider, 'get_market_snapshot', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'timeframe': 'H1',
            'last_price': 1.102,
            'atr': 0.001,
            'trend': 'neutral',
        })
        monkeypatch.setattr(orchestrator.market_provider, 'get_news_context', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'news': [],
        })

        search_called = {'value': False}

        def fail_search(**_kwargs):
            search_called['value'] = True
            raise AssertionError('memory_service.search should not be called when memory context is disabled')

        monkeypatch.setattr(orchestrator.memory_service, 'search', fail_search)
        monkeypatch.setattr(orchestrator.memory_service, 'add_run_memory', lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            orchestrator,
            'analyze_context',
            lambda *_args, **_kwargs: {
                'analysis_outputs': {'technical-analyst': {'signal': 'neutral', 'score': 0.0}},
                'bullish': {'arguments': ['No edge'], 'confidence': 0.0},
                'bearish': {'arguments': ['No edge'], 'confidence': 0.0},
                'trader_decision': {
                    'decision': 'HOLD',
                    'entry': 1.102,
                    'stop_loss': None,
                    'take_profit': None,
                    'confidence': 0.1,
                },
                'risk': {
                    'accepted': True,
                    'suggested_volume': 0.0,
                    'reasons': ['No trade requested (HOLD).'],
                },
            },
        )
        monkeypatch.setattr(
            orchestrator.execution_manager_agent,
            'run',
            lambda *_args, **_kwargs: {
                'decision': 'HOLD',
                'should_execute': False,
                'side': None,
                'volume': 0.0,
                'reason': 'No execution for decision=HOLD.',
                'degraded': False,
                'prompt_meta': {'llm_enabled': False, 'skills_count': 0, 'skills': []},
            },
        )

        completed_run = asyncio.run(orchestrator.execute(db, run, risk_percent=1.0))

        assert completed_run.status == 'completed'
        assert search_called['value'] is False
        assert completed_run.trace.get('memory_context') == []
        assert completed_run.trace.get('memory_context_enabled') is False
        assert completed_run.trace.get('memory_signal', {}).get('used') is False


def test_orchestrator_second_pass_can_promote_hold_to_directional_trade(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db, mode='simulation')
        orchestrator = ForexOrchestrator()
        orchestrator.settings.orchestrator_second_pass_enabled = True
        orchestrator.settings.orchestrator_second_pass_max_attempts = 1
        orchestrator.settings.orchestrator_second_pass_min_combined_score = 0.18

        monkeypatch.setattr(orchestrator.prompt_service, 'seed_defaults', lambda _db: None)
        monkeypatch.setattr(orchestrator.market_provider, 'get_market_snapshot', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'timeframe': 'H1',
            'last_price': 1.102,
            'atr': 0.001,
            'trend': 'bullish',
        })
        monkeypatch.setattr(orchestrator.market_provider, 'get_news_context', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'news': [],
        })
        monkeypatch.setattr(orchestrator.memory_service, 'search', lambda **_kwargs: [])
        monkeypatch.setattr(orchestrator.memory_service, 'add_run_memory', lambda *_args, **_kwargs: None)

        calls = {'count': 0}

        def fake_analyze_context(*_args, **_kwargs):
            calls['count'] += 1
            if calls['count'] == 1:
                return {
                    'analysis_outputs': {'technical-analyst': {'signal': 'bullish', 'score': 0.25}},
                    'bullish': {'arguments': ['trend aligns'], 'confidence': 0.7},
                    'bearish': {'arguments': ['minor headwind'], 'confidence': 0.3},
                    'trader_decision': {
                        'decision': 'HOLD',
                        'confidence': 0.42,
                        'combined_score': 0.22,
                        'strong_conflict': False,
                        'needs_follow_up': True,
                        'follow_up_reason': 'insufficient_evidence',
                        'decision_gates': ['insufficient_aligned_sources', 'low_edge'],
                        'execution_allowed': False,
                        'entry': 1.102,
                        'stop_loss': None,
                        'take_profit': None,
                    },
                    'risk': {
                        'accepted': True,
                        'suggested_volume': 0.0,
                        'reasons': ['No trade requested (HOLD).'],
                    },
                }
            return {
                'analysis_outputs': {'technical-analyst': {'signal': 'bullish', 'score': 0.33}},
                'bullish': {'arguments': ['trend aligns strongly'], 'confidence': 0.8},
                'bearish': {'arguments': ['weak bearish case'], 'confidence': 0.1},
                'trader_decision': {
                    'decision': 'BUY',
                    'confidence': 0.67,
                    'combined_score': 0.31,
                    'strong_conflict': False,
                    'needs_follow_up': False,
                    'follow_up_reason': None,
                    'decision_gates': ['technical_neutral_exception'],
                    'execution_allowed': True,
                    'entry': 1.102,
                    'stop_loss': 1.1,
                    'take_profit': 1.106,
                },
                'risk': {
                    'accepted': True,
                    'suggested_volume': 0.2,
                    'reasons': ['Risk checks passed.'],
                },
            }

        monkeypatch.setattr(orchestrator, 'analyze_context', fake_analyze_context)
        monkeypatch.setattr(
            orchestrator.execution_manager_agent,
            'run',
            lambda *_args, **_kwargs: {
                'decision': 'BUY',
                'should_execute': True,
                'side': 'BUY',
                'volume': 0.2,
                'reason': 'Trade eligible based on trader decision + risk checks.',
                'degraded': False,
            },
        )

        async def fake_execute_order(**_kwargs):
            return {'status': 'simulated', 'executed': False, 'reason': 'Simulation mode: order not sent to broker.'}

        monkeypatch.setattr(orchestrator.execution_service, 'execute', fake_execute_order)

        completed_run = asyncio.run(orchestrator.execute(db, run, risk_percent=1.0))

        assert completed_run.status == 'completed'
        assert calls['count'] == 2
        assert completed_run.decision.get('decision') == 'BUY'
        assert completed_run.decision.get('second_pass', {}).get('attempted') is True
        assert completed_run.decision.get('second_pass', {}).get('selected_pass') == 'second'
        assert completed_run.trace.get('second_pass', {}).get('selected_pass') == 'second'


def test_orchestrator_runtime_supervisor_refreshes_memory_before_second_pass(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db, mode='simulation')
        orchestrator = ForexOrchestrator()
        orchestrator.settings.orchestrator_autonomy_enabled = True
        orchestrator.settings.orchestrator_autonomy_max_cycles = 3
        orchestrator.settings.orchestrator_second_pass_enabled = True
        orchestrator.settings.orchestrator_second_pass_max_attempts = 2
        orchestrator.settings.orchestrator_memory_search_limit = 5
        orchestrator.settings.orchestrator_autonomy_memory_limit_step = 2
        orchestrator.settings.orchestrator_autonomy_memory_limit_max = 9

        monkeypatch.setattr(orchestrator.prompt_service, 'seed_defaults', lambda _db: None)
        monkeypatch.setattr(orchestrator.model_selector, 'resolve_memory_context_enabled', lambda *_args, **_kwargs: True)
        monkeypatch.setattr(orchestrator.market_provider, 'get_market_snapshot', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'timeframe': 'H1',
            'last_price': 1.102,
            'atr': 0.001,
            'trend': 'bullish',
        })
        monkeypatch.setattr(orchestrator.market_provider, 'get_news_context', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'news': [],
        })

        search_limits: list[int] = []

        def fake_search(**kwargs):
            limit = int(kwargs.get('limit', 0) or 0)
            search_limits.append(limit)
            return [{'summary': f'memory-{index + 1}', 'score': 0.5} for index in range(min(limit, 3))]

        monkeypatch.setattr(orchestrator.memory_service, 'search', fake_search)
        monkeypatch.setattr(orchestrator.memory_service, 'add_run_memory', lambda *_args, **_kwargs: None)

        calls = {'count': 0}

        def fake_analyze_context(*_args, **kwargs):
            calls['count'] += 1
            local_context = kwargs.get('context')
            memory_items = list(getattr(local_context, 'memory_context', []))
            if calls['count'] == 1:
                return {
                    'analysis_outputs': {'technical-analyst': {'signal': 'bullish', 'score': 0.24}},
                    'bullish': {'arguments': ['trend aligns'], 'confidence': 0.62},
                    'bearish': {'arguments': ['counter move possible'], 'confidence': 0.31},
                    'trader_decision': {
                        'decision': 'HOLD',
                        'confidence': 0.43,
                        'combined_score': 0.24,
                        'strong_conflict': False,
                        'needs_follow_up': True,
                        'follow_up_reason': 'insufficient_evidence',
                        'decision_gates': ['insufficient_aligned_sources'],
                        'execution_allowed': False,
                        'evidence_strength': 0.31,
                        'entry': 1.102,
                        'stop_loss': None,
                        'take_profit': None,
                        'memory_count': len(memory_items),
                    },
                    'risk': {
                        'accepted': True,
                        'suggested_volume': 0.0,
                        'reasons': ['No trade requested (HOLD).'],
                    },
                }

            return {
                'analysis_outputs': {'technical-analyst': {'signal': 'bullish', 'score': 0.36}},
                'bullish': {'arguments': ['trend aligns strongly'], 'confidence': 0.79},
                'bearish': {'arguments': ['bearish case weakens'], 'confidence': 0.12},
                'trader_decision': {
                    'decision': 'BUY',
                    'confidence': 0.71,
                    'combined_score': 0.33,
                    'strong_conflict': False,
                    'needs_follow_up': False,
                    'follow_up_reason': None,
                    'decision_gates': ['technical_neutral_exception'],
                    'execution_allowed': True,
                    'evidence_strength': 0.58,
                    'entry': 1.102,
                    'stop_loss': 1.1,
                    'take_profit': 1.106,
                    'memory_count': len(memory_items),
                },
                'risk': {
                    'accepted': True,
                    'suggested_volume': 0.2,
                    'reasons': ['Risk checks passed.'],
                },
            }

        monkeypatch.setattr(orchestrator, 'analyze_context', fake_analyze_context)
        monkeypatch.setattr(
            orchestrator.execution_manager_agent,
            'run',
            lambda *_args, **_kwargs: {
                'decision': 'BUY',
                'should_execute': False,
                'side': 'BUY',
                'volume': 0.2,
                'reason': 'Execution disabled in test.',
                'degraded': False,
            },
        )

        completed_run = asyncio.run(orchestrator.execute(db, run, risk_percent=1.0))

        assert completed_run.status == 'completed'
        assert calls['count'] == 2
        assert search_limits == [5, 7]
        assert completed_run.decision.get('decision') == 'BUY'
        assert completed_run.decision.get('second_pass', {}).get('selected_pass') == 'second'
        runtime_supervisor = completed_run.decision.get('runtime_supervisor', {})
        assert runtime_supervisor.get('executed_cycles') == 2
        assert runtime_supervisor.get('selected_cycle') == 2
        assert runtime_supervisor.get('cycles', [])[0].get('action') == 'rerun_with_memory_refresh'


def test_orchestrator_runtime_supervisor_stops_on_stagnation_guardrail(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        run = _seed_run(db, mode='simulation')
        orchestrator = ForexOrchestrator()
        orchestrator.settings.orchestrator_autonomy_enabled = True
        orchestrator.settings.orchestrator_autonomy_max_cycles = 4
        orchestrator.settings.orchestrator_second_pass_enabled = True
        orchestrator.settings.orchestrator_second_pass_max_attempts = 3

        monkeypatch.setattr(orchestrator.prompt_service, 'seed_defaults', lambda _db: None)
        monkeypatch.setattr(orchestrator.market_provider, 'get_market_snapshot', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'timeframe': 'H1',
            'last_price': 1.102,
            'atr': 0.001,
            'trend': 'neutral',
        })
        monkeypatch.setattr(orchestrator.market_provider, 'get_news_context', lambda *_args, **_kwargs: {
            'degraded': False,
            'pair': 'EURUSD',
            'news': [],
        })
        monkeypatch.setattr(orchestrator.memory_service, 'search', lambda **_kwargs: [])
        monkeypatch.setattr(orchestrator.memory_service, 'add_run_memory', lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            orchestrator,
            'analyze_context',
            lambda *_args, **_kwargs: {
                'analysis_outputs': {'technical-analyst': {'signal': 'neutral', 'score': 0.0}},
                'bullish': {'arguments': ['weak bullish signal'], 'confidence': 0.40},
                'bearish': {'arguments': ['weak bearish signal'], 'confidence': 0.41},
                'trader_decision': {
                    'decision': 'HOLD',
                    'confidence': 0.39,
                    'combined_score': 0.21,
                    'strong_conflict': True,
                    'needs_follow_up': True,
                    'follow_up_reason': 'strong_conflict',
                    'decision_gates': ['strong_conflict', 'low_edge'],
                    'execution_allowed': False,
                    'evidence_strength': 0.33,
                    'entry': 1.102,
                    'stop_loss': None,
                    'take_profit': None,
                },
                'risk': {
                    'accepted': True,
                    'suggested_volume': 0.0,
                    'reasons': ['No trade requested (HOLD).'],
                },
            },
        )
        monkeypatch.setattr(
            orchestrator.execution_manager_agent,
            'run',
            lambda *_args, **_kwargs: {
                'decision': 'HOLD',
                'should_execute': False,
                'side': None,
                'volume': 0.0,
                'reason': 'No execution for HOLD.',
                'degraded': False,
            },
        )

        completed_run = asyncio.run(orchestrator.execute(db, run, risk_percent=1.0))

        assert completed_run.status == 'completed'
        runtime_supervisor = completed_run.decision.get('runtime_supervisor', {})
        assert runtime_supervisor.get('executed_cycles') == 2
        cycles = runtime_supervisor.get('cycles', [])
        assert cycles[1].get('action_reason') == 'stagnation_guardrail'
        assert completed_run.decision.get('second_pass', {}).get('attempt_count') == 1
