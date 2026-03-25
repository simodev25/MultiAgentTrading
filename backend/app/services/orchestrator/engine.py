import asyncio
import logging
import json
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import pandas as pd
from sqlalchemy.orm import Session
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange

from app.core.config import get_settings
from app.db.models.agent_step import AgentStep
from app.db.models.run import AnalysisRun
from app.db.session import SessionLocal
from app.observability.metrics import analysis_runs_total, orchestrator_step_duration_seconds
from app.observability.trace_context import trace_ctx
from app.services.execution.executor import ExecutionService
from app.services.llm.model_selector import AgentModelSelector
from app.services.market.news_provider import MarketProvider
from app.services.memory.memori_memory import MemoriMemoryService
from app.services.memory.vector_memory import VectorMemoryService
from app.services.orchestrator.agents import (
    AgentContext,
    BearishResearcherAgent,
    BullishResearcherAgent,
    ExecutionManagerAgent,
    MarketContextAnalystAgent,
    NewsAnalystAgent,
    RiskManagerAgent,
    TechnicalAnalystAgent,
    TraderAgent,
)
from app.services.prompts.registry import PromptTemplateService
from app.services.trading.account_selector import MetaApiAccountSelector
from app.services.trading.metaapi_client import MetaApiClient

logger = logging.getLogger(__name__)


