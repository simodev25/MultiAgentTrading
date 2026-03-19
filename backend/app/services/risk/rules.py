import re
from dataclasses import dataclass


@dataclass
class RiskAssessment:
    accepted: bool
    reasons: list[str]
    suggested_volume: float


class RiskEngine:
    @staticmethod
    def _is_fx_like_symbol(symbol: str | None) -> bool:
        normalized = str(symbol or '').upper().split('.', 1)[0]
        return re.fullmatch(r'[A-Z]{6}', normalized) is not None

    @classmethod
    def _pip_size(cls, pair: str | None, price: float | None = None) -> float:
        normalized = str(pair or '').upper().split('.', 1)[0]
        if cls._is_fx_like_symbol(normalized):
            if normalized.endswith('JPY'):
                return 0.01
            return 0.0001

        price_value = abs(float(price or 0.0))
        if price_value >= 1000:
            return 1.0
        if price_value >= 100:
            return 0.1
        if price_value >= 1:
            return 0.01
        if price_value >= 0.1:
            return 0.001
        return 0.0001

    def evaluate(
        self,
        mode: str,
        decision: str,
        risk_percent: float,
        price: float,
        stop_loss: float | None,
        pair: str | None = None,
        equity: float = 10000.0,
    ) -> RiskAssessment:
        reasons: list[str] = []

        if decision == 'HOLD':
            return RiskAssessment(accepted=True, reasons=['No trade requested (HOLD).'], suggested_volume=0.0)

        if stop_loss is None:
            return RiskAssessment(accepted=False, reasons=['Stop loss is mandatory.'], suggested_volume=0.0)

        max_risk = {'simulation': 5.0, 'paper': 3.0, 'live': 2.0}.get(mode, 2.0)
        if risk_percent > max_risk:
            reasons.append(f'Risk percent {risk_percent}% exceeds mode limit {max_risk}% for {mode}.')

        stop_distance = abs(price - stop_loss)
        if stop_distance <= 0:
            reasons.append('Stop loss distance must be > 0.')

        if stop_distance / price < 0.0005:
            reasons.append('Stop loss is too tight for current market volatility.')

        risk_amount = equity * (risk_percent / 100)
        pip_value_per_lot = 10.0
        pip_size = self._pip_size(pair, price)
        sl_pips = max(stop_distance / pip_size, 0.1)
        suggested_volume = max(min(risk_amount / (sl_pips * pip_value_per_lot), 2.0), 0.01)

        accepted = len(reasons) == 0
        if accepted:
            reasons.append('Risk checks passed.')

        return RiskAssessment(accepted=accepted, reasons=reasons, suggested_volume=round(suggested_volume, 2))
