import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.agent_step import AgentStep
from app.db.models.run import AnalysisRun
from app.db.session import SessionLocal
from app.observability.metrics import analysis_runs_total, orchestrator_step_duration_seconds
from app.services.execution.executor import ExecutionService
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
                    {'news_count': len(context.news_context.get('news', [])), 'memory_context': context.memory_context},
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

        analysis_snapshot = dict(analysis_outputs)
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
            {'analysis_outputs': analysis_outputs, 'bullish': bullish, 'bearish': bearish},
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
        memory_context = self.memory_service.search(
            db=db,
            pair=run.pair,
            timeframe=run.timeframe,
            query=f'{run.pair} {run.timeframe} trend {market.get("trend", "unknown")}',
            limit=5,
        )

        context = AgentContext(
            pair=run.pair,
            timeframe=run.timeframe,
            mode=run.mode,
            risk_percent=risk_percent,
            market_snapshot=market,
            news_context=news,
            memory_context=memory_context,
        )

        try:
            analysis_bundle = self.analyze_context(context=context, db=db, run=run, record_steps=True, emit_step_logs=True)
            analysis_outputs = analysis_bundle['analysis_outputs']
            bullish = analysis_bundle['bullish']
            bearish = analysis_bundle['bearish']
            trader_decision = analysis_bundle['trader_decision']
            risk_output = analysis_bundle['risk']

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
                'status': execution_result.get('status', 'completed' if execution_result.get('executed') else 'skipped'),
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

            run.decision = {
                **trader_decision,
                'risk': risk_output,
                'execution': execution_result,
                'execution_manager': execution_output,
            }
            run.trace = {
                'market': market,
                'news': news,
                'analysis_outputs': analysis_outputs,
                'bullish': bullish,
                'bearish': bearish,
                'memory_context': memory_context,
                'requested_metaapi_account_ref': metaapi_account_ref,
                'workflow': list(self.WORKFLOW_STEPS),
            }
            run.status = 'completed'
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