class TradingOrchestrator:
    WORKFLOW_STEPS = (
        'technical-analyst',
        'news-analyst',
        'market-context-analyst',
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
        self.market_provider = MarketProvider()
        self.metaapi = MetaApiClient()
        self.account_selector = MetaApiAccountSelector()
        self.memory_service = VectorMemoryService()
        self.memori_memory_service = MemoriMemoryService()
        self.prompt_service = PromptTemplateService()
        self.execution_service = ExecutionService()
        self.model_selector = AgentModelSelector()

        self.technical_agent = TechnicalAnalystAgent()
        self.news_agent = NewsAnalystAgent(self.prompt_service)
        self.market_context_agent = MarketContextAnalystAgent()
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
            input_payload=self._json_safe(input_payload),
            output_payload=self._json_safe(output_payload),
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

    _JSON_SAFE_DROP_KEYS: frozenset[str] = frozenset({'_raw_candles'})

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {
                str(key): TradingOrchestrator._json_safe(item)
                for key, item in value.items()
                if key not in TradingOrchestrator._JSON_SAFE_DROP_KEYS
            }
        if isinstance(value, (list, tuple, set)):
            return [TradingOrchestrator._json_safe(item) for item in value]
        if hasattr(value, 'isoformat'):
            try:
                return value.isoformat()
            except Exception:
                pass
        if hasattr(value, 'item'):
            try:
                return TradingOrchestrator._json_safe(value.item())
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
        memory_runtime: dict[str, Any],
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
                'memory_runtime': self._json_safe(memory_runtime),
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

        execution_allowed = bool(trader_decision.get('execution_allowed', False))
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

    @staticmethod
    def _decision_gate_list(trader_decision: dict[str, Any]) -> list[str]:
        gates = trader_decision.get('decision_gates')
        if not isinstance(gates, list):
            rationale = trader_decision.get('rationale')
            if isinstance(rationale, dict):
                gates = rationale.get('decision_gates')
        if not isinstance(gates, list):
            return []
        return [str(item) for item in gates if str(item).strip()]

    def _should_trigger_second_pass(self, trader_decision: dict[str, Any]) -> tuple[bool, str]:
        if not self.settings.orchestrator_second_pass_enabled:
            return False, 'feature_disabled'

        decision = str(trader_decision.get('decision', 'HOLD') or '').strip().upper() or 'HOLD'
        if decision in {'BUY', 'SELL'}:
            return False, 'already_directional_decision'
        if bool(trader_decision.get('degraded', False)):
            return False, 'trader_output_degraded'

        gates = self._decision_gate_list(trader_decision)
        strong_conflict = bool(trader_decision.get('strong_conflict', False))
        needs_follow_up = bool(trader_decision.get('needs_follow_up', False))
        follow_up_reason = str(trader_decision.get('follow_up_reason') or '').strip().lower()
        combined_score = abs(float(trader_decision.get('combined_score', 0.0) or 0.0))
        min_second_pass_score = float(self.settings.orchestrator_second_pass_min_combined_score)

        if 'major_contradiction_execution_block' in gates:
            return False, 'major_contradiction_guardrail'
        if strong_conflict:
            return True, 'strong_conflict'
        if 'insufficient_aligned_sources' in gates and combined_score >= min_second_pass_score:
            return True, 'insufficient_aligned_sources_with_edge'
        if needs_follow_up and follow_up_reason in {'insufficient_evidence', 'low_edge'} and combined_score >= min_second_pass_score:
            return True, f'follow_up_{follow_up_reason}'

        return False, 'no_second_pass_condition'

    @staticmethod
    def _prefer_second_pass_result(
        primary: dict[str, Any],
        secondary: dict[str, Any],
    ) -> bool:
        primary_decision = str(primary.get('decision', 'HOLD') or '').strip().upper() or 'HOLD'
        secondary_decision = str(secondary.get('decision', 'HOLD') or '').strip().upper() or 'HOLD'
        primary_confidence = float(primary.get('confidence', 0.0) or 0.0)
        secondary_confidence = float(secondary.get('confidence', 0.0) or 0.0)

        if primary_decision == 'HOLD' and secondary_decision in {'BUY', 'SELL'} and bool(secondary.get('execution_allowed', False)):
            return True
        if primary_decision == 'HOLD' and secondary_decision == 'HOLD':
            primary_follow_up = bool(primary.get('needs_follow_up', False))
            secondary_follow_up = bool(secondary.get('needs_follow_up', False))
            if primary_follow_up and not secondary_follow_up and secondary_confidence >= primary_confidence:
                return True
            if secondary_confidence >= primary_confidence + 0.15:
                return True
        return False

    def _prefer_autonomy_bundle(
        self,
        primary_bundle: dict[str, Any],
        secondary_bundle: dict[str, Any],
    ) -> bool:
        primary_trader = primary_bundle.get('trader_decision')
        if not isinstance(primary_trader, dict):
            primary_trader = {}
        secondary_trader = secondary_bundle.get('trader_decision')
        if not isinstance(secondary_trader, dict):
            secondary_trader = {}

        if self._prefer_second_pass_result(primary_trader, secondary_trader):
            return True

        primary_decision = self._normalize_trade_decision(primary_trader.get('decision', 'HOLD'))
        secondary_decision = self._normalize_trade_decision(secondary_trader.get('decision', 'HOLD'))
        primary_confidence = self._safe_float(primary_trader.get('confidence'), 0.0)
        secondary_confidence = self._safe_float(secondary_trader.get('confidence'), 0.0)
        primary_evidence = self._safe_float(primary_trader.get('evidence_strength', primary_trader.get('evidence_quality')), 0.0)
        secondary_evidence = self._safe_float(secondary_trader.get('evidence_strength', secondary_trader.get('evidence_quality')), 0.0)
        primary_degraded_count = len(self._collect_bundle_degraded_agents(primary_bundle))
        secondary_degraded_count = len(self._collect_bundle_degraded_agents(secondary_bundle))

        if secondary_degraded_count < primary_degraded_count:
            return True

        if primary_decision == secondary_decision == 'HOLD':
            primary_follow_up = bool(primary_trader.get('needs_follow_up', False))
            secondary_follow_up = bool(secondary_trader.get('needs_follow_up', False))
            if primary_follow_up and not secondary_follow_up:
                return True
            return secondary_confidence >= primary_confidence + 0.10

        if primary_decision == secondary_decision and primary_decision in {'BUY', 'SELL'}:
            if secondary_evidence >= primary_evidence + 0.08 and secondary_confidence >= primary_confidence:
                return True
            return secondary_confidence >= primary_confidence + 0.10

        if primary_decision in {'BUY', 'SELL'} and secondary_decision in {'BUY', 'SELL'}:
            return bool(
                secondary_confidence >= primary_confidence + 0.20
                and secondary_evidence >= primary_evidence + 0.10
                and not bool(secondary_trader.get('strong_conflict', False))
            )

        return False

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _to_finite_float(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        return number

    @staticmethod
    def _resolve_requested_metaapi_account_ref(run: AnalysisRun, explicit_account_ref: int | None) -> int | None:
        if explicit_account_ref is not None:
            return int(explicit_account_ref)
        trace_payload = run.trace if isinstance(run.trace, dict) else {}
        raw_value = trace_payload.get('requested_metaapi_account_ref')
        try:
            resolved = int(raw_value or 0)
        except (TypeError, ValueError):
            return None
        return resolved or None

    @classmethod
    def _build_snapshot_from_market_candles(
        cls,
        *,
        pair: str,
        timeframe: str,
        candles: list[dict[str, Any]],
        symbol: str | None,
    ) -> dict[str, Any]:
        if not isinstance(candles, list) or not candles:
            return {'degraded': True, 'error': 'No market candles available', 'pair': pair, 'timeframe': timeframe}

        rows: list[dict[str, Any]] = []
        for candle in candles:
            if not isinstance(candle, dict):
                continue
            open_price = cls._to_finite_float(candle.get('open'))
            high = cls._to_finite_float(candle.get('high'))
            low = cls._to_finite_float(candle.get('low'))
            close_price = cls._to_finite_float(candle.get('close'))
            if open_price is None or high is None or low is None or close_price is None:
                continue
            rows.append(
                {
                    'time': candle.get('time'),
                    'Open': open_price,
                    'High': high,
                    'Low': low,
                    'Close': close_price,
                }
            )

        if len(rows) < 60:
            return {
                'degraded': True,
                'error': 'Insufficient market candles for indicators',
                'pair': pair,
                'timeframe': timeframe,
            }

        frame = pd.DataFrame(rows)
        frame['time'] = pd.to_datetime(frame['time'], utc=True, errors='coerce')
        frame = (
            frame.dropna(subset=['time'])
            .sort_values('time')
            .drop_duplicates(subset=['time'], keep='last')
            .set_index('time')
        )

        if len(frame) < 60:
            return {
                'degraded': True,
                'error': 'Insufficient chronological market candles for indicators',
                'pair': pair,
                'timeframe': timeframe,
            }

        # Candle freshness guard: reject obviously stale data (e.g. bare
        # symbol returning 2012-era candles for a .PRO instrument).
        _max_age_days: dict[str, int] = {
            'M1': 1, 'M5': 1, 'M15': 2, 'M30': 2,
            'H1': 3, 'H4': 5, 'D1': 10, 'W1': 21, 'MN1': 45,
        }
        _tf_upper = str(timeframe or '').strip().upper()
        _freshness_limit = _max_age_days.get(_tf_upper, 10)
        _latest_ts = frame.index[-1]
        if _latest_ts.tzinfo is None:
            _latest_ts = _latest_ts.tz_localize(timezone.utc)
        _candle_age = datetime.now(timezone.utc) - _latest_ts
        if _candle_age > timedelta(days=_freshness_limit):
            return {
                'degraded': True,
                'error': (
                    f'Stale candle data: latest candle is {_candle_age.days}d old '
                    f'(max {_freshness_limit}d for {_tf_upper})'
                ),
                'pair': pair,
                'timeframe': timeframe,
            }

        close = frame['Close']
        high = frame['High']
        low = frame['Low']

        try:
            rsi_raw = cls._to_finite_float(RSIIndicator(close=close, window=14).rsi().iloc[-1])
            ema_fast_raw = cls._to_finite_float(EMAIndicator(close=close, window=20).ema_indicator().iloc[-1])
            ema_slow_raw = cls._to_finite_float(EMAIndicator(close=close, window=50).ema_indicator().iloc[-1])
            macd_diff_raw = cls._to_finite_float(MACD(close=close).macd_diff().iloc[-1])
            atr_raw = cls._to_finite_float(AverageTrueRange(high=high, low=low, close=close).average_true_range().iloc[-1])
        except Exception as exc:
            return {
                'degraded': True,
                'error': f'Market indicator computation failed: {exc}',
                'pair': pair,
                'timeframe': timeframe,
            }

        latest = cls._to_finite_float(close.iloc[-1])
        prev = cls._to_finite_float(close.iloc[-2]) if len(close) > 1 else latest
        if (
            latest is None
            or prev is None
            or rsi_raw is None
            or ema_fast_raw is None
            or ema_slow_raw is None
            or macd_diff_raw is None
            or atr_raw is None
        ):
            return {
                'degraded': True,
                'error': 'Market indicators unavailable from candles',
                'pair': pair,
                'timeframe': timeframe,
            }

        pct_change = ((latest - prev) / prev) * 100 if prev != 0 else 0.0
        trend = 'bullish' if ema_fast_raw > ema_slow_raw else 'bearish'
        if abs(ema_fast_raw - ema_slow_raw) < latest * 0.0003:
            trend = 'neutral'

        return {
            'degraded': False,
            'pair': pair,
            'timeframe': timeframe,
            'symbol': symbol,
            'last_price': latest,
            'change_pct': round(float(pct_change), 5),
            'rsi': round(float(rsi_raw), 3),
            'ema_fast': round(float(ema_fast_raw), 6),
            'ema_slow': round(float(ema_slow_raw), 6),
            'macd_diff': round(float(macd_diff_raw), 6),
            'atr': round(float(atr_raw), 6),
            'trend': trend,
        }

    def _market_snapshot_with_metaapi_context(
        self,
        *,
        pair: str,
        timeframe: str,
        metaapi_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = self.market_provider.get_market_snapshot(pair, timeframe)
        payload = dict(snapshot) if isinstance(snapshot, dict) else {
            'degraded': True,
            'error': 'Market snapshot unavailable',
            'pair': pair,
            'timeframe': timeframe,
        }
        if metaapi_payload is None:
            payload.setdefault('market_data_source', 'yfinance')
            return payload

        payload['market_data_source'] = 'yfinance_fallback'
        payload['metaapi_market_data_degraded'] = True
        payload['metaapi_market_data_reason'] = (
            str(metaapi_payload.get('reason') or metaapi_payload.get('error') or '').strip() or None
        )
        payload['metaapi_market_data_provider'] = metaapi_payload.get('provider')
        payload['metaapi_requested_symbol'] = metaapi_payload.get('requested_symbol')
        payload['metaapi_tried_symbols'] = metaapi_payload.get('tried_symbols', [])
        return payload

    async def resolve_market_snapshot(
        self,
        db: Session,
        *,
        pair: str,
        timeframe: str,
        metaapi_account_ref: int | None = None,
    ) -> dict[str, Any]:
        if not bool(self.settings.metaapi_use_sdk_for_market_data):
            return self._market_snapshot_with_metaapi_context(pair=pair, timeframe=timeframe)

        selected_account = self.account_selector.resolve(db, metaapi_account_ref)
        _acct_id = selected_account.account_id if selected_account is not None else None
        _acct_region = selected_account.region if selected_account is not None else None

        # Fetch candles and current tick in parallel
        metaapi_market, current_tick = await asyncio.gather(
            self.metaapi.get_market_candles(
                pair=pair,
                timeframe=timeframe,
                limit=240,
                account_id=_acct_id,
                region=_acct_region,
            ),
            self.metaapi.get_current_tick(
                symbol=pair,
                account_id=_acct_id,
                region=_acct_region,
            ),
        )
        if bool(metaapi_market.get('degraded', False)):
            logger.warning(
                'metaapi market snapshot degraded pair=%s timeframe=%s account_id=%s reason=%s',
                pair,
                timeframe,
                selected_account.account_id if selected_account is not None else str(self.settings.metaapi_account_id or '').strip() or 'default',
                metaapi_market.get('reason'),
            )
            return self._market_snapshot_with_metaapi_context(
                pair=pair,
                timeframe=timeframe,
                metaapi_payload=metaapi_market,
            )

        snapshot = self._build_snapshot_from_market_candles(
            pair=pair,
            timeframe=timeframe,
            candles=metaapi_market.get('candles', []) if isinstance(metaapi_market.get('candles'), list) else [],
            symbol=str(metaapi_market.get('symbol') or '').strip() or pair,
        )
        if bool(snapshot.get('degraded', False)):
            logger.warning(
                'metaapi market snapshot indicator build failed pair=%s timeframe=%s account_id=%s reason=%s',
                pair,
                timeframe,
                selected_account.account_id if selected_account is not None else str(self.settings.metaapi_account_id or '').strip() or 'default',
                snapshot.get('error'),
            )
            return self._market_snapshot_with_metaapi_context(
                pair=pair,
                timeframe=timeframe,
                metaapi_payload={
                    **metaapi_market,
                    'reason': snapshot.get('error') or metaapi_market.get('reason'),
                },
            )

        snapshot['_raw_candles'] = metaapi_market.get('candles', []) if isinstance(metaapi_market.get('candles'), list) else []
        snapshot['market_data_source'] = 'metaapi'
        snapshot['market_data_provider'] = metaapi_market.get('provider')
        snapshot['requested_symbol'] = metaapi_market.get('requested_symbol')
        snapshot['tried_symbols'] = metaapi_market.get('tried_symbols', [])
        if selected_account is not None:
            snapshot['metaapi_account_ref'] = selected_account.id
            snapshot['metaapi_account_label'] = selected_account.label
        snapshot['metaapi_account_id'] = (
            selected_account.account_id if selected_account is not None else str(self.settings.metaapi_account_id or '').strip() or None
        )

        # Merge bid/ask/spread from current tick data
        if isinstance(current_tick, dict) and not bool(current_tick.get('degraded', False)):
            snapshot['bid'] = current_tick.get('bid')
            snapshot['ask'] = current_tick.get('ask')
            snapshot['spread'] = current_tick.get('spread')

        # Cross-validate when MetaApi resolved to a fallback symbol.
        # If the primary candidate (e.g. USDJPY.pro) failed and the bare
        # symbol (USDJPY) was used instead, its price may come from a
        # different instrument.  Compare against YFinance to detect this.
        _tried = metaapi_market.get('tried_symbols', [])
        _resolved_sym = str(metaapi_market.get('symbol') or '').strip().upper()
        _primary_sym = str(_tried[0]).strip().upper() if _tried else ''
        if _primary_sym and _resolved_sym and _resolved_sym != _primary_sym:
            try:
                _yf_ref = self.market_provider.get_market_snapshot(pair, timeframe)
                if not bool(_yf_ref.get('degraded', False)):
                    _yf_price = float(_yf_ref.get('last_price', 0))
                    _meta_price = float(snapshot.get('last_price', 0))
                    if _yf_price > 0 and _meta_price > 0:
                        _divergence = abs(_meta_price - _yf_price) / _yf_price
                        if _divergence > 0.25:
                            logger.warning(
                                'MetaApi fallback symbol %s price %.5f diverges %.1f%% from YFinance %.5f for %s — using YFinance',
                                _resolved_sym,
                                _meta_price,
                                _divergence * 100,
                                _yf_price,
                                pair,
                            )
                            return self._market_snapshot_with_metaapi_context(
                                pair=pair,
                                timeframe=timeframe,
                                metaapi_payload=metaapi_market,
                            )
            except Exception as exc:
                logger.debug('YFinance cross-validation failed for %s: %s', pair, exc)

        return snapshot

    async def _fetch_multi_tf_snapshots(
        self,
        db: Session,
        *,
        pair: str,
        current_timeframe: str,
        symbol: str,
        metaapi_account_ref: int | None = None,
    ) -> dict[str, dict[str, Any]]:
        from app.services.orchestrator.agents import _higher_timeframes

        higher_tfs = _higher_timeframes(current_timeframe, max_count=2)
        if not higher_tfs:
            return {}

        async def _fetch_one(tf: str) -> tuple[str, dict[str, Any]]:
            try:
                candles = await self.resolve_recent_candles(
                    db,
                    pair=pair,
                    timeframe=tf,
                    limit=200,
                    metaapi_account_ref=metaapi_account_ref,
                )
                if not candles:
                    return tf, {'degraded': True, 'error': 'no candles', 'timeframe': tf}
                raw = [
                    {'open': c.get('open'), 'high': c.get('high'), 'low': c.get('low'), 'close': c.get('close'), 'time': c.get('ts', c.get('time', ''))}
                    for c in candles if isinstance(c, dict)
                ]
                snapshot = self._build_snapshot_from_market_candles(
                    pair=pair, timeframe=tf, candles=raw, symbol=symbol,
                )
                snapshot['timeframe'] = tf
                return tf, snapshot
            except Exception as exc:
                logger.warning('multi_tf fetch failed tf=%s pair=%s: %s', tf, pair, exc)
                return tf, {'degraded': True, 'error': str(exc), 'timeframe': tf}

        results = await asyncio.gather(*[_fetch_one(tf) for tf in higher_tfs])
        return dict(results)

    async def resolve_recent_candles(
        self,
        db: Session,
        *,
        pair: str,
        timeframe: str,
        limit: int = 200,
        metaapi_account_ref: int | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(int(limit or 1), 1)
        if not bool(self.settings.metaapi_use_sdk_for_market_data):
            return self.market_provider.get_recent_candles(pair, timeframe, limit=safe_limit)

        selected_account = self.account_selector.resolve(db, metaapi_account_ref)
        metaapi_market = await self.metaapi.get_market_candles(
            pair=pair,
            timeframe=timeframe,
            limit=safe_limit,
            account_id=selected_account.account_id if selected_account is not None else None,
            region=selected_account.region if selected_account is not None else None,
        )
        if bool(metaapi_market.get('degraded', False)):
            logger.warning(
                'metaapi recent candles degraded pair=%s timeframe=%s account_id=%s reason=%s',
                pair,
                timeframe,
                selected_account.account_id if selected_account is not None else str(self.settings.metaapi_account_id or '').strip() or 'default',
                metaapi_market.get('reason'),
            )
            return self.market_provider.get_recent_candles(pair, timeframe, limit=safe_limit)

        items = metaapi_market.get('candles', [])
        if not isinstance(items, list) or not items:
            return self.market_provider.get_recent_candles(pair, timeframe, limit=safe_limit)

        normalized: list[dict[str, Any]] = []
        for candle in items:
            if not isinstance(candle, dict):
                continue
            close_value = self._to_finite_float(candle.get('close'))
            if close_value is None:
                continue
            normalized.append(
                {
                    'ts': str(candle.get('time') or '').strip(),
                    'open': self._to_finite_float(candle.get('open')),
                    'high': self._to_finite_float(candle.get('high')),
                    'low': self._to_finite_float(candle.get('low')),
                    'close': close_value,
                    'volume': self._to_finite_float(candle.get('volume')),
                }
            )
        return normalized[-safe_limit:]

    @staticmethod
    def _normalize_trade_decision(value: Any) -> str:
        decision = str(value or '').strip().upper()
        if decision in {'BUY', 'SELL', 'HOLD'}:
            return decision
        return 'HOLD'

    @staticmethod
    def _build_memori_query(
        *,
        pair: str,
        timeframe: str,
        market: dict[str, Any],
        decision_mode: str,
        memory_retrieval_context: dict[str, Any],
    ) -> str:
        context = memory_retrieval_context if isinstance(memory_retrieval_context, dict) else {}
        trend = str(market.get('trend', context.get('trend', 'unknown')) or 'unknown')
        technical_signal = str(context.get('technical_signal', trend) or trend)
        rsi_bucket = str(context.get('rsi_bucket', 'unknown') or 'unknown')
        macd_state = str(context.get('macd_state', 'unknown') or 'unknown')
        volatility_regime = str(context.get('volatility_regime', 'unknown') or 'unknown')
        contradiction_level = str(context.get('contradiction_level', 'unknown') or 'unknown')
        resolved_mode = str(decision_mode or context.get('decision_mode', 'balanced') or 'balanced')
        return (
            f'{pair} {timeframe} trend {trend} technical_signal {technical_signal} '
            f'rsi_bucket {rsi_bucket} macd_state {macd_state} volatility {volatility_regime} '
            f'contradiction {contradiction_level} decision_mode {resolved_mode}'
        )

    @staticmethod
    def _merge_memory_contexts(
        vector_items: list[dict[str, Any]],
        memori_items: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        effective_limit = max(int(limit), 1)

        for item in list(vector_items or []) + list(memori_items or []):
            if not isinstance(item, dict):
                continue
            summary = str(item.get('summary', '') or '').strip()
            source_type = str(item.get('source_type', '') or '').strip().lower()
            dedupe_key = (source_type, summary)
            if summary and dedupe_key in seen:
                continue
            if summary:
                seen.add(dedupe_key)
            merged.append(item)
            if len(merged) >= effective_limit:
                break
        return merged

    def _load_memory_state(
        self,
        *,
        db: Session,
        pair: str,
        timeframe: str,
        market: dict[str, Any],
        decision_mode: str,
        memory_retrieval_context: dict[str, Any],
        memory_context_enabled: bool,
        limit: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        memori_enabled = bool(getattr(self.settings, 'memori_enabled', False))
        memori_limit = max(1, int(getattr(self.settings, 'memori_recall_limit', 3) or 3))
        memori_meta: dict[str, Any] = {
            'enabled': memori_enabled,
            'available': False,
            'returned_count': 0,
            'error': None,
        }
        if not memory_context_enabled:
            memori_meta['error'] = 'memory_context_disabled'
            memory_signal = self.memory_service.empty_memory_signal(
                'memory_context_disabled',
                retrieved_count=0,
                decision_mode=decision_mode,
            )
            memory_runtime = {
                'context_enabled': False,
                'combined_context_count': 0,
                'sources': {'vector': 0, 'memori': 0},
                'vector': {'retrieved_count': 0, 'limit': 0, 'used_for_signal': False},
                'memori': memori_meta,
            }
            return [], memory_signal, memory_runtime

        effective_limit = max(1, int(limit))
        vector_memory_context = self.memory_service.search(
            db=db,
            pair=pair,
            timeframe=timeframe,
            query=f'{pair} {timeframe} trend {market.get("trend", "unknown")}',
            limit=effective_limit,
            retrieval_context=memory_retrieval_context,
        )
        memory_signal = self.memory_service.compute_memory_signal(
            vector_memory_context,
            market_snapshot=market,
            decision_mode=decision_mode,
        )

        memori_query = self._build_memori_query(
            pair=pair,
            timeframe=timeframe,
            market=market,
            decision_mode=decision_mode,
            memory_retrieval_context=memory_retrieval_context,
        )
        memori_context, memori_meta = self.memori_memory_service.recall(
            pair=pair,
            timeframe=timeframe,
            query=memori_query,
            limit=memori_limit,
        )
        combined_limit = min(max(effective_limit + memori_limit, effective_limit), 60)
        combined_context = self._merge_memory_contexts(
            vector_memory_context,
            memori_context,
            limit=combined_limit,
        )

        memory_runtime = {
            'context_enabled': True,
            'combined_context_count': len(combined_context),
            'sources': {
                'vector': len(vector_memory_context),
                'memori': len(memori_context),
            },
            'vector': {
                'retrieved_count': len(vector_memory_context),
                'limit': effective_limit,
                'used_for_signal': bool(memory_signal.get('used', False)),
            },
            'memori': {
                **(memori_meta if isinstance(memori_meta, dict) else {}),
                'query': memori_query,
                'limit': memori_limit,
            },
        }
        return combined_context, memory_signal, memory_runtime

    def _collect_bundle_degraded_agents(self, analysis_bundle: dict[str, Any]) -> list[str]:
        analysis_outputs = analysis_bundle.get('analysis_outputs')
        if not isinstance(analysis_outputs, dict):
            analysis_outputs = {}
        named_outputs: dict[str, dict[str, Any] | None] = {
            **analysis_outputs,
            self.bullish_researcher.name: analysis_bundle.get('bullish'),
            self.bearish_researcher.name: analysis_bundle.get('bearish'),
            self.trader_agent.name: analysis_bundle.get('trader_decision'),
            self.risk_manager_agent.name: analysis_bundle.get('risk'),
        }
        return sorted(dict.fromkeys(self._collect_degraded_agents(named_outputs)))

    def _build_autonomy_cycle_assessment(
        self,
        *,
        cycle_index: int,
        max_cycles: int,
        analysis_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        trader_decision = analysis_bundle.get('trader_decision')
        if not isinstance(trader_decision, dict):
            trader_decision = {}
        risk_output = analysis_bundle.get('risk')
        if not isinstance(risk_output, dict):
            risk_output = {}

        decision = self._normalize_trade_decision(trader_decision.get('decision', 'HOLD'))
        confidence = self._safe_float(trader_decision.get('confidence'), 0.0)
        evidence_strength = self._safe_float(
            trader_decision.get(
                'evidence_strength',
                trader_decision.get('evidence_quality', trader_decision.get('confidence', 0.0)),
            ),
            0.0,
        )
        combined_score = abs(self._safe_float(trader_decision.get('combined_score'), 0.0))
        uncertainty_level = str(trader_decision.get('uncertainty_level') or 'high').strip().lower() or 'high'
        needs_follow_up = bool(trader_decision.get('needs_follow_up', False))
        follow_up_reason = str(trader_decision.get('follow_up_reason') or '').strip().lower() or None
        strong_conflict = bool(trader_decision.get('strong_conflict', False))
        execution_allowed = bool(trader_decision.get('execution_allowed', False))
        risk_accepted = bool(risk_output.get('accepted', False))
        degraded_agents = self._collect_bundle_degraded_agents(analysis_bundle)
        should_second_pass, second_pass_reason = self._should_trigger_second_pass(trader_decision)

        remaining_cycles = max(max_cycles - (cycle_index + 1), 0)
        action = 'accept'
        action_reason = 'decision_ready'
        if not self.settings.orchestrator_autonomy_enabled:
            action = 'accept'
            action_reason = 'autonomy_disabled'
        elif remaining_cycles > 0 and degraded_agents:
            action = 'rerun_due_to_degraded_outputs'
            action_reason = 'degraded_outputs'
        elif remaining_cycles > 0 and should_second_pass:
            if strong_conflict:
                action = 'rerun_with_conflict_focus'
            elif follow_up_reason in {'insufficient_evidence', 'low_edge'}:
                action = 'rerun_with_memory_refresh'
            else:
                action = 'rerun_second_pass'
            action_reason = second_pass_reason
        elif decision in {'BUY', 'SELL'} and execution_allowed and risk_accepted:
            min_confidence = float(self.settings.orchestrator_autonomy_accept_min_confidence)
            min_evidence = float(self.settings.orchestrator_autonomy_accept_min_evidence)
            if confidence < min_confidence or evidence_strength < min_evidence:
                if remaining_cycles > 0:
                    action = 'rerun_with_conflict_focus'
                    action_reason = 'directional_low_quality'
                else:
                    action = 'accept_with_low_quality'
                    action_reason = 'cycle_cap_reached'
            else:
                action = 'accept'
                action_reason = 'directional_quality_ok'
        elif decision == 'HOLD' and (needs_follow_up or strong_conflict) and remaining_cycles == 0:
            action = 'finalize_hold'
            action_reason = 'cycle_cap_reached_for_hold'

        return {
            'cycle': cycle_index + 1,
            'remaining_cycles': remaining_cycles,
            'decision': decision,
            'confidence': round(confidence, 4),
            'combined_score': round(combined_score, 4),
            'evidence_strength': round(evidence_strength, 4),
            'uncertainty_level': uncertainty_level,
            'needs_follow_up': needs_follow_up,
            'follow_up_reason': follow_up_reason,
            'strong_conflict': strong_conflict,
            'execution_allowed': execution_allowed,
            'risk_accepted': risk_accepted,
            'degraded_agents': degraded_agents,
            'should_second_pass': should_second_pass,
            'second_pass_reason': second_pass_reason,
            'action': action,
            'action_reason': action_reason,
            'should_rerun': action.startswith('rerun_') and remaining_cycles > 0,
        }

    def _build_autonomy_model_overrides(
        self,
        *,
        db: Session,
        action: str,
        degraded_agents: list[str],
    ) -> dict[str, str]:
        if not self.settings.orchestrator_autonomy_model_boost_enabled:
            return {}

        default_model = str(self.model_selector.resolve(db)).strip()
        if not default_model:
            return {}

        target_agents: set[str] = set()
        if action in {'rerun_with_conflict_focus', 'rerun_second_pass'}:
            target_agents.update(
                {
                    self.news_agent.name,
                    self.bullish_researcher.name,
                    self.bearish_researcher.name,
                    self.trader_agent.name,
                }
            )
        if action == 'rerun_due_to_degraded_outputs':
            target_agents.update(str(name).strip() for name in degraded_agents if str(name).strip())

        if not target_agents:
            return {}

        overrides: dict[str, str] = {}
        for agent_name in sorted(target_agents):
            current_model = str(self.model_selector.resolve(db, agent_name)).strip()
            if current_model and current_model != default_model:
                overrides[agent_name] = default_model
        return overrides

    @staticmethod
    def _selected_pass_label(selected_cycle: int) -> str:
        if selected_cycle <= 1:
            return 'first'
        if selected_cycle == 2:
            return 'second'
        return f'pass-{selected_cycle}'

    def _build_second_pass_meta_from_autonomy(self, autonomy_meta: dict[str, Any]) -> dict[str, Any]:
        cycles = autonomy_meta.get('cycles')
        if not isinstance(cycles, list):
            cycles = []
        first_cycle = cycles[0] if cycles else {}
        second_cycle = cycles[1] if len(cycles) > 1 else {}
        selected_cycle = max(int(autonomy_meta.get('selected_cycle', 1) or 1), 1)
        attempt_count = max(int(autonomy_meta.get('second_pass_attempt_count', 0) or 0), 0)
        attempted = attempt_count > 0

        trigger_reason: str | None = None
        for cycle in cycles:
            if not isinstance(cycle, dict):
                continue
            if bool(cycle.get('should_second_pass')) and str(cycle.get('action') or '').startswith('rerun_'):
                trigger_reason = str(cycle.get('action_reason') or '') or None
                break

        result: dict[str, Any] = {
            'enabled': bool(self.settings.orchestrator_second_pass_enabled),
            'attempted': attempted,
            'attempt_count': attempt_count,
            'trigger_reason': trigger_reason,
            'selected_pass': self._selected_pass_label(selected_cycle),
            'first_decision': self._normalize_trade_decision(first_cycle.get('decision', 'HOLD')),
            'first_confidence': self._safe_float(first_cycle.get('confidence'), 0.0),
        }
        if attempted and second_cycle:
            result['second_decision'] = self._normalize_trade_decision(second_cycle.get('decision', 'HOLD'))
            result['second_confidence'] = self._safe_float(second_cycle.get('confidence'), 0.0)
            result['used_second_pass_result'] = selected_cycle >= 2
        else:
            result['skip_reason'] = str(first_cycle.get('action_reason') or 'no_second_pass_condition')
        return result

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
                    try:
                        output, elapsed = future.result()
                    except Exception as exc:
                        logger.warning("Parallel agent %s failed: %s", agent_name, exc, exc_info=True)
                        output = {'error': f'{type(exc).__name__}: agent execution failed', 'agent': agent_name}
                        elapsed = 0.0
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
                    self.market_context_agent.name,
                    {'market': context.market_snapshot},
                    lambda local_db: self.market_context_agent.run(context, db=local_db),
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
        trace_ctx.set(correlation_id=f'run-{run_id}', causation_id=f'execute-{run_id}')
        run.status = 'running'
        db.commit()
        db.refresh(run)

        self._ensure_prompt_defaults(self.prompt_service, db)

        metaapi_account_ref = self._resolve_requested_metaapi_account_ref(run, metaapi_account_ref)
        market = await self.resolve_market_snapshot(
            db,
            pair=run.pair,
            timeframe=run.timeframe,
            metaapi_account_ref=metaapi_account_ref,
        )
        news = self.market_provider.get_news_context(run.pair)
        memory_context_enabled = self.model_selector.resolve_memory_context_enabled(db)
        decision_mode = self.model_selector.resolve_decision_mode(db)
        memory_retrieval_context = self.memory_service.build_retrieval_context(
            market,
            decision_mode=decision_mode,
        )
        memory_limit = max(int(self.settings.orchestrator_memory_search_limit), 1)
        memory_context, memory_signal, memory_runtime_meta = self._load_memory_state(
            db=db,
            pair=run.pair,
            timeframe=run.timeframe,
            market=market,
            decision_mode=decision_mode,
            memory_retrieval_context=memory_retrieval_context,
            memory_context_enabled=memory_context_enabled,
            limit=memory_limit,
        )

        raw_candles = market.pop('_raw_candles', [])
        multi_tf_snapshots = await self._fetch_multi_tf_snapshots(
            db,
            pair=run.pair,
            current_timeframe=run.timeframe,
            symbol=str(market.get('symbol') or run.pair),
            metaapi_account_ref=metaapi_account_ref,
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
            llm_model_overrides={},
            price_history=raw_candles,
            multi_tf_snapshots=multi_tf_snapshots,
        )
        price_history: list[dict[str, Any]] = list(raw_candles) if raw_candles else []
        if self.settings.debug_trade_json_enabled and self.settings.debug_trade_json_include_price_history:
            debug_limit = self.settings.debug_trade_json_price_history_limit
            if len(price_history) < debug_limit:
                try:
                    price_history = await self.resolve_recent_candles(
                        db,
                        pair=run.pair,
                        timeframe=run.timeframe,
                        limit=debug_limit,
                        metaapi_account_ref=metaapi_account_ref,
                    )
                except Exception:
                    logger.exception('debug price history fetch failed run_id=%s', run_id)

        try:
            autonomy_enabled = bool(self.settings.orchestrator_autonomy_enabled)
            max_cycles = max(int(self.settings.orchestrator_autonomy_max_cycles), 1)
            if not autonomy_enabled:
                max_cycles = 1

            max_second_pass_attempts = max(int(self.settings.orchestrator_second_pass_max_attempts), 0)
            second_pass_attempt_count = 0
            cycle_summaries: list[dict[str, Any]] = []

            selected_bundle: dict[str, Any] | None = None
            selected_cycle = 1
            selected_memory_context: list[dict[str, Any]] = list(memory_context)
            selected_memory_signal: dict[str, Any] = dict(memory_signal)
            selected_memory_runtime_meta: dict[str, Any] = self._json_safe(memory_runtime_meta)
            selected_model_overrides: dict[str, str] = dict(context.llm_model_overrides or {})

            current_memory_context: list[dict[str, Any]] = list(memory_context)
            current_memory_signal: dict[str, Any] = dict(memory_signal)
            current_memory_runtime_meta: dict[str, Any] = self._json_safe(memory_runtime_meta)
            current_model_overrides: dict[str, str] = {}

            for cycle_index in range(max_cycles):
                context.memory_context = current_memory_context
                context.memory_signal = current_memory_signal
                context.llm_model_overrides = dict(current_model_overrides)

                candidate_bundle = self.analyze_context(
                    context=context,
                    db=db,
                    run=run,
                    record_steps=True,
                    emit_step_logs=True,
                )

                if selected_bundle is None:
                    selected_bundle = candidate_bundle
                    selected_cycle = cycle_index + 1
                    selected_memory_context = list(current_memory_context)
                    selected_memory_signal = dict(current_memory_signal)
                    selected_memory_runtime_meta = self._json_safe(current_memory_runtime_meta)
                    selected_model_overrides = dict(current_model_overrides)
                elif self._prefer_autonomy_bundle(selected_bundle, candidate_bundle):
                    selected_bundle = candidate_bundle
                    selected_cycle = cycle_index + 1
                    selected_memory_context = list(current_memory_context)
                    selected_memory_signal = dict(current_memory_signal)
                    selected_memory_runtime_meta = self._json_safe(current_memory_runtime_meta)
                    selected_model_overrides = dict(current_model_overrides)

                cycle_assessment = self._build_autonomy_cycle_assessment(
                    cycle_index=cycle_index,
                    max_cycles=max_cycles,
                    analysis_bundle=candidate_bundle,
                )
                cycle_assessment['memory_context_count'] = len(current_memory_context)
                cycle_assessment['memory_limit'] = memory_limit
                cycle_assessment['memory_signal_used'] = bool(current_memory_signal.get('used', False))
                cycle_assessment['memori_context_count'] = int(
                    ((current_memory_runtime_meta.get('sources') or {}).get('memori', 0) or 0)
                )
                cycle_assessment['memori_available'] = bool(
                    ((current_memory_runtime_meta.get('memori') or {}).get('available', False))
                )
                cycle_assessment['llm_model_overrides'] = dict(current_model_overrides)

                if bool(cycle_assessment.get('should_second_pass')) and bool(cycle_assessment.get('should_rerun')):
                    if not self.settings.orchestrator_second_pass_enabled:
                        cycle_assessment['should_rerun'] = False
                        cycle_assessment['action'] = 'accept'
                        cycle_assessment['action_reason'] = 'second_pass_feature_disabled'
                    elif second_pass_attempt_count >= max_second_pass_attempts:
                        cycle_assessment['should_rerun'] = False
                        cycle_assessment['action'] = 'finalize_hold'
                        cycle_assessment['action_reason'] = 'second_pass_attempt_limit_reached'

                if bool(cycle_assessment.get('should_rerun')) and cycle_summaries:
                    previous = cycle_summaries[-1]
                    previous_degraded = previous.get('degraded_agents')
                    current_degraded = cycle_assessment.get('degraded_agents')
                    stagnating = bool(
                        previous.get('action') == cycle_assessment.get('action')
                        and previous.get('decision') == cycle_assessment.get('decision')
                        and previous_degraded == current_degraded
                        and abs(self._safe_float(previous.get('confidence')) - self._safe_float(cycle_assessment.get('confidence'))) <= 0.02
                        and abs(self._safe_float(previous.get('combined_score')) - self._safe_float(cycle_assessment.get('combined_score'))) <= 0.02
                    )
                    if stagnating:
                        cycle_assessment['should_rerun'] = False
                        cycle_assessment['action'] = (
                            'finalize_hold'
                            if str(cycle_assessment.get('decision') or 'HOLD').upper() == 'HOLD'
                            else 'accept'
                        )
                        cycle_assessment['action_reason'] = 'stagnation_guardrail'

                cycle_summaries.append(cycle_assessment)

                if bool(cycle_assessment.get('should_rerun')) and bool(cycle_assessment.get('should_second_pass')):
                    second_pass_attempt_count += 1

                if not bool(cycle_assessment.get('should_rerun')):
                    break

                rerun_action = str(cycle_assessment.get('action') or '')
                if memory_context_enabled and rerun_action == 'rerun_with_memory_refresh':
                    next_limit = min(
                        memory_limit + int(self.settings.orchestrator_autonomy_memory_limit_step),
                        max(int(self.settings.orchestrator_autonomy_memory_limit_max), memory_limit),
                    )
                    memory_limit = max(next_limit, memory_limit)
                    (
                        current_memory_context,
                        current_memory_signal,
                        current_memory_runtime_meta,
                    ) = self._load_memory_state(
                        db=db,
                        pair=run.pair,
                        timeframe=run.timeframe,
                        market=market,
                        decision_mode=decision_mode,
                        memory_retrieval_context=memory_retrieval_context,
                        memory_context_enabled=memory_context_enabled,
                        limit=memory_limit,
                    )

                current_model_overrides = self._build_autonomy_model_overrides(
                    db=db,
                    action=rerun_action,
                    degraded_agents=list(cycle_assessment.get('degraded_agents', [])),
                )

            if selected_bundle is None:
                raise RuntimeError('Orchestrator produced no analysis bundle.')

            analysis_bundle = selected_bundle
            analysis_outputs = analysis_bundle['analysis_outputs']
            bullish = analysis_bundle['bullish']
            bearish = analysis_bundle['bearish']
            trader_decision = analysis_bundle['trader_decision']
            risk_output = analysis_bundle['risk']

            memory_context = selected_memory_context
            memory_signal = selected_memory_signal
            memory_runtime_meta = selected_memory_runtime_meta
            context.memory_context = memory_context
            context.memory_signal = memory_signal
            context.llm_model_overrides = selected_model_overrides

            autonomy_meta = {
                'enabled': autonomy_enabled,
                'max_cycles': max_cycles,
                'executed_cycles': len(cycle_summaries),
                'selected_cycle': selected_cycle,
                'second_pass_attempt_count': second_pass_attempt_count,
                'selected_pass': self._selected_pass_label(selected_cycle),
                'cycles': cycle_summaries,
            }
            second_pass_meta = self._build_second_pass_meta_from_autonomy(autonomy_meta)

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
                'second_pass': second_pass_meta,
                'runtime_supervisor': autonomy_meta,
                'memory_runtime': memory_runtime_meta,
            }
            run.status = 'completed'
            trace_payload = {
                'trace_ids': trace_ctx.as_dict(),
                'market': self._json_safe(market),
                'news': news,
                'analysis_outputs': analysis_outputs,
                'bullish': bullish,
                'bearish': bearish,
                'memory_context': memory_context,
                'memory_context_enabled': memory_context_enabled,
                'memory_signal': memory_signal,
                'memory_runtime': memory_runtime_meta,
                'memory_retrieval_context': memory_retrieval_context,
                'requested_metaapi_account_ref': metaapi_account_ref,
                'workflow': list(self.WORKFLOW_STEPS),
                'second_pass': second_pass_meta,
                'runtime_supervisor': autonomy_meta,
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
                    memory_runtime=memory_runtime_meta,
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

            vector_memory_meta: dict[str, Any] = {
                'stored': False,
                'entry_id': None,
                'error': None,
            }
            try:
                vector_entry = self.memory_service.add_run_memory(db, run)
                vector_memory_meta['stored'] = True
                if vector_entry is not None:
                    vector_memory_meta['entry_id'] = getattr(vector_entry, 'id', None)
            except Exception as exc:
                logger.exception('vector memory persistence failed run_id=%s', run.id)
                vector_memory_meta['error'] = f'{type(exc).__name__}: vector memory persistence failed'

            memori_store_meta = self.memori_memory_service.store_run_memory(run)
            memory_persistence_meta = {
                'vector': vector_memory_meta,
                'memori': self._json_safe(memori_store_meta),
            }

            if isinstance(run.trace, dict):
                run.trace = {**run.trace, 'memory_persistence': memory_persistence_meta}
            else:
                run.trace = {'memory_persistence': memory_persistence_meta}

            if isinstance(run.decision, dict):
                run.decision = {**run.decision, 'memory_persistence': memory_persistence_meta}
            else:
                run.decision = {'memory_persistence': memory_persistence_meta}

            db.commit()
            db.refresh(run)
            analysis_runs_total.labels(status='completed').inc()
            return run
        except Exception as exc:
            logger.exception('orchestration failed run_id=%s', run_id)
            db.rollback()
            failed_run = db.get(AnalysisRun, run_id)
            if failed_run is None:
                raise
            failed_run.status = 'failed'
            failed_run.error = f'{type(exc).__name__}: analysis failed'
            db.commit()
            db.refresh(failed_run)
            analysis_runs_total.labels(status='failed').inc()
            return failed_run


# Backward-compatible alias
ForexOrchestrator = TradingOrchestrator
