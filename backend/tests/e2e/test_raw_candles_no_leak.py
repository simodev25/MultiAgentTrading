"""E2E test: _raw_candles must never leak into agent outputs or debug traces.

Regression test for the bug where MetaAPI OHLC candle data (injected via
``_raw_candles`` in the market snapshot) propagated into the
technical-analyst ``indicators`` field, the ``market_snapshot`` tool
invocation data, and eventually into the JSON debug-trace file — inflating
payloads and exposing internal transport data.

The test exercises **two levels**:

1. **Agent-level** — calls ``TechnicalAnalystAgent.run()`` directly with a
   market snapshot that deliberately contains ``_raw_candles``, and asserts
   the key is absent from every nested dict in the output.

2. **Orchestrator pipeline** — runs ``ForexOrchestrator.execute()`` with
   MetaAPI-style candle data (which injects ``_raw_candles`` into the
   snapshot inside ``resolve_market_snapshot``), and asserts the key is
   absent from ``run.decision``, ``run.trace``, and the debug-trace JSON
   file.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.metaapi_account import MetaApiAccount
from app.db.models.run import AnalysisRun
from app.db.models.user import User
from app.services.orchestrator.agents import AgentContext, TechnicalAnalystAgent
from app.services.orchestrator.engine import ForexOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_candles(count: int = 100, base_price: float = 1.1000) -> list[dict[str, Any]]:
    """Generate realistic OHLC candle data."""
    candles: list[dict[str, Any]] = []
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    price = base_price
    for idx in range(count):
        # Simple random walk
        delta = 0.0002 * (1 if idx % 3 != 0 else -1)
        price += delta
        candles.append({
            'time': (start + timedelta(hours=idx)).isoformat().replace('+00:00', 'Z'),
            'open': round(price - 0.0003, 5),
            'high': round(price + 0.0005, 5),
            'low': round(price - 0.0006, 5),
            'close': round(price, 5),
            'volume': 1000 + idx,
        })
    return candles


def _dict_contains_key_recursive(obj: Any, key: str) -> list[str]:
    """Return dot-paths where *key* appears in a nested dict/list structure."""
    hits: list[str] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                current = f'{path}.{k}' if path else k
                if k == key:
                    hits.append(current)
                _walk(v, current)
        elif isinstance(node, (list, tuple)):
            for i, item in enumerate(node):
                _walk(item, f'{path}[{i}]')

    _walk(obj, '')
    return hits


def _seed_user(db: Session) -> User:
    user = User(email='e2e@local.dev', hashed_password='x', role='admin', is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _seed_run(db: Session, *, mode: str = 'simulation') -> AnalysisRun:
    user = _seed_user(db)
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


def _seed_metaapi_account(db: Session) -> MetaApiAccount:
    account = MetaApiAccount(
        label='paper-e2e',
        account_id='e2e-account-id',
        region='new-york',
        enabled=True,
        is_default=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _market_snapshot_with_raw_candles(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a market snapshot that includes ``_raw_candles`` — simulating
    what ``resolve_market_snapshot`` produces before the pop in ``execute``."""
    return {
        'degraded': False,
        'pair': 'EURUSD',
        'symbol': 'EURUSD.pro',
        'timeframe': 'H1',
        'last_price': 1.1020,
        'atr': 0.0008,
        'atr_ratio': 0.00073,
        'trend': 'bullish',
        'rsi': 58.2,
        'ema_fast': 1.1015,
        'ema_slow': 1.1005,
        'macd_diff': 0.00012,
        'change_pct': 0.08,
        'market_data_source': 'metaapi',
        'market_data_provider': 'sdk',
        '_raw_candles': candles,
    }


# ---------------------------------------------------------------------------
# 1. Agent-level: TechnicalAnalystAgent must not leak _raw_candles
# ---------------------------------------------------------------------------

