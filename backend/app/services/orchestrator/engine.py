import logging
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.agent_step import AgentStep
from app.db.models.run import AnalysisRun
from app.db.session import SessionLocal
from app.observability.metrics import analysis_runs_total, orchestrator_step_duration_seconds
from app.services.execution.executor import ExecutionService
from app.services.llm.model_selector import AgentModelSelector
from app.services.market.yfinance_provider import YFinanceMarketProvider
from app.services.memory.vector_memory import VectorMemoryService
from app.services.orchestrator.agents import (
    AgentContext,
    BearishResearcherAgent,
    BullishResearcherAgent,
    ExecutionManagerAgent,
    MacroAnalystAgent,
    NewsAnalystAgent,
    RiskManagerAgent,
    SentimentAgent,
    TechnicalAnalystAgent,
    TraderAgent,
)
from app.services.prompts.registry import PromptTemplateService

logger = logging.getLogger(__name__)


class ForexOrchestrator:
    WORKFLOW_STEPS = (
        'technical-analyst',
        'news-analyst',
        'macro-analyst',
        'sentiment-agent',
        'bullish-researcher',
        'bearish-researcher',
        'trader-agent',
        'risk-manager',
        'execution-manager',
    )
    _prompt_seed_lock = threading.Lock()
    _prompt_defaults_seeded = False

    def __init__(self) -> None:
        self.settings = get_settings()
        self.market_provider = YFinanceMarketProvider()
        self.memory_service = VectorMemoryService()
        self.prompt_service = PromptTemplateService()
        self.execution_service = ExecutionService()
        self.model_selector = AgentModelSelector()

        self.technical_agent = TechnicalAnalystAgent()
        self.news_agent = NewsAnalystAgent(self.prompt_service)
        self.macro_agent = MacroAnalystAgent()
        self.sentiment_agent = SentimentAgent()
        self.bullish_researcher = BullishResearcherAgent(self.prompt_service)
        self.bearish_researcher = BearishResearcherAgent(self.prompt_service)
        self.trader_agent = TraderAgent()
        self.risk_manager_agent = RiskManagerAgent()
        self.execution_manager_agent = ExecutionManagerAgent()

    @classmethod
    def _ensure_prompt_defaults(cls, prompt_service: PromptTemplateService, db: Session) -> None:
        if cls._prompt_defaults_seeded:
            return
        with cls._prompt_seed_lock:
            if cls._prompt_defaults_seeded:
                return
            prompt_service.seed_defaults(db)
            cls._prompt_defaults_seeded = True

    def _record_step(self, db: Session, run: AnalysisRun, agent_name: str, input_payload: dict[str, Any], output_payload: dict[str, Any]) -> None:
        step = AgentStep(
            run_id=run.id,
            agent_name=agent_name,
            status='completed',
            input_payload=input_payload,
            output_payload=output_payload,
        )
        db.add(step)
        db.flush()

    def _run_step(
        self,
        db: Session,
        run: AnalysisRun,
        agent_name: str,
        input_payload: dict[str, Any],
        fn: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        output = fn()
        elapsed = time.perf_counter() - started
        orchestrator_step_duration_seconds.labels(agent=agent_name).observe(elapsed)
        self._record_step(db, run, agent_name, input_payload, output)
        return output

    def _run_transient_step(
        self,
        agent_name: str,
        fn: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        output = fn()
        elapsed = time.perf_counter() - started
        orchestrator_step_duration_seconds.labels(agent=agent_name).observe(elapsed)
        return output

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): ForexOrchestrator._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [ForexOrchestrator._json_safe(item) for item in value]
        if hasattr(value, 'isoformat'):
            try:
                return value.isoformat()
            except Exception:
                pass
        if hasattr(value, 'item'):
            try:
                return ForexOrchestrator._json_safe(value.item())
            except Exception:
                pass
        return str(value)

    @staticmethod
    def _compact_analysis_outputs_for_debate(analysis_outputs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        compact: dict[str, dict[str, Any]] = {}
        for agent_name, output in (analysis_outputs or {}).items():
            if not isinstance(output, dict):
                continue
            compact_output: dict[str, Any] = {}
            for key in (
                'signal',
                'score',
                'reason',
                'summary',
                'llm_summary',
                'news_count',
                'macro_event_count',
                'coverage',
                'information_state',
                'decision_mode',
                'fetch_status',
                'degraded',
            ):
                if key in output:
                    compact_output[key] = output.get(key)
            indicators = output.get('indicators')
            if isinstance(indicators, dict):
                compact_indicators = {
                    key: indicators.get(key)
                    for key in ('trend', 'rsi', 'macd_diff', 'last_price', 'atr', 'change_pct')
                    if key in indicators
                }
                if compact_indicators:
                    compact_output['indicators'] = compact_indicators
            compact[agent_name] = compact_output
        return compact

    def _collect_run_steps(self, db: Session, run_id: int) -> list[dict[str, Any]]:
        steps = (
            db.query(AgentStep)
            .filter(AgentStep.run_id == run_id)
            .order_by(AgentStep.id.asc())
            .all()
        )
        return [
            {
                'id': step.id,
                'agent_name': step.agent_name,
                'status': step.status,
                'created_at': step.created_at.isoformat() if step.created_at else None,
                'input_payload': self._json_safe(step.input_payload),
                'output_payload': self._json_safe(step.output_payload),
                'error': step.error,
            }
            for step in steps
        ]

    def _build_debug_trade_payload(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        risk_percent: float,
        metaapi_account_ref: int | None,
        market: dict[str, Any],
        news: dict[str, Any],
        memory_context: list[dict[str, Any]],
        memory_signal: dict[str, Any],
        price_history: list[dict[str, Any]],
        analysis_outputs: dict[str, dict[str, Any]],
        bullish: dict[str, Any],
        bearish: dict[str, Any],
        trader_decision: dict[str, Any],
        risk_output: dict[str, Any],
        execution_output: dict[str, Any],
        execution_result: dict[str, Any],
    ) -> dict[str, Any]:
        step_payloads = self._collect_run_steps(db, run.id)
        agent_prompt_skills: dict[str, dict[str, Any]] = {}
        for step in step_payloads:
            output_payload = step.get('output_payload')
            if not isinstance(output_payload, dict):
                continue
            prompt_meta = output_payload.get('prompt_meta')
            if isinstance(prompt_meta, dict):
                agent_prompt_skills[step['agent_name']] = self._json_safe(prompt_meta)

        return {
            'schema_version': 1,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'run': {
                'id': run.id,
                'pair': run.pair,
                'timeframe': run.timeframe,
                'mode': run.mode,
                'status': run.status,
                'risk_percent': risk_percent,
                'metaapi_account_ref': metaapi_account_ref,
                'created_at': run.created_at.isoformat() if run.created_at else None,
                'updated_at': run.updated_at.isoformat() if run.updated_at else None,
            },
            'context': {
                'market_snapshot': self._json_safe(market),
                'price_history': self._json_safe(price_history),
                'news_context': self._json_safe(news),
                'memory_context': self._json_safe(memory_context),
                'memory_signal': self._json_safe(memory_signal),
            },
            'workflow': list(self.WORKFLOW_STEPS),
            'agent_steps': step_payloads,
            'agent_prompt_skills': agent_prompt_skills,
            'analysis_bundle': {
                'analysis_outputs': self._json_safe(analysis_outputs),
                'bullish': self._json_safe(bullish),
                'bearish': self._json_safe(bearish),
                'trader_decision': self._json_safe(trader_decision),
                'risk': self._json_safe(risk_output),
                'execution_manager': self._json_safe(execution_output),
                'execution_result': self._json_safe(execution_result),
            },
            'final_decision': self._json_safe(run.decision),
        }

    def _write_debug_trade_payload(self, run_id: int, payload: dict[str, Any]) -> str | None:
        try:
            directory = Path(self.settings.debug_trade_json_dir or './debug-traces').expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            file_path = directory / f'run-{run_id}-{suffix}.json'
            file_path.write_text(
                json.dumps(self._json_safe(payload), ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            return str(file_path.resolve())
        except Exception:
            logger.exception('failed to persist debug trade payload run_id=%s', run_id)
            return None

    @staticmethod
    def _collect_degraded_agents(named_outputs: dict[str, dict[str, Any] | None]) -> list[str]:
        degraded_agents: list[str] = []
        for agent_name, output in named_outputs.items():
            if isinstance(output, dict) and output.get('degraded'):
                degraded_agents.append(agent_name)
        return degraded_agents

    @staticmethod
    def _is_live_trade_candidate(
        trader_decision: dict[str, Any],
        risk_output: dict[str, Any],
    ) -> bool:
        decision = str(trader_decision.get('decision', 'HOLD') or '').strip().upper() or 'HOLD'
        if decision not in {'BUY', 'SELL'}:
            return False

        execution_allowed = bool(trader_decision.get('execution_allowed', decision in {'BUY', 'SELL'}))
        if not execution_allowed or not bool(risk_output.get('accepted')):
            return False

        try:
            suggested_volume = float(risk_output.get('suggested_volume', 0.0) or 0.0)
        except (TypeError, ValueError):
            suggested_volume = 0.0
        return suggested_volume > 0.0

    def _collect_live_blocking_degraded_agents(
        self,
        named_outputs: dict[str, dict[str, Any] | None],
        *,
        trader_decision: dict[str, Any],
        risk_output: dict[str, Any],
    ) -> list[str]:
        degraded_agents = self._collect_degraded_agents(named_outputs)
        if not degraded_agents:
            return []

        # These agents can degrade on explanatory text while core trade safety remains deterministic.
        non_blocking_when_no_trade = {
            self.bullish_researcher.name,
            self.bearish_researcher.name,
            self.trader_agent.name,
            self.risk_manager_agent.name,
        }

        if not self._is_live_trade_candidate(trader_decision, risk_output):
            return []

        return [agent for agent in degraded_agents if agent not in non_blocking_when_no_trade]

    def analyze_context(
        self,
        context: AgentContext,
        db: Session | None = None,
        run: AnalysisRun | None = None,
        record_steps: bool = False,
        emit_step_logs: bool = False,
    ) -> dict[str, Any]:
        if record_steps and (db is None or run is None):
            raise ValueError('record_steps requires db session and run entity')

        def summarize_output(output: dict[str, Any]) -> dict[str, Any]:
            summary: dict[str, Any] = {}
            for key in (
                'signal',
                'score',
                'decision',
                'confidence',
                'net_score',
                'accepted',
                'suggested_volume',
                'should_execute',
                'status',
            ):
                if key in output:
                    summary[key] = output[key]
            return summary

        def execute_step(
            agent_name: str,
            input_payload: dict[str, Any],
            fn: Callable[[], dict[str, Any]],
        ) -> dict[str, Any]:
            if record_steps and db is not None and run is not None:
                output = self._run_step(db, run, agent_name, input_payload, fn)
            else:
                output = self._run_transient_step(agent_name, fn)
            if self.settings.log_agent_steps and emit_step_logs:
                logger.info(
                    'agent_step mode=%s pair=%s timeframe=%s agent=%s summary=%s',
                    context.mode,
                    context.pair,
                    context.timeframe,
                    agent_name,
                    summarize_output(output),
                )
            return output

        def execute_parallel_steps(
            steps: list[tuple[str, dict[str, Any], Callable[[Session | None], dict[str, Any]]]],
        ) -> dict[str, dict[str, Any]]:
            if len(steps) <= 1 or self.settings.orchestrator_parallel_workers <= 1:
                outputs: dict[str, dict[str, Any]] = {}
                for agent_name, input_payload, fn in steps:
                    outputs[agent_name] = execute_step(agent_name, input_payload, lambda fn=fn: fn(db))
                return outputs

            max_workers = min(len(steps), int(self.settings.orchestrator_parallel_workers))
            finished: dict[str, tuple[dict[str, Any], float]] = {}

            def run_one(agent_name: str, fn: Callable[[Session | None], dict[str, Any]]) -> tuple[dict[str, Any], float]:
                started = time.perf_counter()
                local_db: Session | None = None
                try:
                    if db is not None:
                        local_db = SessionLocal()
                    output = fn(local_db if local_db is not None else db)
                    elapsed = time.perf_counter() - started
                    return output, elapsed
                finally:
                    if local_db is not None:
                        local_db.close()

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(run_one, agent_name, fn): (agent_name, input_payload)
                    for agent_name, input_payload, fn in steps
                }
                for future in as_completed(future_map):
                    agent_name, _ = future_map[future]
                    output, elapsed = future.result()
                    finished[agent_name] = (output, elapsed)

            ordered: dict[str, dict[str, Any]] = {}
            for agent_name, input_payload, _ in steps:
                output, elapsed = finished[agent_name]
                orchestrator_step_duration_seconds.labels(agent=agent_name).observe(elapsed)
                if record_steps and db is not None and run is not None:
                    self._record_step(db, run, agent_name, input_payload, output)
                if self.settings.log_agent_steps and emit_step_logs:
                    logger.info(
                        'agent_step mode=%s pair=%s timeframe=%s agent=%s summary=%s',
                        context.mode,
                        context.pair,
                        context.timeframe,
                        agent_name,
                        summarize_output(output),
                    )
                ordered[agent_name] = output
            return ordered

        analysis_outputs: dict[str, dict[str, Any]] = {}
        initial_outputs = execute_parallel_steps(
            [
                (
                    self.technical_agent.name,
                    {'pair': context.pair, 'timeframe': context.timeframe},
                    lambda local_db: self.technical_agent.run(context, db=local_db),
                ),
                (
                    self.news_agent.name,
                    {
                        'news_count': len(context.news_context.get('news', [])),
                        'memory_context': context.memory_context,
                        'news_symbol': context.news_context.get('symbol'),
                        'news_reason': context.news_context.get('reason'),
                        'news_symbols_scanned': context.news_context.get('symbols_scanned', []),
                    },
                    lambda local_db: self.news_agent.run(context, db=local_db),
                ),
                (
                    self.macro_agent.name,
                    {'market': context.market_snapshot},
                    lambda local_db: self.macro_agent.run(context, db=local_db),
                ),
                (
                    self.sentiment_agent.name,
                    {'market': context.market_snapshot},
                    lambda local_db: self.sentiment_agent.run(context, db=local_db),
                ),
            ]
        )
        analysis_outputs.update(initial_outputs)

        analysis_snapshot = self._compact_analysis_outputs_for_debate(analysis_outputs)
        debate_outputs = execute_parallel_steps(
            [
                (
                    self.bullish_researcher.name,
                    {'analysis_outputs': analysis_snapshot, 'memory_context': context.memory_context},
                    lambda local_db: self.bullish_researcher.run(context, analysis_snapshot, db=local_db),
                ),
                (
                    self.bearish_researcher.name,
                    {'analysis_outputs': analysis_snapshot, 'memory_context': context.memory_context},
                    lambda local_db: self.bearish_researcher.run(context, analysis_snapshot, db=local_db),
                ),
            ]
        )
        bullish = debate_outputs[self.bullish_researcher.name]
        bearish = debate_outputs[self.bearish_researcher.name]

        trader_decision = execute_step(
            self.trader_agent.name,
            {
                'analysis_outputs': analysis_outputs,
                'bullish': bullish,
                'bearish': bearish,
                'memory_signal': context.memory_signal,
            },
            lambda: self.trader_agent.run(context, analysis_outputs, bullish, bearish, db=db),
        )

        risk_output = execute_step(
            self.risk_manager_agent.name,
            {'trader_decision': trader_decision},
            lambda: self.risk_manager_agent.run(context, trader_decision, db=db),
        )

        return {
            'analysis_outputs': analysis_outputs,
            'bullish': bullish,
            'bearish': bearish,
            'trader_decision': trader_decision,
            'risk': risk_output,
        }

    async def execute(
        self,
        db: Session,
        run: AnalysisRun,
        risk_percent: float,
        metaapi_account_ref: int | None = None,
    ) -> AnalysisRun:
        run_id = run.id
        run.status = 'running'
        db.commit()
        db.refresh(run)

        self._ensure_prompt_defaults(self.prompt_service, db)

        market = self.market_provider.get_market_snapshot(run.pair, run.timeframe)
        news = self.market_provider.get_news_context(run.pair)
        memory_context_enabled = self.model_selector.resolve_memory_context_enabled(db)
        decision_mode = self.model_selector.resolve_decision_mode(db)
        memory_retrieval_context = self.memory_service.build_retrieval_context(
            market,
            decision_mode=decision_mode,
        )
        memory_context: list[dict[str, Any]] = []
        memory_signal: dict[str, Any] = self.memory_service.empty_memory_signal(
            'memory_context_disabled',
            retrieved_count=0,
            decision_mode=decision_mode,
        )
        if memory_context_enabled:
            memory_context = self.memory_service.search(
                db=db,
                pair=run.pair,
                timeframe=run.timeframe,
                query=f'{run.pair} {run.timeframe} trend {market.get("trend", "unknown")}',
                limit=5,
                retrieval_context=memory_retrieval_context,
            )
            memory_signal = self.memory_service.compute_memory_signal(
                memory_context,
                market_snapshot=market,
                decision_mode=decision_mode,
            )

        context = AgentContext(
            pair=run.pair,
            timeframe=run.timeframe,
            mode=run.mode,
            risk_percent=risk_percent,
            market_snapshot=market,
            news_context=news,
            memory_context=memory_context,
            memory_signal=memory_signal,
        )
        price_history: list[dict[str, Any]] = []
        if self.settings.debug_trade_json_enabled and self.settings.debug_trade_json_include_price_history:
            try:
                price_history = self.market_provider.get_recent_candles(
                    run.pair,
                    run.timeframe,
                    limit=self.settings.debug_trade_json_price_history_limit,
                )
            except Exception:
                logger.exception('debug price history fetch failed run_id=%s', run_id)

        try:
            analysis_bundle = self.analyze_context(context=context, db=db, run=run, record_steps=True, emit_step_logs=True)
            analysis_outputs = analysis_bundle['analysis_outputs']
            bullish = analysis_bundle['bullish']
            bearish = analysis_bundle['bearish']
            trader_decision = analysis_bundle['trader_decision']
            risk_output = analysis_bundle['risk']

            if str(run.mode or '').strip().lower() == 'live':
                candidate_outputs = {
                    **analysis_outputs,
                    self.bullish_researcher.name: bullish,
                    self.bearish_researcher.name: bearish,
                    self.trader_agent.name: trader_decision,
                    self.risk_manager_agent.name: risk_output,
                }
                degraded_agents = self._collect_live_blocking_degraded_agents(
                    candidate_outputs,
                    trader_decision=trader_decision,
                    risk_output=risk_output,
                )
                if degraded_agents:
                    degraded_list = ', '.join(sorted(dict.fromkeys(degraded_agents)))
                    raise RuntimeError(
                        f'Live mode aborted: degraded LLM response from {degraded_list}.'
                    )

            if metaapi_account_ref is None:
                metaapi_account_ref = int((run.trace or {}).get('requested_metaapi_account_ref', 0) or 0) or None

            execution_input = {
                'trader_decision': trader_decision,
                'risk': risk_output,
                'metaapi_account_ref': metaapi_account_ref,
            }
            execution_started = time.perf_counter()
            execution_plan = self.execution_manager_agent.run(
                context,
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
                execution_result = await self.execution_service.execute(
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
            execution_elapsed = time.perf_counter() - execution_started
            orchestrator_step_duration_seconds.labels(agent='execution-manager').observe(execution_elapsed)
            execution_output = {
                **execution_plan,
                'execution': execution_result,
                'status': execution_result.get('status', 'failed'),
            }
            self._record_step(db, run, 'execution-manager', execution_input, execution_output)
            if self.settings.log_agent_steps:
                logger.info(
                    'agent_step mode=%s pair=%s timeframe=%s agent=execution-manager summary=%s',
                    context.mode,
                    context.pair,
                    context.timeframe,
                    {
                        'decision': execution_output.get('decision'),
                        'should_execute': execution_output.get('should_execute'),
                        'status': execution_output.get('status'),
                    },
                )

            if str(run.mode or '').strip().lower() == 'live':
                degraded_agents = self._collect_degraded_agents(
                    {self.execution_manager_agent.name: execution_output}
                )
                if degraded_agents:
                    raise RuntimeError(
                        'Live mode aborted: degraded LLM response from execution-manager.'
                    )

            run.decision = {
                **trader_decision,
                'risk': risk_output,
                'execution': execution_result,
                'execution_manager': execution_output,
            }
            run.status = 'completed'
            trace_payload = {
                'market': market,
                'news': news,
                'analysis_outputs': analysis_outputs,
                'bullish': bullish,
                'bearish': bearish,
                'memory_context': memory_context,
                'memory_context_enabled': memory_context_enabled,
                'memory_signal': memory_signal,
                'memory_retrieval_context': memory_retrieval_context,
                'requested_metaapi_account_ref': metaapi_account_ref,
                'workflow': list(self.WORKFLOW_STEPS),
            }
            if self.settings.debug_trade_json_enabled:
                debug_payload = self._build_debug_trade_payload(
                    db=db,
                    run=run,
                    risk_percent=risk_percent,
                    metaapi_account_ref=metaapi_account_ref,
                    market=market,
                    news=news,
                    memory_context=memory_context,
                    memory_signal=memory_signal,
                    price_history=price_history,
                    analysis_outputs=analysis_outputs,
                    bullish=bullish,
                    bearish=bearish,
                    trader_decision=trader_decision,
                    risk_output=risk_output,
                    execution_output=execution_output,
                    execution_result=execution_result,
                )
                debug_file = self._write_debug_trade_payload(run.id, debug_payload)
                trace_payload['debug_trace_meta'] = {
                    'enabled': True,
                    'generated_at': debug_payload.get('generated_at'),
                    'steps_count': len(debug_payload.get('agent_steps', [])),
                    'inline_in_run_trace': self.settings.debug_trade_json_inline_in_run_trace,
                    'file_written': bool(debug_file),
                }
                if debug_file:
                    trace_payload['debug_trace_file'] = debug_file
                if self.settings.debug_trade_json_inline_in_run_trace:
                    trace_payload['debug_trace'] = debug_payload
            run.trace = trace_payload
            db.commit()
            db.refresh(run)

            self.memory_service.add_run_memory(db, run)
            analysis_runs_total.labels(status='completed').inc()
            return run
        except Exception as exc:
            logger.exception('orchestration failed run_id=%s', run_id)
            db.rollback()
            failed_run = db.get(AnalysisRun, run_id)
            if failed_run is None:
                raise
            failed_run.status = 'failed'
            failed_run.error = str(exc)
            db.commit()
            db.refresh(failed_run)
            analysis_runs_total.labels(status='failed').inc()
            return failed_run
