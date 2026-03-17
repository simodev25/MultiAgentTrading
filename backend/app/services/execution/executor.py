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

        request_payload = {
            'symbol': symbol,
            'side': side,
            'volume': volume,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'metaapi_account_ref': metaapi_account_ref,
        }

        order = ExecutionOrder(
            run_id=run_id,
            mode=mode,
            side=side,
            symbol=symbol,
            volume=volume,
            request_payload=request_payload,
            response_payload={},
            status='created',
        )
        db.add(order)
        db.flush()

        if mode == 'simulation':
            response = self._normalized_result(
                {'simulated': True, 'fill_price': None, 'message': 'Simulation order accepted'},
                status='simulated',
                executed=False,
                reason='Simulation mode: order not sent to broker.',
            )
            order.status = 'simulated'
            order.response_payload = response
            db.commit()
            return response

        if mode == 'paper' and not self.settings.enable_paper_execution:
            order.status = 'blocked'
            order.error = 'Paper trading disabled by configuration.'
            response = self._normalized_result({'error': order.error}, status='blocked', executed=False, reason=order.error)
            order.response_payload = response
            db.commit()
            return response

        if mode == 'live' and not self.settings.allow_live_trading:
            order.status = 'blocked'
            order.error = 'Live trading is disabled by default.'
            response = self._normalized_result({'error': order.error}, status='blocked', executed=False, reason=order.error)
            order.response_payload = response
            db.commit()
            return response

        if mode in {'paper', 'live'}:
            selected_account = self.account_selector.resolve(db, metaapi_account_ref)
            metaapi_response = await self.metaapi.place_order(
                symbol=symbol,
                side=side,
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
                order.status = 'submitted'
                order.response_payload = response
                db.commit()
                return response

            # Degraded fallback: emulate paper execution without external broker.
            if mode == 'paper':
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
                order.status = 'paper-simulated'
                order.response_payload = response
                db.commit()
                return response

            order.status = 'failed'
            order.error = metaapi_response.get('reason', 'MetaApi execution failed')
            response = self._normalized_result(
                {'error': order.error, 'details': safe_metaapi_response},
                status='failed',
                executed=False,
                reason=order.error,
            )
            order.response_payload = response
            db.commit()
            return response

        order.status = 'failed'
        order.error = f'Unsupported execution mode: {mode}'
        response = self._normalized_result({'error': order.error}, status='failed', executed=False, reason=order.error)
        order.response_payload = response
        db.commit()
        return response