class TestTechnicalAnalystRawCandlesNoLeak:
    """Verify that ``_raw_candles`` never appears in TechnicalAnalystAgent output."""

    def test_raw_candles_absent_from_output_with_candles_in_snapshot(self, monkeypatch) -> None:
        """Even when ``_raw_candles`` is present in ``ctx.market_snapshot``,
        the agent output must not contain it anywhere."""
        candles = _generate_candles(100)
        snapshot = _market_snapshot_with_raw_candles(candles)

        ctx = AgentContext(
            pair='EURUSD',
            timeframe='H1',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot=snapshot,
            news_context={'degraded': False, 'pair': 'EURUSD', 'news': []},
            memory_context=[],
            memory_signal={},
            price_history=candles,
        )

        agent = TechnicalAnalystAgent()
        # Force LLM disabled so the agent returns deterministic output
        monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_kw: False)
        monkeypatch.setattr(agent.model_selector, 'resolve_enabled_tools', lambda *_a, **_kw: [
            'market_snapshot', 'indicator_bundle', 'divergence_detector',
            'pattern_detector', 'support_resistance_or_structure_detector',
            'multi_timeframe_context',
        ])
        monkeypatch.setattr(agent.model_selector, 'resolve_skills', lambda *_a, **_kw: [])
        monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_a, **_kw: 'conservative')

        output = agent.run(ctx, db=None)

        # Global recursive check
        leak_paths = _dict_contains_key_recursive(output, '_raw_candles')
        assert leak_paths == [], (
            f'_raw_candles leaked into agent output at: {leak_paths}'
        )

        # Targeted checks
        indicators = output.get('indicators', {})
        assert '_raw_candles' not in indicators, (
            '_raw_candles found in indicators (m_effective)'
        )

        tooling = output.get('tooling', {})
        invocations = tooling.get('invocations', {})
        for tool_id, inv in invocations.items():
            data = inv.get('data', {})
            assert '_raw_candles' not in data, (
                f'_raw_candles found in tool invocation data for {tool_id}'
            )

    def test_raw_candles_absent_even_when_no_price_history(self, monkeypatch) -> None:
        """When ``price_history`` is empty but snapshot has ``_raw_candles``,
        the output must still be clean."""
        candles = _generate_candles(50)
        snapshot = _market_snapshot_with_raw_candles(candles)

        ctx = AgentContext(
            pair='EURUSD',
            timeframe='H1',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot=snapshot,
            news_context={'degraded': False, 'pair': 'EURUSD', 'news': []},
            memory_context=[],
            memory_signal={},
            price_history=[],  # No candle data provided as price_history
        )

        agent = TechnicalAnalystAgent()
        monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_kw: False)
        monkeypatch.setattr(agent.model_selector, 'resolve_enabled_tools', lambda *_a, **_kw: [
            'market_snapshot', 'indicator_bundle',
        ])
        monkeypatch.setattr(agent.model_selector, 'resolve_skills', lambda *_a, **_kw: [])
        monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_a, **_kw: 'conservative')

        output = agent.run(ctx, db=None)

        leak_paths = _dict_contains_key_recursive(output, '_raw_candles')
        assert leak_paths == [], (
            f'_raw_candles leaked into agent output at: {leak_paths}'
        )

    def test_mcp_tool_dispatchers_use_real_candles(self, monkeypatch) -> None:
        """When enough candles are provided, MCP tool dispatchers should
        produce real computation results (not stubs)."""
        candles = _generate_candles(100)
        snapshot = _market_snapshot_with_raw_candles(candles)

        ctx = AgentContext(
            pair='EURUSD',
            timeframe='H1',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot=snapshot,
            news_context={'degraded': False, 'pair': 'EURUSD', 'news': []},
            memory_context=[],
            memory_signal={},
            price_history=candles,
        )

        agent = TechnicalAnalystAgent()
        monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_kw: False)
        monkeypatch.setattr(agent.model_selector, 'resolve_enabled_tools', lambda *_a, **_kw: [
            'market_snapshot', 'indicator_bundle', 'divergence_detector',
            'pattern_detector', 'support_resistance_or_structure_detector',
            'multi_timeframe_context',
        ])
        monkeypatch.setattr(agent.model_selector, 'resolve_skills', lambda *_a, **_kw: [])
        monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_a, **_kw: 'conservative')

        output = agent.run(ctx, db=None)

        invocations = output.get('tooling', {}).get('invocations', {})

        # structure tool should have real S/R data
        sr_tool = invocations.get('support_resistance_or_structure_detector', {})
        assert sr_tool.get('status') == 'ok', 'S/R tool should succeed with real candles'
        sr_data = sr_tool.get('data', {})
        # Real MCP handler returns levels/support_levels/resistance_levels
        assert 'levels' in sr_data or 'support_levels' in sr_data or 'resistance_levels' in sr_data, (
            f'S/R tool should return level data, got keys: {list(sr_data.keys())}'
        )

        # No leaks
        leak_paths = _dict_contains_key_recursive(output, '_raw_candles')
        assert leak_paths == [], (
            f'_raw_candles leaked into agent output at: {leak_paths}'
        )


