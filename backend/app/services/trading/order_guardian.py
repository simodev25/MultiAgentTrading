from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.connector_config import ConnectorConfig
from app.schemas.order_guardian import OrderGuardianStatusUpdate
from app.services.llm.model_selector import AgentModelSelector
from app.services.llm.ollama_client import OllamaCloudClient
from app.services.orchestrator.agents import AgentContext
from app.services.orchestrator.engine import ForexOrchestrator
from app.services.prompts.registry import PromptTemplateService
from app.services.trading.account_selector import MetaApiAccountSelector
from app.services.trading.metaapi_client import MetaApiClient

logger = logging.getLogger(__name__)


class OrderGuardianService:
    CONNECTOR_NAME = 'order-guardian'
    DEFAULT_TIMEFRAME = 'H1'
    DEFAULT_RISK_PERCENT = 1.0
    DEFAULT_MAX_POSITIONS_PER_CYCLE = 10
    DEFAULT_SL_TP_MIN_DELTA = 0.0002
    LLM_AGENT_NAMES = (
        'technical-analyst',
        'news-analyst',
        'macro-analyst',
        'sentiment-agent',
        'bullish-researcher',
        'bearish-researcher',
        'trader-agent',
    )

    def __init__(self) -> None:
        self.metaapi = MetaApiClient()
        self.account_selector = MetaApiAccountSelector()
        self.orchestrator = ForexOrchestrator()
        self.llm = OllamaCloudClient()
        self.model_selector = AgentModelSelector()
        self.prompt_service = PromptTemplateService()

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            result = float(value)
            if math.isfinite(result):
                return result
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                result = float(text)
                if math.isfinite(result):
                    return result
            except ValueError:
                return None
        return None

    @staticmethod
    def _to_positive_float(value: Any) -> float | None:
        parsed = OrderGuardianService._to_float(value)
        if parsed is None or parsed <= 0:
            return None
        return parsed

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                return None
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return int(float(text))
            except ValueError:
                return None
        return None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(timezone.utc)
            except ValueError:
                return None
        return None

    @staticmethod
    def _iso_utc(value: datetime) -> str:
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')

    def _guardian_llm_model_overrides(self, db: Session) -> dict[str, str]:
        connector = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == 'ollama').first()
        if connector is None or not isinstance(connector.settings, dict):
            return {}

        settings = connector.settings
        raw_agent_models = settings.get('agent_models', {})
        if not isinstance(raw_agent_models, dict):
            return {}

        guardian_model = str(raw_agent_models.get('order-guardian') or '').strip()
        if not guardian_model:
            return {}

        overrides: dict[str, str] = {}
        for agent_name in self.LLM_AGENT_NAMES:
            explicit_agent_model = str(raw_agent_models.get(agent_name) or '').strip()
            if explicit_agent_model:
                continue
            overrides[agent_name] = guardian_model
        return overrides

    def _default_settings(self) -> dict[str, Any]:
        return {
            'timeframe': self.DEFAULT_TIMEFRAME,
            'risk_percent': self.DEFAULT_RISK_PERCENT,
            'max_positions_per_cycle': self.DEFAULT_MAX_POSITIONS_PER_CYCLE,
            'sl_tp_min_delta': self.DEFAULT_SL_TP_MIN_DELTA,
        }

    def _sanitize_settings(self, raw_settings: Any) -> dict[str, Any]:
        defaults = self._default_settings()
        settings = dict(raw_settings) if isinstance(raw_settings, dict) else {}

        timeframe = str(settings.get('timeframe') or defaults['timeframe']).strip().upper()
        if len(timeframe) < 2 or len(timeframe) > 5:
            timeframe = defaults['timeframe']

        risk_percent = self._to_float(settings.get('risk_percent'))
        if risk_percent is None:
            risk_percent = defaults['risk_percent']
        risk_percent = min(max(float(risk_percent), 0.1), 5.0)

        max_positions = self._to_int(settings.get('max_positions_per_cycle'))
        if max_positions is None:
            max_positions = defaults['max_positions_per_cycle']
        max_positions = min(max(int(max_positions), 1), 50)

        sl_tp_min_delta = self._to_float(settings.get('sl_tp_min_delta'))
        if sl_tp_min_delta is None:
            sl_tp_min_delta = defaults['sl_tp_min_delta']
        sl_tp_min_delta = min(max(float(sl_tp_min_delta), 0.0), 0.02)

        sanitized = {
            'timeframe': timeframe,
            'risk_percent': round(risk_percent, 4),
            'max_positions_per_cycle': max_positions,
            'sl_tp_min_delta': round(sl_tp_min_delta, 8),
        }
        if 'last_run_at' in settings:
            sanitized['last_run_at'] = settings.get('last_run_at')
        if isinstance(settings.get('last_summary'), dict):
            sanitized['last_summary'] = settings.get('last_summary')
        return sanitized

    def _get_or_create_connector(self, db: Session) -> ConnectorConfig:
        connector = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == self.CONNECTOR_NAME).first()
        if connector:
            return connector

        connector = ConnectorConfig(
            connector_name=self.CONNECTOR_NAME,
            enabled=False,
            settings=self._default_settings(),
        )
        db.add(connector)
        db.commit()
        db.refresh(connector)
        return connector

    def get_status(self, db: Session) -> dict[str, Any]:
        connector = self._get_or_create_connector(db)
        settings = self._sanitize_settings(connector.settings)
        return {
            'enabled': bool(connector.enabled),
            'timeframe': settings['timeframe'],
            'risk_percent': settings['risk_percent'],
            'max_positions_per_cycle': settings['max_positions_per_cycle'],
            'sl_tp_min_delta': settings['sl_tp_min_delta'],
            'last_run_at': self._parse_datetime(settings.get('last_run_at')),
            'last_summary': settings.get('last_summary', {}) if isinstance(settings.get('last_summary'), dict) else {},
            'updated_at': connector.updated_at,
        }

    def update_status(self, db: Session, payload: OrderGuardianStatusUpdate) -> dict[str, Any]:
        connector = self._get_or_create_connector(db)
        current_settings = self._sanitize_settings(connector.settings)
        updates = payload.model_dump(exclude_unset=True)

        if 'enabled' in updates and updates['enabled'] is not None:
            connector.enabled = bool(updates['enabled'])
        if updates.get('timeframe') is not None:
            current_settings['timeframe'] = str(updates['timeframe']).strip().upper()
        if updates.get('risk_percent') is not None:
            current_settings['risk_percent'] = float(updates['risk_percent'])
        if updates.get('max_positions_per_cycle') is not None:
            current_settings['max_positions_per_cycle'] = int(updates['max_positions_per_cycle'])
        if updates.get('sl_tp_min_delta') is not None:
            current_settings['sl_tp_min_delta'] = float(updates['sl_tp_min_delta'])

        connector.settings = self._sanitize_settings(current_settings)
        db.commit()
        db.refresh(connector)
        return self.get_status(db)

    @staticmethod
    def _resolve_position_side(value: Any) -> str | None:
        if isinstance(value, (int, float)):
            int_value = int(value)
            if int_value == 0:
                return 'BUY'
            if int_value == 1:
                return 'SELL'
            return None

        text = str(value or '').strip().upper()
        if not text:
            return None
        if 'BUY' in text:
            return 'BUY'
        if 'SELL' in text:
            return 'SELL'
        return None

    def _normalize_position(self, position: Any) -> dict[str, Any] | None:
        if not isinstance(position, dict):
            return None

        position_id = (
            str(position.get('id') or position.get('positionId') or position.get('ticket') or position.get('orderId') or '').strip()
        )
        symbol = str(position.get('symbol') or '').strip().upper()
        side = self._resolve_position_side(position.get('type'))
        volume = self._to_positive_float(position.get('volume') or position.get('currentVolume') or position.get('lot'))
        if not position_id or not symbol or not side:
            return None

        stop_loss = self._to_positive_float(position.get('stopLoss'))
        if stop_loss is None:
            stop_loss = self._to_positive_float(position.get('stopLossPrice'))
        if stop_loss is None:
            stop_loss = self._to_positive_float(position.get('sl'))

        take_profit = self._to_positive_float(position.get('takeProfit'))
        if take_profit is None:
            take_profit = self._to_positive_float(position.get('takeProfitPrice'))
        if take_profit is None:
            take_profit = self._to_positive_float(position.get('tp'))

        return {
            'position_id': position_id,
            'symbol': symbol,
            'side': side,
            'volume': volume,
            'current_stop_loss': stop_loss,
            'current_take_profit': take_profit,
            'raw': position,
        }

    @staticmethod
    def _decision_from_analysis(bundle: dict[str, Any]) -> dict[str, Any]:
        trader_decision = bundle.get('trader_decision', {}) if isinstance(bundle, dict) else {}
        decision = str(trader_decision.get('decision') or 'HOLD').strip().upper()
        if decision not in {'BUY', 'SELL', 'HOLD'}:
            decision = 'HOLD'
        return {
            'decision': decision,
            'stop_loss': OrderGuardianService._to_positive_float(trader_decision.get('stop_loss')),
            'take_profit': OrderGuardianService._to_positive_float(trader_decision.get('take_profit')),
            'confidence': OrderGuardianService._to_float(trader_decision.get('confidence')) or 0.0,
            'net_score': OrderGuardianService._to_float(trader_decision.get('net_score')) or 0.0,
        }

    @staticmethod
    def _needs_level_update(current: float | None, suggested: float | None, min_delta: float) -> bool:
        if suggested is None:
            return False
        if current is None:
            return True
        return abs(suggested - current) >= min_delta

    def _guardian_report_from_llm(
        self,
        db: Session,
        *,
        account_label: str | None,
        timeframe: str,
        dry_run: bool,
        summary: dict[str, Any],
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        llm_enabled = self.model_selector.is_enabled(db, 'order-guardian')
        llm_model = self.model_selector.resolve(db, 'order-guardian')
        prompt_meta: dict[str, Any] = {
            'prompt_id': None,
            'prompt_version': 0,
            'llm_enabled': llm_enabled,
            'llm_model': llm_model,
        }
        if not llm_enabled:
            return {'text': '', 'degraded': False, 'prompt_meta': prompt_meta}

        fallback_system = (
            "Tu es Order Guardian MT5. "
            "Produis un rapport de supervision des positions clair, bref et actionnable."
        )
        fallback_user = (
            "Compte: {account_label}\nTimeframe guardian: {timeframe}\nMode: {mode}\n"
            "Résumé cycle: {summary_json}\nActions: {actions_json}\n"
            "Donne une synthèse en français: points critiques, exécutions faites, et priorités au prochain scan."
        )
        compact_actions = [
            {
                'position_id': item.get('position_id'),
                'symbol': item.get('symbol'),
                'side': item.get('side'),
                'decision': item.get('decision'),
                'action': item.get('action'),
                'executed': item.get('executed'),
                'reason': item.get('reason'),
            }
            for item in actions[:25]
        ]
        summary_json = json.dumps(summary, ensure_ascii=True)
        actions_json = json.dumps(compact_actions, ensure_ascii=True)

        prompt_info = self.prompt_service.render(
            db=db,
            agent_name='order-guardian',
            fallback_system=fallback_system,
            fallback_user=fallback_user,
            variables={
                'account_label': account_label or 'default',
                'timeframe': timeframe,
                'mode': 'dry-run' if dry_run else 'live',
                'summary_json': summary_json,
                'actions_json': actions_json,
            },
        )
        llm_res = self.llm.chat(
            prompt_info['system_prompt'],
            prompt_info['user_prompt'],
            model=llm_model,
        )
        return {
            'text': llm_res.get('text', ''),
            'degraded': llm_res.get('degraded', False),
            'prompt_meta': {
                'prompt_id': prompt_info.get('prompt_id'),
                'prompt_version': prompt_info.get('version', 0),
                'llm_enabled': llm_enabled,
                'llm_model': llm_model,
            },
        }

    async def _analyze_position(
        self,
        db: Session,
        *,
        symbol: str,
        timeframe: str,
        risk_percent: float,
        llm_model_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        market = self.orchestrator.market_provider.get_market_snapshot(symbol, timeframe)
        news = self.orchestrator.market_provider.get_news_context(symbol)
        memory_context = self.orchestrator.memory_service.search(
            db=db,
            pair=symbol,
            timeframe=timeframe,
            query=f'{symbol} {timeframe} open position management',
            limit=5,
        )
        context = AgentContext(
            pair=symbol,
            timeframe=timeframe,
            mode='live',
            risk_percent=risk_percent,
            market_snapshot=market,
            news_context=news,
            memory_context=memory_context,
            llm_model_overrides=llm_model_overrides or {},
        )
        return self.orchestrator.analyze_context(context=context, db=db, record_steps=False, emit_step_logs=False)

    async def evaluate(
        self,
        db: Session,
        *,
        account_ref: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        generated_at = datetime.now(timezone.utc)
        connector = self._get_or_create_connector(db)
        settings = self._sanitize_settings(connector.settings)

        if not connector.enabled:
            return {
                'enabled': False,
                'dry_run': dry_run,
                'timeframe': settings['timeframe'],
                'account_ref': account_ref,
                'account_label': None,
                'account_id': None,
                'provider': None,
                'analyzed_positions': 0,
                'actions': [],
                'actions_executed': 0,
                'skipped_reason': 'Order guardian disabled',
                'llm_report': None,
                'llm_degraded': False,
                'llm_prompt_meta': {},
                'generated_at': generated_at,
            }

        selected_account = self.account_selector.resolve(db, account_ref)
        account_id = selected_account.account_id if selected_account else None
        region = selected_account.region if selected_account else None
        positions_result = await self.metaapi.get_positions(account_id=account_id, region=region)
        positions_payload = positions_result.get('positions', []) if isinstance(positions_result, dict) else []
        provider = positions_result.get('provider') if isinstance(positions_result, dict) else None

        if not isinstance(positions_payload, list):
            positions_payload = []

        normalized_positions = [
            normalized
            for position in positions_payload
            for normalized in [self._normalize_position(position)]
            if normalized is not None
        ]
        normalized_positions = normalized_positions[: settings['max_positions_per_cycle']]
        llm_model_overrides = self._guardian_llm_model_overrides(db)

        actions: list[dict[str, Any]] = []
        executed_count = 0

        for position in normalized_positions:
            try:
                analysis_bundle = await self._analyze_position(
                    db,
                    symbol=position['symbol'],
                    timeframe=settings['timeframe'],
                    risk_percent=float(settings['risk_percent']),
                    llm_model_overrides=llm_model_overrides,
                )
                analysis = self._decision_from_analysis(analysis_bundle)
                decision = analysis['decision']
                suggested_sl = analysis['stop_loss']
                suggested_tp = analysis['take_profit']
                current_sl = position['current_stop_loss']
                current_tp = position['current_take_profit']

                reason = 'No change required'
                action = 'HOLD'
                execution: dict[str, Any] = {}
                executed = False

                if decision in {'BUY', 'SELL'} and decision != position['side']:
                    action = 'EXIT'
                    reason = f'Signal {decision} opposite to current position side {position["side"]}'
                    if not dry_run:
                        execution = await self.metaapi.close_position(
                            position_id=position['position_id'],
                            volume=position['volume'],
                            side=position['side'],
                            symbol=position['symbol'],
                            account_id=account_id,
                            region=region,
                            allow_opposite_fallback=False,
                        )
                        executed = bool(execution.get('executed'))
                elif decision == position['side']:
                    should_update_sl = self._needs_level_update(current_sl, suggested_sl, float(settings['sl_tp_min_delta']))
                    should_update_tp = self._needs_level_update(current_tp, suggested_tp, float(settings['sl_tp_min_delta']))
                    if should_update_sl or should_update_tp:
                        action = 'UPDATE_SL_TP'
                        reason = f'Signal {decision} aligned; refreshing protection levels'
                        if not dry_run:
                            execution = await self.metaapi.modify_position(
                                position_id=position['position_id'],
                                stop_loss=suggested_sl if should_update_sl else current_sl,
                                take_profit=suggested_tp if should_update_tp else current_tp,
                                account_id=account_id,
                                region=region,
                            )
                            executed = bool(execution.get('executed'))

                if executed:
                    executed_count += 1

                actions.append(
                    {
                        'position_id': position['position_id'],
                        'symbol': position['symbol'],
                        'side': position['side'],
                        'decision': decision,
                        'action': action,
                        'reason': reason,
                        'current_stop_loss': current_sl,
                        'current_take_profit': current_tp,
                        'suggested_stop_loss': suggested_sl,
                        'suggested_take_profit': suggested_tp,
                        'executed': executed,
                        'execution': execution if isinstance(execution, dict) else {'result': execution},
                        'analysis': {
                            'confidence': analysis.get('confidence', 0.0),
                            'net_score': analysis.get('net_score', 0.0),
                        },
                    }
                )
            except Exception as exc:  # pragma: no cover - runtime external failures
                logger.exception('order guardian position evaluation failed position=%s', position.get('position_id'))
                actions.append(
                    {
                        'position_id': position['position_id'],
                        'symbol': position['symbol'],
                        'side': position['side'],
                        'decision': 'HOLD',
                        'action': 'HOLD',
                        'reason': f'Analysis error: {exc}',
                        'current_stop_loss': position['current_stop_loss'],
                        'current_take_profit': position['current_take_profit'],
                        'suggested_stop_loss': None,
                        'suggested_take_profit': None,
                        'executed': False,
                        'execution': {},
                        'analysis': {},
                    }
                )

        summary = {
            'positions_seen': len(positions_payload),
            'positions_analyzed': len(normalized_positions),
            'actions_total': len(actions),
            'actions_executed': executed_count,
            'dry_run': dry_run,
        }
        llm_report = self._guardian_report_from_llm(
            db,
            account_label=selected_account.label if selected_account else None,
            timeframe=settings['timeframe'],
            dry_run=dry_run,
            summary=summary,
            actions=actions,
        )
        summary['llm_report'] = llm_report.get('text', '')
        summary['llm_degraded'] = bool(llm_report.get('degraded', False))
        summary['llm_prompt_meta'] = llm_report.get('prompt_meta', {})
        settings['last_run_at'] = self._iso_utc(generated_at)
        settings['last_summary'] = summary
        connector.settings = self._sanitize_settings(settings)
        db.commit()

        return {
            'enabled': True,
            'dry_run': dry_run,
            'timeframe': settings['timeframe'],
            'account_ref': account_ref,
            'account_label': selected_account.label if selected_account else None,
            'account_id': selected_account.account_id if selected_account else None,
            'provider': provider if isinstance(provider, str) else None,
            'analyzed_positions': len(normalized_positions),
            'actions': actions,
            'actions_executed': executed_count,
            'skipped_reason': positions_result.get('reason') if isinstance(positions_result, dict) else None,
            'llm_report': llm_report.get('text') or None,
            'llm_degraded': bool(llm_report.get('degraded', False)),
            'llm_prompt_meta': llm_report.get('prompt_meta', {}),
            'generated_at': generated_at,
        }
