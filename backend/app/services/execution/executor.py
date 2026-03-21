import logging
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.execution_order import ExecutionOrder
from app.services.trading.account_selector import MetaApiAccountSelector
from app.services.trading.metaapi_client import MetaApiClient

logger = logging.getLogger(__name__)


class ExecutionService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.metaapi = MetaApiClient()
        self.account_selector = MetaApiAccountSelector()

    @staticmethod
    def _json_safe(payload: Any) -> dict[str, Any]:
        encoded = jsonable_encoder(payload)
        if isinstance(encoded, dict):
            return encoded
        return {'value': encoded}

    @classmethod
    def _normalized_result(
        cls,
        payload: Any,
        *,
        status: str,
        executed: bool,
        reason: str | None = None,
    ) -> dict[str, Any]:
        result = cls._json_safe(payload)
        result['status'] = status
        result['executed'] = bool(executed)
        if reason:
            existing_reason = str(result.get('reason', '') or '').strip()
            if not existing_reason:
                result['reason'] = reason
        return result

    @staticmethod
    def _normalize_float(value: float | None, *, precision: int = 8) -> float | None:
        if value is None:
            return None
        try:
            return round(float(value), precision)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _build_idempotency_key(
        cls,
        *,
        run_id: int,
        mode: str,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float | None,
        take_profit: float | None,
        metaapi_account_ref: int | None,
    ) -> str:
        return (
            f'run={int(run_id)}|mode={str(mode).strip().lower()}|symbol={str(symbol).strip().upper()}|'
            f'side={str(side).strip().upper()}|vol={cls._normalize_float(volume, precision=4)}|'
            f'sl={cls._normalize_float(stop_loss)}|tp={cls._normalize_float(take_profit)}|acct={metaapi_account_ref}'
        )

    @staticmethod
    def _classify_execution_error(message: str | None) -> str:
        text = str(message or '').strip().lower()
        if not text:
            return 'provider_error'
        if any(keyword in text for keyword in ('timeout', 'timed out', 'temporarily unavailable', 'connection', 'network')):
            return 'transient_network'
        if any(keyword in text for keyword in ('rate limit', 'too many requests', '429')):
            return 'rate_limited'
        if any(keyword in text for keyword in ('unauthorized', 'forbidden', 'invalid token', 'auth')):
            return 'auth_or_permission'
        if any(keyword in text for keyword in ('insufficient funds', 'not enough money', 'margin', 'balance')):
            return 'account_funds'
        if any(keyword in text for keyword in ('invalid symbol', 'symbol', 'instrument')):
            return 'symbol_error'
        return 'provider_error'

    @classmethod
    def _is_retryable_error_class(cls, error_class: str) -> bool:
        return error_class in {'transient_network', 'rate_limited'}

    def _find_replayable_order(
        self,
        db: Session,
        *,
        run_id: int,
        mode: str,
        symbol: str,
        side: str,
        idempotency_key: str,
    ) -> ExecutionOrder | None:
        candidates = (
            db.query(ExecutionOrder)
            .filter(
                ExecutionOrder.run_id == run_id,
                ExecutionOrder.mode == mode,
                ExecutionOrder.symbol == symbol,
                ExecutionOrder.side == side,
            )
            .order_by(ExecutionOrder.id.desc())
            .limit(25)
            .all()
        )
        for order in candidates:
            request_payload = order.request_payload if isinstance(order.request_payload, dict) else {}
            if str(request_payload.get('idempotency_key') or '') != idempotency_key:
                continue
            if order.status not in {'submitted', 'simulated', 'paper-simulated', 'blocked'}:
                continue
            if not isinstance(order.response_payload, dict) or not order.response_payload:
                continue
            return order
        return None

    @classmethod
    def _replay_response_from_order(cls, order: ExecutionOrder, idempotency_key: str) -> dict[str, Any]:
        replay_payload = cls._json_safe(order.response_payload)
        replay_payload['idempotent_replay'] = True
        replay_payload['idempotency_key'] = idempotency_key
        replay_payload.setdefault('status', str(order.status or 'unknown'))
        replay_payload.setdefault('executed', bool(replay_payload.get('executed', False)))
        replay_reason = f'Idempotent replay of existing execution order #{order.id}.'
        existing_reason = str(replay_payload.get('reason', '') or '').strip()
        if existing_reason:
            replay_payload['replay_reason'] = replay_reason
        else:
            replay_payload['reason'] = replay_reason
        return replay_payload

    async def execute(
        self,
        db: Session,
        run_id: int,
        mode: str,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float | None,
        take_profit: float | None,
        metaapi_account_ref: int | None = None,
    ) -> dict[str, Any]:
        if side == 'HOLD':
            reason = 'No order executed for HOLD decision.'
            return self._normalized_result({'reason': reason}, status='skipped', executed=False, reason=reason)

        normalized_mode = str(mode or '').strip().lower()
        normalized_symbol = str(symbol or '').strip().upper()
        normalized_side = str(side or '').strip().upper()
        idempotency_key = self._build_idempotency_key(
            run_id=run_id,
            mode=normalized_mode,
            symbol=normalized_symbol,
            side=normalized_side,
            volume=volume,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metaapi_account_ref=metaapi_account_ref,
        )
        replayable = self._find_replayable_order(
            db,
            run_id=run_id,
            mode=normalized_mode,
            symbol=normalized_symbol,
            side=normalized_side,
            idempotency_key=idempotency_key,
        )
        if replayable is not None:
            logger.info(
                'execution idempotent replay run_id=%s mode=%s side=%s symbol=%s order_id=%s',
                run_id,
                normalized_mode,
                normalized_side,
                normalized_symbol,
                replayable.id,
            )
            return self._replay_response_from_order(replayable, idempotency_key)

        request_payload = {
            'symbol': normalized_symbol,
            'side': normalized_side,
            'volume': self._normalize_float(volume, precision=4),
            'stop_loss': self._normalize_float(stop_loss),
            'take_profit': self._normalize_float(take_profit),
            'metaapi_account_ref': metaapi_account_ref,
            'idempotency_key': idempotency_key,
        }

        order = ExecutionOrder(
            run_id=run_id,
            mode=normalized_mode,
            side=normalized_side,
            symbol=normalized_symbol,
            volume=volume,
            request_payload=request_payload,
            response_payload={},
            status='created',
        )
        db.add(order)
        db.flush()

        if normalized_mode == 'simulation':
            response = self._normalized_result(
                {'simulated': True, 'fill_price': None, 'message': 'Simulation order accepted'},
                status='simulated',
                executed=False,
                reason='Simulation mode: order not sent to broker.',
            )
            response['idempotency_key'] = idempotency_key
            order.status = 'simulated'
            order.response_payload = response
            db.commit()
            return response

        if normalized_mode == 'paper' and not self.settings.enable_paper_execution:
            order.status = 'blocked'
            order.error = 'Paper trading disabled by configuration.'
            response = self._normalized_result({'error': order.error}, status='blocked', executed=False, reason=order.error)
            response['idempotency_key'] = idempotency_key
            order.response_payload = response
            db.commit()
            return response

        if normalized_mode == 'live' and not self.settings.allow_live_trading:
            order.status = 'blocked'
            order.error = 'Live trading is disabled by default.'
            response = self._normalized_result({'error': order.error}, status='blocked', executed=False, reason=order.error)
            response['idempotency_key'] = idempotency_key
            order.response_payload = response
            db.commit()
            return response

        if normalized_mode in {'paper', 'live'}:
            selected_account = self.account_selector.resolve(db, metaapi_account_ref)
            metaapi_response = await self.metaapi.place_order(
                symbol=normalized_symbol,
                side=normalized_side,
                volume=volume,
                stop_loss=stop_loss,
                take_profit=take_profit,
                account_id=selected_account.account_id if selected_account else None,
                region=selected_account.region if selected_account else None,
            )
            safe_metaapi_response = self._json_safe(metaapi_response)
            if metaapi_response.get('executed'):
                safe_metaapi_response['account_label'] = selected_account.label if selected_account else 'default'
                response = self._normalized_result(
                    safe_metaapi_response,
                    status='submitted',
                    executed=True,
                    reason='Order submitted to broker.',
                )
                response['idempotency_key'] = idempotency_key
                order.status = 'submitted'
                order.response_payload = response
                db.commit()
                return response

            # Degraded fallback: emulate paper execution without external broker.
            if normalized_mode == 'paper':
                fallback_reason = str(metaapi_response.get('reason') or 'MetaApi unavailable')
                fallback = self._json_safe(
                    {'simulated': True, 'paper_fallback': True, 'reason': fallback_reason}
                )
                response = self._normalized_result(
                    fallback,
                    status='paper-simulated',
                    executed=False,
                    reason=fallback_reason,
                )
                response['idempotency_key'] = idempotency_key
                order.status = 'paper-simulated'
                order.response_payload = response
                db.commit()
                return response

            order.status = 'failed'
            order.error = metaapi_response.get('reason', 'MetaApi execution failed')
            error_class = self._classify_execution_error(order.error)
            response = self._normalized_result(
                {
                    'error': order.error,
                    'details': safe_metaapi_response,
                    'error_class': error_class,
                    'retryable': self._is_retryable_error_class(error_class),
                    'idempotency_key': idempotency_key,
                },
                status='failed',
                executed=False,
                reason=order.error,
            )
            order.response_payload = response
            db.commit()
            return response

        order.status = 'failed'
        order.error = f'Unsupported execution mode: {normalized_mode}'
        error_class = self._classify_execution_error(order.error)
        response = self._normalized_result(
            {
                'error': order.error,
                'error_class': error_class,
                'retryable': self._is_retryable_error_class(error_class),
                'idempotency_key': idempotency_key,
            },
            status='failed',
            executed=False,
            reason=order.error,
        )
        order.response_payload = response
        db.commit()
        return response