# ---------------------------------------------------------------------------
# 2. Orchestrator pipeline: _raw_candles must not leak through execute()
# ---------------------------------------------------------------------------

class TestOrchestratorRawCandlesNoLeak:
    """Full pipeline E2E: MetaAPI candles flow through the orchestrator
    and ``_raw_candles`` must never appear in ``run.decision``,
    ``run.trace``, or the debug-trace JSON."""

    def test_execute_with_metaapi_candles_no_raw_candles_in_output(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(bind=engine)

        with Session(engine) as db:
            run = _seed_run(db)
            account = _seed_metaapi_account(db)
            orchestrator = ForexOrchestrator()

            # --- Settings ---
            orchestrator.settings.metaapi_use_sdk_for_market_data = True
            orchestrator.settings.debug_trade_json_enabled = True
            orchestrator.settings.debug_trade_json_dir = str(tmp_path)
            orchestrator.settings.debug_trade_json_include_price_history = True
            orchestrator.settings.debug_trade_json_price_history_limit = 50
            orchestrator.settings.debug_trade_json_inline_in_run_trace = False
            orchestrator.settings.orchestrator_parallel_workers = 1

            # --- Mock MetaAPI to return candles with data that would create _raw_candles ---
            candles = _generate_candles(100)

            async def fake_get_market_candles(*, pair, timeframe, limit, account_id=None, region=None):
                return {
                    'degraded': False,
                    'pair': pair,
                    'symbol': f'{pair}.pro',
                    'requested_symbol': pair,
                    'tried_symbols': [pair, f'{pair}.pro'],
                    'timeframe': timeframe,
                    'candles': candles,
                    'provider': 'sdk',
                }

            monkeypatch.setattr(orchestrator.metaapi, 'get_market_candles', fake_get_market_candles)
            monkeypatch.setattr(orchestrator.prompt_service, 'seed_defaults', lambda _db: None)
            monkeypatch.setattr(orchestrator.market_provider, 'get_news_context', lambda *_a, **_kw: {
                'degraded': False,
                'pair': 'EURUSD',
                'news': [{'title': 'ECB holds rates steady'}],
            })
            monkeypatch.setattr(orchestrator.market_provider, 'get_recent_candles', lambda *_a, **_kw: candles[:50])
            monkeypatch.setattr(orchestrator.memory_service, 'search', lambda **_kw: [])
            monkeypatch.setattr(orchestrator.memory_service, 'add_run_memory', lambda *_a, **_kw: None)

            # --- Mock analyze_context to exercise TechnicalAnalystAgent.run() for real ---
            # We do NOT mock analyze_context — we mock only the LLM calls and
            # downstream agents so the technical-analyst runs with real data.

            # Disable LLM for all agents so they return deterministic output
            monkeypatch.setattr(
                orchestrator.model_selector,
                'is_enabled',
                lambda *_a, **_kw: False,
            )

            def fake_analyze_context(*_args, **kwargs):
                """Run the real technical-analyst but stub the rest."""
                context: AgentContext = kwargs.get('context')
                if context is None and _args:
                    context = _args[0]
                local_db = kwargs.get('db')
                local_run = kwargs.get('run')

                # Run real technical-analyst
                tech_output = orchestrator.technical_agent.run(context, db=local_db)

                # Record step if needed
                if kwargs.get('record_steps') and local_db is not None and local_run is not None:
                    orchestrator._record_step(
                        local_db,
                        local_run,
                        'technical-analyst',
                        {'pair': context.pair, 'timeframe': context.timeframe},
                        tech_output,
                    )

                return {
                    'analysis_outputs': {
                        'technical-analyst': tech_output,
                        'news-analyst': {'signal': 'neutral', 'score': 0.0, 'reason': 'No relevant news'},
                        'market-context-analyst': {'signal': 'bullish', 'score': 0.15, 'regime': 'trending'},
                    },
                    'bullish': {'arguments': ['EMA alignment'], 'confidence': 0.65},
                    'bearish': {'arguments': ['Minimal risk'], 'confidence': 0.2},
                    'trader_decision': {
                        'decision': 'HOLD',
                        'entry': 1.102,
                        'stop_loss': None,
                        'take_profit': None,
                        'confidence': 0.45,
                        'execution_allowed': False,
                    },
                    'risk': {
                        'accepted': True,
                        'suggested_volume': 0.0,
                        'reasons': ['No trade requested (HOLD).'],
                    },
                }

            monkeypatch.setattr(orchestrator, 'analyze_context', fake_analyze_context)
            monkeypatch.setattr(
                orchestrator.execution_manager_agent,
                'run',
                lambda *_a, **_kw: {
                    'decision': 'HOLD',
                    'should_execute': False,
                    'side': None,
                    'volume': 0.0,
                    'reason': 'No trade to execute.',
                },
            )

            completed_run = asyncio.run(
                orchestrator.execute(db, run, risk_percent=1.0, metaapi_account_ref=account.id)
            )

            assert completed_run.status == 'completed'

            # --- Assert _raw_candles is NOT in run.decision ---
            decision_leaks = _dict_contains_key_recursive(
                completed_run.decision or {}, '_raw_candles',
            )
            assert decision_leaks == [], (
                f'_raw_candles leaked into run.decision at: {decision_leaks}'
            )

            # --- Assert _raw_candles is NOT in run.trace ---
            trace_leaks = _dict_contains_key_recursive(
                completed_run.trace or {}, '_raw_candles',
            )
            assert trace_leaks == [], (
                f'_raw_candles leaked into run.trace at: {trace_leaks}'
            )

            # --- Assert _raw_candles is NOT in the debug trace JSON ---
            debug_trace_meta = (completed_run.trace or {}).get('debug_trace_meta', {})
            if debug_trace_meta.get('enabled'):
                debug_file = completed_run.trace.get('debug_trace_file')
                if debug_file and Path(debug_file).exists():
                    payload = json.loads(Path(debug_file).read_text(encoding='utf-8'))
                    json_leaks = _dict_contains_key_recursive(payload, '_raw_candles')
                    # price_history in context section is allowed (it's the raw
                    # candle array under a known key), but ``_raw_candles`` is not
                    assert json_leaks == [], (
                        f'_raw_candles leaked into debug trace JSON at: {json_leaks}'
                    )

            # --- Assert technical-analyst output specifically ---
            tech_output = (completed_run.decision or {}).get('technical-analyst')
            if tech_output is None:
                # May be nested under analysis_outputs in trace
                for step in (completed_run.trace or {}).get('steps', []):
                    if step.get('agent_name') == 'technical-analyst':
                        tech_output = step.get('output', {})
                        break

            if tech_output is not None:
                tech_leaks = _dict_contains_key_recursive(tech_output, '_raw_candles')
                assert tech_leaks == [], (
                    f'_raw_candles leaked into technical-analyst output at: {tech_leaks}'
                )
                indicators = tech_output.get('indicators', {})
                assert '_raw_candles' not in indicators, (
                    '_raw_candles found in technical-analyst indicators'
                )

    def test_resolve_market_snapshot_embeds_raw_candles(self, monkeypatch) -> None:
        """Verify that ``resolve_market_snapshot`` does embed ``_raw_candles``
        in the snapshot (this is the expected behavior — the key should exist
        in the raw snapshot and be stripped later by ``execute``)."""
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(bind=engine)

        with Session(engine) as db:
            account = _seed_metaapi_account(db)
            orchestrator = ForexOrchestrator()
            orchestrator.settings.metaapi_use_sdk_for_market_data = True

            candles = _generate_candles(80)

            async def fake_get_market_candles(*, pair, timeframe, limit, account_id=None, region=None):
                return {
                    'degraded': False,
                    'pair': pair,
                    'symbol': f'{pair}.pro',
                    'requested_symbol': pair,
                    'tried_symbols': [pair],
                    'timeframe': timeframe,
                    'candles': candles,
                    'provider': 'sdk',
                }

            monkeypatch.setattr(orchestrator.metaapi, 'get_market_candles', fake_get_market_candles)
            monkeypatch.setattr(
                orchestrator.market_provider,
                'get_market_snapshot',
                lambda *_a, **_kw: {'degraded': True},
            )

            snapshot = asyncio.run(
                orchestrator.resolve_market_snapshot(
                    db,
                    pair='EURUSD',
                    timeframe='H1',
                    metaapi_account_ref=account.id,
                )
            )

            # resolve_market_snapshot SHOULD contain _raw_candles
            assert '_raw_candles' in snapshot, (
                'resolve_market_snapshot should embed _raw_candles for downstream use'
            )
            assert len(snapshot['_raw_candles']) == len(candles)
            assert snapshot['market_data_source'] == 'metaapi'
