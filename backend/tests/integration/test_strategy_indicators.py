import asyncio
import os
import sys
import types

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ['DATABASE_URL'] = 'sqlite:///./test.db'


class _FakeCeleryApp:
    def task(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(func):
            return func

        return _decorator


sys.modules.setdefault(
    'app.tasks.celery_app',
    types.SimpleNamespace(celery_app=_FakeCeleryApp()),
)

from app.api.routes.strategies import get_strategy_indicators
from app.db.base import Base
from app.db.models.strategy import Strategy
from app.db.models.user import User
from app.services.backtest.engine import BacktestEngine
from app.services.strategy.signal_engine import compute_strategy_overlays_and_signals
from app.tasks.strategy_monitor_task import _compute_latest_signal


def _candles(close_values: list[float]) -> list[dict]:
    start = pd.Timestamp('2025-01-01T00:00:00Z')
    return [
        {
            'time': (start + pd.Timedelta(hours=idx)).isoformat().replace('+00:00', 'Z'),
            'open': value,
            'high': value + 0.001,
            'low': value - 0.001,
            'close': value,
            'volume': 1000,
        }
        for idx, value in enumerate(close_values)
    ]


def _frame_from_candles(candles: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            'Open': [candle['open'] for candle in candles],
            'High': [candle['high'] for candle in candles],
            'Low': [candle['low'] for candle in candles],
            'Close': [candle['close'] for candle in candles],
            'Volume': [candle['volume'] for candle in candles],
        },
        index=pd.DatetimeIndex([candle['time'] for candle in candles]),
    )


def _backtest_entries(series: pd.Series, candles: list[dict]) -> list[dict]:
    entries: list[dict] = []
    previous = 0

    for signal, candle in zip(series.tolist(), candles):
        current = int(signal)
        if current != 0 and current != previous:
            entries.append(
                {
                    'time': candle['time'],
                    'price': float(candle['close']),
                    'side': 'BUY' if current == 1 else 'SELL',
                }
            )
        previous = current

    return entries


async def _fake_get_market_candles(self, pair, timeframe, limit):  # noqa: ANN001, ARG001
    return {'candles': _candles([1.2 - i * 0.002 for i in range(40)] + [1.12 + i * 0.002 for i in range(40)])}


def test_strategy_indicators_endpoint_matches_monitor_and_backtest(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr('app.services.trading.metaapi_client.MetaApiClient.get_market_candles', _fake_get_market_candles)

    engine = create_engine(f"sqlite:///{tmp_path / 'strategy-indicators.db'}", connect_args={'check_same_thread': False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        user = User(
            email='parity@local.dev',
            hashed_password='not-used-in-this-test',
            role='super-admin',
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        strategy = Strategy(
            strategy_id='STRAT-PARITY-001',
            name='parity_strategy',
            description='Parity test strategy',
            status='DRAFT',
            score=0.0,
            template='ema_crossover',
            symbol='EURUSD.PRO',
            timeframe='H1',
            params={'ema_fast': 5, 'ema_slow': 20, 'rsi_filter': 30},
            metrics={},
            prompt_history=[],
            created_by_id=user.id,
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        payload = asyncio.run(
            get_strategy_indicators(
                strategy_id=strategy.id,
                db=db,
                user=user,
            )
        )

    candles = _candles([1.2 - i * 0.002 for i in range(40)] + [1.12 + i * 0.002 for i in range(40)])
    params = {'ema_fast': 5, 'ema_slow': 20, 'rsi_filter': 30}
    shared = compute_strategy_overlays_and_signals(candles, 'ema_crossover', params)
    latest_signal = _compute_latest_signal(candles, 'ema_crossover', params)
    backtest_series = BacktestEngine()._signal_series_for_strategy(_frame_from_candles(candles), 'ema_crossover', params)

    assert payload['template'] == 'ema_crossover'
    assert payload['signals'] == shared['signals']
    assert payload['overlays'] == shared['overlays']
    assert latest_signal == shared['signals'][-1]
    assert _backtest_entries(backtest_series, candles) == shared['signals']
