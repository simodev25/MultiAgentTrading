from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.routes.strategies import StrategyEditRequest, edit_strategy
from app.db.models.strategy import Strategy


class _FakeDB:
    def __init__(self, strategy: object) -> None:
        self._strategy = strategy
        self.committed = False

    def get(self, model, strategy_id):  # noqa: ANN001, ARG002
        if model is Strategy and strategy_id == self._strategy.id:
            return self._strategy
        return None

    def commit(self) -> None:
        self.committed = True

    def refresh(self, obj) -> None:  # noqa: ANN001, ARG002
        return None


def _legacy_strategy() -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=1,
        strategy_id='STRAT-001',
        name='legacy-ema',
        description='legacy strategy',
        status='DRAFT',
        score=0.0,
        template='ema_rsi',
        symbol='EURUSD.PRO',
        timeframe='H1',
        params={'ema_fast': 12, 'ema_slow': 26, 'legacy_mode': True},
        metrics={},
        is_monitoring=False,
        monitoring_mode='simulation',
        monitoring_risk_percent=1.0,
        last_signal_key=None,
        prompt_history=[],
        last_backtest_id=None,
        created_by_id=42,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_edit_strategy_preserves_legacy_params_without_raising(monkeypatch) -> None:
    strategy = _legacy_strategy()
    db = _FakeDB(strategy)

    async def fake_llm_edit(history, edit_prompt, current_params, template):  # noqa: ANN001
        return {
            'template': 'ema_rsi',
            'params': {'ema_fast': 8, 'ema_slow': 21, 'legacy_mode': True},
            'name': 'legacy-ema-edited',
            'description': 'edited legacy strategy',
        }

    monkeypatch.setattr('app.api.routes.strategies._llm_edit', fake_llm_edit)

    result = await edit_strategy(
        strategy_id=1,
        payload=StrategyEditRequest(prompt='keep the legacy strategy compatible'),
        db=db,
        user=None,
    )

    assert result.template == 'ema_rsi'
    assert result.params == {'ema_fast': 8, 'ema_slow': 21, 'legacy_mode': True}
    assert db.committed is True


@pytest.mark.asyncio
async def test_edit_strategy_sanitizes_when_legacy_moves_to_executable_template(monkeypatch) -> None:
    strategy = _legacy_strategy()
    db = _FakeDB(strategy)

    async def fake_llm_edit(history, edit_prompt, current_params, template):  # noqa: ANN001
        return {
            'template': 'bollinger_breakout',
            'params': {'bb_period': 20, 'bb_std': 2.0, 'volume_filter': True},
            'name': 'bollinger-edited',
            'description': 'moved to executable',
        }

    monkeypatch.setattr('app.api.routes.strategies._llm_edit', fake_llm_edit)

    result = await edit_strategy(
        strategy_id=1,
        payload=StrategyEditRequest(prompt='convert this legacy strategy'),
        db=db,
        user=None,
    )

    assert result.template == 'bollinger_breakout'
    assert result.params == {'bb_period': 20, 'bb_std': 2.0}
    assert 'volume_filter' not in result.params
