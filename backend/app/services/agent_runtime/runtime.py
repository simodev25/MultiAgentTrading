from __future__ import annotations

import logging
import time
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.run import AnalysisRun
from app.observability.metrics import orchestrator_step_duration_seconds
from app.services.agent_runtime.constants import AGENTIC_V2_RUNTIME
from app.services.agent_runtime.models import RuntimeSessionState
from app.services.agent_runtime.planner import AgenticRuntimePlanner
from app.services.agent_runtime.session_store import RuntimeSessionStore
from app.services.agent_runtime.tool_registry import RuntimeToolRegistry
from app.services.orchestrator.agents import AgentContext
from app.services.orchestrator.engine import ForexOrchestrator

logger = logging.getLogger(__name__)


class AgenticTradingRuntime:
    PLAN = (
        'resolve_market_context',
        'load_memory_context',
        'run_technical_analyst',
        'run_news_analyst',
        'run_market_context_analyst',
        'run_bullish_researcher',
        'run_bearish_researcher',
        'run_trader_agent',
        'run_risk_manager',
        'run_execution_manager',
    )

    def __init__(self) -> None:
        self.settings = get_settings()
        self.orchestrator = ForexOrchestrator()
        self.planner = AgenticRuntimePlanner(self.orchestrator.prompt_service)
        self.session_store = RuntimeSessionStore(
            event_limit=self.settings.agentic_runtime_event_limit,
            history_limit=self.settings.agentic_runtime_history_limit,
        )
        self.registry = RuntimeToolRegistry()
        self._register_tools()

    def _register_tools(self) -> None:
        self.registry.register(
            'resolve_market_context',
            self._tool_resolve_market_context,
            description='Load market snapshot and news context for the run.',
            section='context',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'load_memory_context',
            self._tool_load_memory_context,
            description='Retrieve vector and semantic memory context for the run.',
            section='memory',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'refresh_memory_context',
            self._tool_refresh_memory_context,
            description='Expand memory recall and reset downstream analysis artifacts.',
            section='memory',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'spawn_subagent',
            self._tool_spawn_subagent,
            description='Spawn an isolated specialist session and collect its completion.',
            section='sessions',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'sessions_spawn',
            self._tool_sessions_spawn,
            description='Spawn a persistent child session and run a specialist task.',
            section='sessions',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'sessions_resume',
            self._tool_sessions_resume,
            description='Resume an existing child session by session key.',
            section='sessions',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'session_status',
            self._tool_session_status,
            description='Inspect one runtime session state.',
            section='sessions',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'sessions_list',
            self._tool_sessions_list,
            description='List runtime sessions for the current run.',
            section='sessions',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'sessions_send',
            self._tool_sessions_send,
            description='Send a message to a runtime child session and optionally resume it.',
            section='sessions',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'sessions_history',
            self._tool_sessions_history,
            description='Read dedicated message history for a runtime session.',
            section='sessions',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'run_technical_analyst',
            self._tool_run_technical_analyst,
            description='Run the technical analyst specialist.',
            section='analysis',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'run_news_analyst',
            self._tool_run_news_analyst,
            description='Run the news analyst specialist.',
            section='analysis',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'run_market_context_analyst',
            self._tool_run_market_context_analyst,
            description='Run the market-context analyst specialist.',
            section='analysis',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'run_bullish_researcher',
            self._tool_run_bullish_researcher,
            description='Build the bullish debate package.',
            section='debate',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'run_bearish_researcher',
            self._tool_run_bearish_researcher,
            description='Build the bearish debate package.',
            section='debate',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'run_trader_agent',
            self._tool_run_trader_agent,
            description='Produce the final trading decision.',
            section='decision',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'run_risk_manager',
            self._tool_run_risk_manager,
            description='Apply deterministic risk validation.',
            section='risk',
            profiles=('agentic_v2',),
        )
        self.registry.register(
            'run_execution_manager',
            self._tool_run_execution_manager,
            description='Prepare execution and optionally submit the order.',
            section='execution',
            profiles=('agentic_v2',),
        )
        self.registry.set_policy(allow=['*'], deny=[])

    def _build_objective(
        self,
        *,
        run: AnalysisRun,
        risk_percent: float,
        metaapi_account_ref: int | None,
    ) -> dict[str, Any]:
        return {
            'kind': 'trade-analysis',
            'pair': run.pair,
            'timeframe': run.timeframe,
            'mode': run.mode,
            'risk_percent': risk_percent,
            'metaapi_account_ref': metaapi_account_ref,
        }

    def _build_context(self, state: RuntimeSessionState, *, run: AnalysisRun, risk_percent: float) -> AgentContext:
        return AgentContext(
            pair=run.pair,
            timeframe=run.timeframe,
            mode=run.mode,
            risk_percent=risk_percent,
            market_snapshot=state.context.get('market') if isinstance(state.context.get('market'), dict) else {},
            news_context=state.context.get('news') if isinstance(state.context.get('news'), dict) else {},
            memory_context=state.context.get('memory_context') if isinstance(state.context.get('memory_context'), list) else [],
            memory_signal=state.context.get('memory_signal') if isinstance(state.context.get('memory_signal'), dict) else {},
            llm_model_overrides={},
        )

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return ForexOrchestrator._json_safe(value)

    def _compact_payload(self, value: Any) -> Any:
        safe = self._json_safe(value)
        if isinstance(safe, dict):
            compact: dict[str, Any] = {}
            for key in (
                'signal',
                'score',
                'decision',
                'confidence',
                'accepted',
                'suggested_volume',
                'should_execute',
                'side',
                'volume',
                'status',
                'reason',
                'coverage',
                'degraded',
            ):
                if key in safe:
                    compact[key] = safe[key]
            if compact:
                return compact
        if isinstance(safe, list):
            return safe[:5]
        return safe

    def _record_agent_step(
        self,
        db: Session,
        run: AnalysisRun,
        *,
        agent_name: str,
        input_payload: dict[str, Any],
        fn: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        output = fn()
        orchestrator_step_duration_seconds.labels(agent=agent_name).observe(time.perf_counter() - started)
        self.orchestrator._record_step(db, run, agent_name, input_payload, output)
        return output

    @staticmethod
    def _analysis_outputs(state: RuntimeSessionState) -> dict[str, dict[str, Any]]:
        outputs = state.artifacts.get('analysis_outputs')
        return outputs if isinstance(outputs, dict) else {}

    def _record_history(self, state: RuntimeSessionState, *, tool_name: str, result: dict[str, Any]) -> None:
        state.history.append(
            {
                'turn': state.turn,
                'tool': tool_name,
                'summary': self._compact_payload(result),
            }
        )
        state.history = state.history[-40:]

    def _build_subagent_objective(
        self,
        *,
        run: AnalysisRun,
        kind: str,
        label: str,
        source_tool: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            'kind': kind,
            'label': label,
            'source_tool': source_tool,
            'pair': run.pair,
            'timeframe': run.timeframe,
            'mode': run.mode,
            **(extra or {}),
        }

    async def _run_specialist_subagent(
        self,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        *,
        name: str,
        label: str,
        source_tool: str,
        objective: dict[str, Any],
        input_payload: dict[str, Any],
        fn: Callable[[], dict[str, Any]],
        session_key: str | None = None,
        session_mode: str = 'session',
    ) -> dict[str, Any]:
        root_session_key = self.session_store.root_session_key(run)
        resumed = bool(str(session_key or '').strip())
        session_metadata = {
            'agent_name': name,
            'input': self._compact_payload(input_payload),
        }
        if resumed:
            child_session = self.session_store.reopen_subagent_session(
                db,
                run,
                session_key=str(session_key or '').strip(),
                metadata=session_metadata,
            )
            if child_session is None:
                raise RuntimeError(f'Unknown subagent session: {session_key}')
        else:
            child_session = self.session_store.create_subagent_session(
                db,
                run,
                parent_session_key=root_session_key,
                name=name,
                label=label,
                objective=objective,
                source_tool=source_tool,
                depth=1,
                mode=session_mode,
                metadata=session_metadata,
            )
        child_session_key = str(child_session.get('session_key') or '').strip()
        started_at_ms = int(time.time() * 1000)

        self.session_store.append_event(
            db,
            run,
            state=state,
            event_stream='sessions',
            name='subagent_resumed' if resumed else 'subagent_spawned',
            payload={
                'phase': 'resume' if resumed else 'start',
                'childSessionKey': child_session_key,
                'parentSessionKey': root_session_key,
                'label': label,
                'sourceTool': source_tool,
                'objective': objective,
            },
        )
        self.session_store.append_event(
            db,
            run,
            state=state,
            event_stream='lifecycle',
            name='resumed' if resumed else 'started',
            session_key=child_session_key,
            payload={
                'phase': 'resume' if resumed else 'start',
                'startedAt': started_at_ms,
                'label': label,
                'objective': objective,
                'parentSessionKey': root_session_key,
                'sourceTool': source_tool,
            },
        )
        self.session_store.append_session_message(
            db,
            run,
            session_key=child_session_key,
            role='system',
            content=f'{label} {"resumed" if resumed else "started"}.',
            sender_session_key=root_session_key,
            metadata={
                'phase': 'resume' if resumed else 'start',
                'source_tool': source_tool,
            },
        )

        try:
            output = fn()
            summary = self._compact_payload(output)
            self.session_store.append_session_message(
                db,
                run,
                session_key=child_session_key,
                role='assistant',
                content=str(summary),
                sender_session_key=child_session_key,
                metadata={
                    'phase': 'message',
                    'source_tool': source_tool,
                },
            )
            self.session_store.append_event(
                db,
                run,
                state=state,
                event_stream='assistant',
                name=name,
                session_key=child_session_key,
                payload={
                    'phase': 'message',
                    'summary': summary,
                },
            )
            self.session_store.finalize_subagent_session(
                db,
                run,
                session_key=child_session_key,
                status='completed',
                summary=summary if isinstance(summary, dict) else {'value': summary},
            )
            self.session_store.append_event(
                db,
                run,
                state=state,
                event_stream='lifecycle',
                name='completed',
                session_key=child_session_key,
                payload={
                    'phase': 'end',
                    'startedAt': started_at_ms,
                    'endedAt': int(time.time() * 1000),
                    'summary': summary,
                },
            )
            self.session_store.append_event(
                db,
                run,
                state=state,
                event_stream='sessions',
                name='subagent_completed',
                payload={
                    'phase': 'end',
                    'childSessionKey': child_session_key,
                    'parentSessionKey': root_session_key,
                    'label': label,
                    'sourceTool': source_tool,
                    'summary': summary,
                    'resumed': resumed,
                },
            )
            return output
        except Exception as exc:
            self.session_store.finalize_subagent_session(
                db,
                run,
                session_key=child_session_key,
                status='failed',
                error=str(exc),
            )
            self.session_store.append_event(
                db,
                run,
                state=state,
                event_stream='lifecycle',
                name='failed',
                session_key=child_session_key,
                payload={
                    'phase': 'error',
                    'startedAt': started_at_ms,
                    'endedAt': int(time.time() * 1000),
                    'error': str(exc),
                },
                runtime_status='failed',
            )
            self.session_store.append_event(
                db,
                run,
                state=state,
                event_stream='sessions',
                name='subagent_failed',
                payload={
                    'phase': 'error',
                    'childSessionKey': child_session_key,
                    'parentSessionKey': root_session_key,
                    'label': label,
                    'sourceTool': source_tool,
                    'error': str(exc),
                    'resumed': resumed,
                },
                runtime_status='failed',
            )
            raise

    def _should_refresh_memory(self, state: RuntimeSessionState) -> bool:
        trader_decision = state.artifacts.get('trader_decision')
        if not isinstance(trader_decision, dict):
            return False
        if str(trader_decision.get('decision') or '').strip().upper() != 'HOLD':
            return False
        if not bool(trader_decision.get('needs_follow_up', False)):
            return False
        if not bool(state.context.get('memory_context_enabled', False)):
            return False

        refresh_count = int(state.context.get('memory_refresh_count', 0) or 0)
        max_limit = max(int(self.settings.orchestrator_autonomy_memory_limit_max), 1)
        current_limit = int(state.context.get('memory_limit', 0) or 0)
        if refresh_count >= 1:
            return False
        if current_limit >= max_limit:
            return False
        return True

    def _candidate_tools(self, state: RuntimeSessionState) -> list[str]:
        if not isinstance(state.context.get('market'), dict) or not isinstance(state.context.get('news'), dict):
            return ['resolve_market_context']
        if 'memory_context' not in state.context:
            return ['load_memory_context']

        analysis_outputs = self._analysis_outputs(state)
        analysis_candidates: list[str] = []
        if 'technical-analyst' not in analysis_outputs:
            analysis_candidates.append('run_technical_analyst')
        if 'news-analyst' not in analysis_outputs:
            analysis_candidates.append('run_news_analyst')
        if 'market-context-analyst' not in analysis_outputs:
            analysis_candidates.append('run_market_context_analyst')
        if analysis_candidates:
            return analysis_candidates
        if not isinstance(state.artifacts.get('bullish'), dict):
            return ['run_bullish_researcher']
        if not isinstance(state.artifacts.get('bearish'), dict):
            return ['run_bearish_researcher']
        if not isinstance(state.artifacts.get('trader_decision'), dict):
            return ['run_trader_agent']
        if not isinstance(state.artifacts.get('risk'), dict):
            return ['run_risk_manager']
        if self._should_refresh_memory(state):
            return ['refresh_memory_context']
        if not isinstance(state.artifacts.get('execution_manager'), dict):
            return ['run_execution_manager']
        return []

    def _next_tool(self, state: RuntimeSessionState) -> str | None:
        candidates = self._candidate_tools(state)
        return candidates[0] if candidates else None

    def _candidate_tool_payloads(self, candidate_tools: list[str]) -> list[dict[str, Any]]:
        catalog = {str(item.get('name') or '').strip(): item for item in self.registry.list_tools()}
        payloads: list[dict[str, Any]] = []
        for tool_name in candidate_tools:
            item = catalog.get(tool_name, {})
            payloads.append(
                {
                    'name': tool_name,
                    'description': item.get('description', ''),
                    'section': item.get('section', ''),
                    'profiles': item.get('profiles', []),
                }
            )
        return payloads

    def _select_next_tool(
        self,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
    ) -> str | None:
        candidate_tools = self._candidate_tools(state)
        if not candidate_tools:
            return None

        candidate_payloads = self._candidate_tool_payloads(candidate_tools)
        decision = self.planner.choose_tool(
            db=db,
            state=state,
            candidate_tools=candidate_payloads,
        )
        selected_tool = decision.tool_name if decision.tool_name in candidate_tools else candidate_tools[0]
        state.notes.append(f'Planner[{decision.source}] -> {selected_tool}: {decision.reason}')
        state.notes = state.notes[-40:]
        self.session_store.append_event(
            db,
            run,
            state=state,
            event_stream='assistant',
            name='agentic-runtime-planner',
            payload={
                'phase': 'plan',
                'source': decision.source,
                'degraded': decision.degraded,
                'selectedTool': selected_tool,
                'candidateTools': candidate_tools,
                'reason': decision.reason,
                'llm_model': decision.llm_model,
                'prompt_meta': decision.prompt_meta,
            },
        )
        return selected_tool

    async def _call_tool(
        self,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        *,
        tool_name: str,
        risk_percent: float,
        metaapi_account_ref: int | None,
    ) -> dict[str, Any]:
        tool_payload = {
            'run_id': run.id,
            'pair': run.pair,
            'timeframe': run.timeframe,
            'mode': run.mode,
            'risk_percent': risk_percent,
            'metaapi_account_ref': metaapi_account_ref,
        }
        self.session_store.append_event(
            db,
            run,
            state=state,
            event_stream='tool',
            name=tool_name,
            payload={
                'phase': 'start',
                'name': tool_name,
                'toolCallId': f'{run.id}:{state.turn}:{tool_name}',
                'args': tool_payload,
            },
        )
        try:
            result = await self.registry.call(
                tool_name,
                db=db,
                run=run,
                state=state,
                risk_percent=risk_percent,
                metaapi_account_ref=metaapi_account_ref,
            )
        except Exception as exc:
            self.session_store.append_event(
                db,
                run,
                state=state,
                event_stream='tool',
                name=tool_name,
                payload={
                    'phase': 'result',
                    'name': tool_name,
                    'toolCallId': f'{run.id}:{state.turn}:{tool_name}',
                    'isError': True,
                    'error': str(exc),
                },
                runtime_status='failed',
            )
            raise
        self._record_history(state, tool_name=tool_name, result=result)
        self.session_store.append_event(
            db,
            run,
            state=state,
            event_stream='tool',
            name=tool_name,
            payload={
                'phase': 'result',
                'name': tool_name,
                'toolCallId': f'{run.id}:{state.turn}:{tool_name}',
                'isError': False,
                'result': self._compact_payload(result),
            },
        )
        state.completed_tools.append(tool_name)
        state.current_phase = tool_name
        return result

    async def execute(
        self,
        db: Session,
        run: AnalysisRun,
        risk_percent: float,
        metaapi_account_ref: int | None = None,
    ) -> AnalysisRun:
        run.status = 'running'
        db.commit()
        db.refresh(run)

        self.orchestrator._ensure_prompt_defaults(self.orchestrator.prompt_service, db)
        metaapi_account_ref = self.orchestrator._resolve_requested_metaapi_account_ref(run, metaapi_account_ref)
        started_at_ms = int(time.time() * 1000)
        objective = self._build_objective(run=run, risk_percent=risk_percent, metaapi_account_ref=metaapi_account_ref)
        state = self.session_store.restore_state(run)
        resuming_existing_state = state is not None
        if state is None:
            state = RuntimeSessionState(
                objective=objective,
                max_turns=max(int(self.settings.agentic_runtime_max_turns), len(self.PLAN)),
                plan=list(self.PLAN),
            )
            state.context['metaapi_account_ref'] = metaapi_account_ref
            state.context['memory_refresh_count'] = 0

            self.session_store.initialize(
                db,
                run,
                runtime_engine=AGENTIC_V2_RUNTIME,
                objective=objective,
                plan=list(self.PLAN),
                max_turns=state.max_turns,
            )
            self.session_store.append_event(
                db,
                run,
                state=state,
                event_stream='lifecycle',
                name='started',
                payload={
                    'phase': 'start',
                    'startedAt': started_at_ms,
                    'objective': objective,
                    'tools': self.registry.list_tools(),
                },
            )
        else:
            state.context['metaapi_account_ref'] = metaapi_account_ref
            self.session_store.append_event(
                db,
                run,
                state=state,
                event_stream='lifecycle',
                name='resumed',
                payload={
                    'phase': 'resume',
                    'startedAt': started_at_ms,
                    'objective': state.objective,
                    'tools': self.registry.list_tools(),
                },
            )
            state.notes.append('Runtime resumed from persisted state snapshot.')

        try:
            while state.turn < state.max_turns:
                tool_name = self._select_next_tool(db, run, state)
                if tool_name is None:
                    break
                state.turn += 1
                await self._call_tool(
                    db,
                    run,
                    state,
                    tool_name=tool_name,
                    risk_percent=risk_percent,
                    metaapi_account_ref=metaapi_account_ref,
                )
                self.session_store.persist_session(db, run, state=state)

            if self._candidate_tools(state):
                raise RuntimeError(f'Agent runtime reached max_turns={state.max_turns} before completion.')

            analysis_outputs = self._analysis_outputs(state)
            bullish = state.artifacts.get('bullish') if isinstance(state.artifacts.get('bullish'), dict) else {}
            bearish = state.artifacts.get('bearish') if isinstance(state.artifacts.get('bearish'), dict) else {}
            trader_decision = state.artifacts.get('trader_decision') if isinstance(state.artifacts.get('trader_decision'), dict) else {}
            risk_output = state.artifacts.get('risk') if isinstance(state.artifacts.get('risk'), dict) else {}
            execution_output = state.artifacts.get('execution_manager') if isinstance(state.artifacts.get('execution_manager'), dict) else {}
            execution_result = state.artifacts.get('execution_result') if isinstance(state.artifacts.get('execution_result'), dict) else {}

            run.decision = {
                **trader_decision,
                'risk': risk_output,
                'execution': execution_result,
                'execution_manager': execution_output,
                'runtime_engine': AGENTIC_V2_RUNTIME,
            }
            run.status = 'completed'

            trace_payload = run.trace if isinstance(run.trace, dict) else {}
            trace_payload = {
                **trace_payload,
                'market': state.context.get('market', {}),
                'news': state.context.get('news', {}),
                'analysis_outputs': analysis_outputs,
                'bullish': bullish,
                'bearish': bearish,
                'memory_context': state.context.get('memory_context', []),
                'memory_context_enabled': state.context.get('memory_context_enabled', False),
                'memory_signal': state.context.get('memory_signal', {}),
                'memory_runtime': state.context.get('memory_runtime', {}),
                'memory_retrieval_context': state.context.get('memory_retrieval_context', {}),
                'requested_metaapi_account_ref': metaapi_account_ref,
                'workflow': list(self.orchestrator.WORKFLOW_STEPS),
                'workflow_mode': AGENTIC_V2_RUNTIME,
                'runtime_engine': AGENTIC_V2_RUNTIME,
            }
            run.trace = trace_payload
            db.commit()
            db.refresh(run)

            vector_memory_meta: dict[str, Any] = {
                'stored': False,
                'entry_id': None,
                'error': None,
            }
            try:
                vector_entry = self.orchestrator.memory_service.add_run_memory(db, run)
                vector_memory_meta['stored'] = True
                if vector_entry is not None:
                    vector_memory_meta['entry_id'] = getattr(vector_entry, 'id', None)
            except Exception as exc:
                logger.exception('agentic runtime vector memory persistence failed run_id=%s', run.id)
                vector_memory_meta['error'] = str(exc)

            memori_store_meta = self.orchestrator.memori_memory_service.store_run_memory(run)
            memory_persistence_meta = {
                'vector': vector_memory_meta,
                'memori': memori_store_meta,
            }
            updated_trace = run.trace if isinstance(run.trace, dict) else {}
            run.trace = {**updated_trace, 'memory_persistence': memory_persistence_meta}
            db.commit()
            db.refresh(run)

            state.status = 'completed'
            state.notes.append('Runtime completed successfully.')
            self.session_store.append_event(
                db,
                run,
                state=state,
                event_stream='lifecycle',
                name='completed',
                payload={
                    'phase': 'end',
                    'startedAt': started_at_ms,
                    'endedAt': int(time.time() * 1000),
                    'decision': trader_decision.get('decision'),
                    'confidence': trader_decision.get('confidence'),
                    'risk_accepted': risk_output.get('accepted'),
                    'execution_status': execution_result.get('status'),
                    'resumed': resuming_existing_state,
                },
                runtime_status='completed',
            )
            return run
        except Exception as exc:
            logger.exception('agentic runtime failed run_id=%s', run.id)
            state.status = 'failed'
            state.notes.append(str(exc))
            run.status = 'failed'
            run.error = str(exc)
            failed_trace = run.trace if isinstance(run.trace, dict) else {}
            run.trace = {
                **failed_trace,
                'runtime_engine': AGENTIC_V2_RUNTIME,
                'workflow_mode': AGENTIC_V2_RUNTIME,
            }
            db.commit()
            db.refresh(run)
            self.session_store.append_event(
                db,
                run,
                state=state,
                event_stream='lifecycle',
                name='failed',
                payload={
                    'phase': 'error',
                    'startedAt': started_at_ms,
                    'endedAt': int(time.time() * 1000),
                    'error': str(exc),
                    'resumed': resuming_existing_state,
                },
                runtime_status='failed',
            )
            raise

    async def _tool_resolve_market_context(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        risk_percent: float,
        metaapi_account_ref: int | None,
    ) -> dict[str, Any]:
        del risk_percent
        market = await self.orchestrator.resolve_market_snapshot(
            db,
            pair=run.pair,
            timeframe=run.timeframe,
            metaapi_account_ref=metaapi_account_ref,
        )
        news = self.orchestrator.market_provider.get_news_context(run.pair)
        state.context['market'] = market
        state.context['news'] = news
        return {'market': market, 'news': news}

    async def _tool_load_memory_context(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        risk_percent: float,
        metaapi_account_ref: int | None,
    ) -> dict[str, Any]:
        del metaapi_account_ref, risk_percent
        market = state.context.get('market')
        if not isinstance(market, dict):
            raise RuntimeError('Market snapshot must be loaded before memory context.')

        memory_context_enabled = self.orchestrator.model_selector.resolve_memory_context_enabled(db)
        decision_mode = self.orchestrator.model_selector.resolve_decision_mode(db)
        memory_retrieval_context = self.orchestrator.memory_service.build_retrieval_context(
            market,
            decision_mode=decision_mode,
        )
        memory_limit = max(int(self.settings.orchestrator_memory_search_limit), 1)
        memory_context, memory_signal, memory_runtime = self.orchestrator._load_memory_state(
            db=db,
            pair=run.pair,
            timeframe=run.timeframe,
            market=market,
            decision_mode=decision_mode,
            memory_retrieval_context=memory_retrieval_context,
            memory_context_enabled=memory_context_enabled,
            limit=memory_limit,
        )
        state.context['memory_context_enabled'] = memory_context_enabled
        state.context['memory_context'] = memory_context
        state.context['memory_signal'] = memory_signal
        state.context['memory_runtime'] = memory_runtime
        state.context['memory_retrieval_context'] = memory_retrieval_context
        state.context['memory_limit'] = memory_limit
        return {
            'memory_context_count': len(memory_context),
            'memory_signal': memory_signal,
            'memory_runtime': memory_runtime,
        }

    async def _tool_refresh_memory_context(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        risk_percent: float,
        metaapi_account_ref: int | None,
    ) -> dict[str, Any]:
        del metaapi_account_ref, risk_percent
        market = state.context.get('market')
        if not isinstance(market, dict):
            raise RuntimeError('Market snapshot must be loaded before memory refresh.')

        decision_mode = self.orchestrator.model_selector.resolve_decision_mode(db)
        memory_retrieval_context = state.context.get('memory_retrieval_context')
        if not isinstance(memory_retrieval_context, dict):
            memory_retrieval_context = self.orchestrator.memory_service.build_retrieval_context(
                market,
                decision_mode=decision_mode,
            )

        current_limit = max(int(state.context.get('memory_limit', self.settings.orchestrator_memory_search_limit) or 1), 1)
        next_limit = min(
            current_limit + int(self.settings.orchestrator_autonomy_memory_limit_step),
            max(int(self.settings.orchestrator_autonomy_memory_limit_max), current_limit),
        )
        memory_context, memory_signal, memory_runtime = self.orchestrator._load_memory_state(
            db=db,
            pair=run.pair,
            timeframe=run.timeframe,
            market=market,
            decision_mode=decision_mode,
            memory_retrieval_context=memory_retrieval_context,
            memory_context_enabled=bool(state.context.get('memory_context_enabled', False)),
            limit=next_limit,
        )
        state.context['memory_context'] = memory_context
        state.context['memory_signal'] = memory_signal
        state.context['memory_runtime'] = memory_runtime
        state.context['memory_limit'] = next_limit
        state.context['memory_refresh_count'] = int(state.context.get('memory_refresh_count', 0) or 0) + 1
        state.artifacts.pop('analysis_outputs', None)
        state.artifacts.pop('bullish', None)
        state.artifacts.pop('bearish', None)
        state.artifacts.pop('trader_decision', None)
        state.artifacts.pop('risk', None)
        state.artifacts.pop('execution_manager', None)
        state.artifacts.pop('execution_result', None)
        state.notes.append(f'Memory refreshed with limit={next_limit}.')
        return {
            'memory_context_count': len(memory_context),
            'memory_limit': next_limit,
            'memory_refresh_count': state.context['memory_refresh_count'],
        }

    async def _tool_spawn_subagent(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        name: str,
        label: str,
        source_tool: str,
        objective: dict[str, Any],
        input_payload: dict[str, Any],
        fn: Callable[[], dict[str, Any]],
        session_key: str | None = None,
        existing_session_key: str | None = None,
        mode: str = 'session',
        **_: Any,
    ) -> dict[str, Any]:
        return await self._run_specialist_subagent(
            db,
            run,
            state,
            name=name,
            label=label,
            source_tool=source_tool,
            objective=objective,
            input_payload=input_payload,
            fn=fn,
            session_key=str(existing_session_key or session_key or '').strip() or None,
            session_mode=mode,
        )

    async def _tool_sessions_spawn(self, **kwargs: Any) -> dict[str, Any]:
        return await self._tool_spawn_subagent(**kwargs)

    async def _tool_sessions_resume(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        session_key: str,
        risk_percent: float,
        metaapi_account_ref: int | None,
        **_: Any,
    ) -> dict[str, Any]:
        session_entry = self.session_store.get_session(run, session_key=session_key)
        if session_entry is None:
            raise RuntimeError(f'Unknown subagent session: {session_key}')

        source_tool = str(session_entry.get('source_tool') or '').strip()
        if not source_tool:
            raise RuntimeError(f'Session {session_key} has no resumable source tool.')
        if not self.registry.has(source_tool):
            raise RuntimeError(f'Resumable source tool is not registered: {source_tool}')

        return await self.registry.call(
            source_tool,
            db=db,
            run=run,
            state=state,
            risk_percent=risk_percent,
            metaapi_account_ref=metaapi_account_ref,
            existing_session_key=session_key,
        )

    async def _tool_session_status(
        self,
        *,
        run: AnalysisRun,
        session_key: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        target_session_key = str(session_key or self.session_store.root_session_key(run)).strip()
        session_entry = self.session_store.get_session(run, session_key=target_session_key)
        if session_entry is None:
            raise RuntimeError(f'Unknown runtime session: {target_session_key}')
        return session_entry

    async def _tool_sessions_list(
        self,
        *,
        run: AnalysisRun,
        **_: Any,
    ) -> dict[str, Any]:
        sessions = self.session_store.list_sessions(run)
        return {
            'count': len(sessions),
            'sessions': sessions,
        }

    async def _tool_sessions_history(
        self,
        *,
        run: AnalysisRun,
        session_key: str | None = None,
        limit: int = 20,
        **_: Any,
    ) -> dict[str, Any]:
        target_session_key = str(session_key or self.session_store.root_session_key(run)).strip()
        items = self.session_store.get_session_history(run, session_key=target_session_key, limit=limit)
        return {
            'session_key': target_session_key,
            'count': len(items),
            'messages': items,
        }

    async def _tool_sessions_send(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        session_key: str,
        message: str,
        risk_percent: float,
        metaapi_account_ref: int | None,
        resume: bool = False,
        sender_session_key: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        normalized_session_key = str(session_key or '').strip()
        if not normalized_session_key:
            raise RuntimeError('sessions_send requires a target session_key.')
        target_session = self.session_store.get_session(run, session_key=normalized_session_key)
        if target_session is None:
            raise RuntimeError(f'Unknown runtime session: {normalized_session_key}')

        root_session_key = self.session_store.root_session_key(run)
        origin_session_key = str(sender_session_key or root_session_key).strip() or root_session_key
        stored_message = self.session_store.append_session_message(
            db,
            run,
            session_key=normalized_session_key,
            role='user',
            content=str(message or ''),
            sender_session_key=origin_session_key,
            metadata={
                'phase': 'message',
                'resume_requested': bool(resume),
            },
        )
        self.session_store.append_event(
            db,
            run,
            state=state,
            event_stream='sessions',
            name='message_sent',
            payload={
                'phase': 'message',
                'sessionKey': normalized_session_key,
                'senderSessionKey': origin_session_key,
                'messageId': stored_message.get('id'),
                'resumeRequested': bool(resume),
            },
        )

        resume_output: dict[str, Any] | None = None
        if resume:
            resume_output = await self._tool_sessions_resume(
                db=db,
                run=run,
                state=state,
                session_key=normalized_session_key,
                risk_percent=risk_percent,
                metaapi_account_ref=metaapi_account_ref,
            )

        return {
            'delivered': True,
            'session_key': normalized_session_key,
            'message': stored_message,
            'resumed': bool(resume_output is not None),
            'resume_output': resume_output,
        }

    async def _tool_run_technical_analyst(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        risk_percent: float,
        metaapi_account_ref: int | None,
        existing_session_key: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        del metaapi_account_ref
        ctx = self._build_context(state, run=run, risk_percent=risk_percent)
        input_payload = {'pair': run.pair, 'timeframe': run.timeframe}
        output = await self._tool_spawn_subagent(
            db=db,
            run=run,
            state=state,
            name=self.orchestrator.technical_agent.name,
            label='Technical analyst',
            source_tool='run_technical_analyst',
            objective=self._build_subagent_objective(
                run=run,
                kind='technical-analysis',
                label='Technical analyst',
                source_tool='run_technical_analyst',
            ),
            input_payload=input_payload,
            existing_session_key=existing_session_key,
            fn=lambda: self._record_agent_step(
                db,
                run,
                agent_name=self.orchestrator.technical_agent.name,
                input_payload=input_payload,
                fn=lambda: self.orchestrator.technical_agent.run(ctx, db=db),
            ),
        )
        analysis_outputs = self._analysis_outputs(state)
        state.artifacts['analysis_outputs'] = {**analysis_outputs, self.orchestrator.technical_agent.name: output}
        return output

    async def _tool_run_news_analyst(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        risk_percent: float,
        metaapi_account_ref: int | None,
        existing_session_key: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        del metaapi_account_ref
        ctx = self._build_context(state, run=run, risk_percent=risk_percent)
        input_payload = {
            'news_count': len(ctx.news_context.get('news', [])),
            'memory_context': ctx.memory_context,
            'news_symbol': ctx.news_context.get('symbol'),
            'news_reason': ctx.news_context.get('reason'),
            'news_symbols_scanned': ctx.news_context.get('symbols_scanned', []),
        }
        output = await self._tool_spawn_subagent(
            db=db,
            run=run,
            state=state,
            name=self.orchestrator.news_agent.name,
            label='News analyst',
            source_tool='run_news_analyst',
            objective=self._build_subagent_objective(
                run=run,
                kind='news-analysis',
                label='News analyst',
                source_tool='run_news_analyst',
            ),
            input_payload=input_payload,
            existing_session_key=existing_session_key,
            fn=lambda: self._record_agent_step(
                db,
                run,
                agent_name=self.orchestrator.news_agent.name,
                input_payload=input_payload,
                fn=lambda: self.orchestrator.news_agent.run(ctx, db=db),
            ),
        )
        analysis_outputs = self._analysis_outputs(state)
        state.artifacts['analysis_outputs'] = {**analysis_outputs, self.orchestrator.news_agent.name: output}
        return output

    async def _tool_run_market_context_analyst(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        risk_percent: float,
        metaapi_account_ref: int | None,
        existing_session_key: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        del metaapi_account_ref
        ctx = self._build_context(state, run=run, risk_percent=risk_percent)
        input_payload = {'market': ctx.market_snapshot}
        output = await self._tool_spawn_subagent(
            db=db,
            run=run,
            state=state,
            name=self.orchestrator.market_context_agent.name,
            label='Market context analyst',
            source_tool='run_market_context_analyst',
            objective=self._build_subagent_objective(
                run=run,
                kind='market-context-analysis',
                label='Market context analyst',
                source_tool='run_market_context_analyst',
            ),
            input_payload=input_payload,
            existing_session_key=existing_session_key,
            fn=lambda: self._record_agent_step(
                db,
                run,
                agent_name=self.orchestrator.market_context_agent.name,
                input_payload=input_payload,
                fn=lambda: self.orchestrator.market_context_agent.run(ctx, db=db),
            ),
        )
        analysis_outputs = self._analysis_outputs(state)
        state.artifacts['analysis_outputs'] = {**analysis_outputs, self.orchestrator.market_context_agent.name: output}
        return output

    async def _tool_run_bullish_researcher(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        risk_percent: float,
        metaapi_account_ref: int | None,
        existing_session_key: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        del metaapi_account_ref
        ctx = self._build_context(state, run=run, risk_percent=risk_percent)
        analysis_snapshot = self.orchestrator._compact_analysis_outputs_for_debate(self._analysis_outputs(state))
        input_payload = {'analysis_outputs': analysis_snapshot, 'memory_context': ctx.memory_context}
        output = await self._tool_spawn_subagent(
            db=db,
            run=run,
            state=state,
            name=self.orchestrator.bullish_researcher.name,
            label='Bullish researcher',
            source_tool='run_bullish_researcher',
            objective=self._build_subagent_objective(
                run=run,
                kind='debate',
                label='Bullish researcher',
                source_tool='run_bullish_researcher',
                extra={'stance': 'bullish'},
            ),
            input_payload=input_payload,
            existing_session_key=existing_session_key,
            fn=lambda: self._record_agent_step(
                db,
                run,
                agent_name=self.orchestrator.bullish_researcher.name,
                input_payload=input_payload,
                fn=lambda: self.orchestrator.bullish_researcher.run(ctx, analysis_snapshot, db=db),
            ),
        )
        state.artifacts['bullish'] = output
        return output

    async def _tool_run_bearish_researcher(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        risk_percent: float,
        metaapi_account_ref: int | None,
        existing_session_key: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        del metaapi_account_ref
        ctx = self._build_context(state, run=run, risk_percent=risk_percent)
        analysis_snapshot = self.orchestrator._compact_analysis_outputs_for_debate(self._analysis_outputs(state))
        input_payload = {'analysis_outputs': analysis_snapshot, 'memory_context': ctx.memory_context}
        output = await self._tool_spawn_subagent(
            db=db,
            run=run,
            state=state,
            name=self.orchestrator.bearish_researcher.name,
            label='Bearish researcher',
            source_tool='run_bearish_researcher',
            objective=self._build_subagent_objective(
                run=run,
                kind='debate',
                label='Bearish researcher',
                source_tool='run_bearish_researcher',
                extra={'stance': 'bearish'},
            ),
            input_payload=input_payload,
            existing_session_key=existing_session_key,
            fn=lambda: self._record_agent_step(
                db,
                run,
                agent_name=self.orchestrator.bearish_researcher.name,
                input_payload=input_payload,
                fn=lambda: self.orchestrator.bearish_researcher.run(ctx, analysis_snapshot, db=db),
            ),
        )
        state.artifacts['bearish'] = output
        return output

    async def _tool_run_trader_agent(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        risk_percent: float,
        metaapi_account_ref: int | None,
        existing_session_key: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        del metaapi_account_ref
        ctx = self._build_context(state, run=run, risk_percent=risk_percent)
        analysis_outputs = self._analysis_outputs(state)
        bullish = state.artifacts.get('bullish') if isinstance(state.artifacts.get('bullish'), dict) else {}
        bearish = state.artifacts.get('bearish') if isinstance(state.artifacts.get('bearish'), dict) else {}
        input_payload = {
            'analysis_outputs': analysis_outputs,
            'bullish': bullish,
            'bearish': bearish,
            'memory_signal': ctx.memory_signal,
        }
        output = await self._tool_spawn_subagent(
            db=db,
            run=run,
            state=state,
            name=self.orchestrator.trader_agent.name,
            label='Trader agent',
            source_tool='run_trader_agent',
            objective=self._build_subagent_objective(
                run=run,
                kind='trade-decision',
                label='Trader agent',
                source_tool='run_trader_agent',
            ),
            input_payload=input_payload,
            existing_session_key=existing_session_key,
            fn=lambda: self._record_agent_step(
                db,
                run,
                agent_name=self.orchestrator.trader_agent.name,
                input_payload=input_payload,
                fn=lambda: self.orchestrator.trader_agent.run(ctx, analysis_outputs, bullish, bearish, db=db),
            ),
        )
        state.artifacts['trader_decision'] = output
        return output

    async def _tool_run_risk_manager(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        risk_percent: float,
        metaapi_account_ref: int | None,
    ) -> dict[str, Any]:
        del metaapi_account_ref
        ctx = self._build_context(state, run=run, risk_percent=risk_percent)
        trader_decision = state.artifacts.get('trader_decision') if isinstance(state.artifacts.get('trader_decision'), dict) else {}
        output = self._record_agent_step(
            db,
            run,
            agent_name=self.orchestrator.risk_manager_agent.name,
            input_payload={'trader_decision': trader_decision},
            fn=lambda: self.orchestrator.risk_manager_agent.run(ctx, trader_decision, db=db),
        )
        state.artifacts['risk'] = output
        return output

    async def _tool_run_execution_manager(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        state: RuntimeSessionState,
        risk_percent: float,
        metaapi_account_ref: int | None,
    ) -> dict[str, Any]:
        ctx = self._build_context(state, run=run, risk_percent=risk_percent)
        trader_decision = state.artifacts.get('trader_decision') if isinstance(state.artifacts.get('trader_decision'), dict) else {}
        risk_output = state.artifacts.get('risk') if isinstance(state.artifacts.get('risk'), dict) else {}

        execution_input = {
            'trader_decision': trader_decision,
            'risk': risk_output,
            'metaapi_account_ref': metaapi_account_ref,
        }
        execution_plan = self.orchestrator.execution_manager_agent.run(
            ctx,
            trader_decision,
            risk_output,
            db=db,
        )
        execution_result: dict[str, Any] = {
            'status': 'skipped',
            'executed': False,
            'reason': execution_plan.get('reason', 'Execution blocked by execution-manager'),
        }
        if bool(execution_plan.get('should_execute')) and execution_plan.get('side') in {'BUY', 'SELL'}:
            execution_result = await self.orchestrator.execution_service.execute(
                db=db,
                run_id=run.id,
                mode=run.mode,
                symbol=run.pair,
                side=str(execution_plan.get('side')),
                volume=float(execution_plan.get('volume', 0.0)),
                stop_loss=trader_decision.get('stop_loss'),
                take_profit=trader_decision.get('take_profit'),
                metaapi_account_ref=metaapi_account_ref,
            )
        execution_output = {
            **execution_plan,
            'execution': execution_result,
            'status': execution_result.get('status', 'failed'),
        }
        self.orchestrator._record_step(db, run, self.orchestrator.execution_manager_agent.name, execution_input, execution_output)
        state.artifacts['execution_manager'] = execution_output
        state.artifacts['execution_result'] = execution_result
        return execution_output
