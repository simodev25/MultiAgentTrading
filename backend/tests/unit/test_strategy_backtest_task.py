from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, date
from pathlib import Path
from types import ModuleType, SimpleNamespace

from app.db.models.strategy import Strategy


def _task_decorator(*args, **kwargs):  # noqa: ANN001, ARG001
    def _decorator(func):  # noqa: ANN001
        return func

    return _decorator


def _load_strategy_backtest_task(monkeypatch):
    fake_celery_app_module = ModuleType('app.tasks.celery_app')
    fake_celery_app_module.celery_app = SimpleNamespace(task=_task_decorator)
    monkeypatch.setitem(sys.modules, 'app.tasks.celery_app', fake_celery_app_module)
    sys.modules.pop('app.tasks.strategy_backtest_task', None)
    return importlib.import_module('app.tasks.strategy_backtest_task')


class _FakeDB:
    def __init__(self, strategy: object) -> None:
        self._strategy = strategy
        self.committed = False
        self.closed = False

    def get(self, model, strategy_id):  # noqa: ANN001, ARG002
        if model is Strategy and strategy_id == self._strategy.id:
            return self._strategy
        return None

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def test_strategy_backtest_task_passes_persisted_params(monkeypatch, tmp_path) -> None:
    task_module = _load_strategy_backtest_task(monkeypatch)
    monkeypatch.setattr(task_module, 'TRACE_DIR', str(tmp_path))
    execute = task_module.execute

    strategy = SimpleNamespace(
        id=1,
        strategy_id='STRAT-001',
        name='ema crossover',
        description='validated with persisted params',
        status='BACKTESTING',
        score=0.0,
        template='ema_crossover',
        symbol='EURUSD.PRO',
        timeframe='H1',
        params={'ema_fast': 7, 'ema_slow': 14, 'rsi_filter': 25},
        metrics={},
        prompt_history=[],
        is_monitoring=False,
        monitoring_mode='simulation',
        monitoring_risk_percent=1.0,
        last_signal_key=None,
        last_backtest_id=None,
        created_by_id=42,
    )
    db = _FakeDB(strategy)
    captured: dict[str, object] = {}

    class FakeEngine:
        def run(self, *args, **kwargs):  # noqa: ANN001
            captured['args'] = args
            captured['kwargs'] = kwargs
            return SimpleNamespace(
                metrics={
                    'win_rate_pct': 80,
                    'profit_factor': 2.0,
                    'max_drawdown_pct': 5.0,
                    'total_return_pct': 25.0,
                    'total_trades': 50,
                }
            )

    monkeypatch.setattr('app.tasks.strategy_backtest_task.SessionLocal', lambda: db)
    monkeypatch.setattr('app.tasks.strategy_backtest_task.BacktestEngine', FakeEngine)

    execute(strategy_db_id=1)

    assert captured['args'][0] == 'EURUSD.PRO'
    assert captured['args'][1] == 'H1'
    assert len(captured['args']) >= 4
    start_date = date.fromisoformat(captured['args'][2])
    end_date = date.fromisoformat(captured['args'][3])
    assert (end_date - start_date).days == 30
    assert captured['kwargs']['strategy'] == 'ema_crossover'
    assert captured['kwargs']['strategy_params'] == {'ema_fast': 7, 'ema_slow': 14, 'rsi_filter': 25}
    assert strategy.status == 'VALIDATED'
    assert db.committed is True
    assert db.closed is True
    assert strategy.metrics['validated_template'] == 'ema_crossover'
    assert strategy.metrics['validated_params'] == {'ema_fast': 7, 'ema_slow': 14, 'rsi_filter': 25}
    validation_trace = strategy.metrics.get('validation_trace', {})
    assert validation_trace.get('path')
    assert Path(validation_trace['path']).exists()
    assert 'backend/debug-strategy' in validation_trace.get('tags', [])


def test_strategy_backtest_task_sets_zero_score_when_no_trades(monkeypatch, tmp_path) -> None:
    task_module = _load_strategy_backtest_task(monkeypatch)
    monkeypatch.setattr(task_module, 'TRACE_DIR', str(tmp_path))
    execute = task_module.execute

    strategy = SimpleNamespace(
        id=2,
        strategy_id='STRAT-002',
        name='no trades strategy',
        description='should be rejected when inactive',
        status='BACKTESTING',
        score=0.0,
        template='ema_crossover',
        symbol='BTCUSD',
        timeframe='H1',
        params={'ema_fast': 18, 'ema_slow': 50, 'rsi_filter': 50},
        metrics={},
        prompt_history=[],
        is_monitoring=False,
        monitoring_mode='simulation',
        monitoring_risk_percent=1.0,
        last_signal_key=None,
        last_backtest_id=None,
        created_by_id=42,
    )
    db = _FakeDB(strategy)

    class FakeEngine:
        def run(self, *args, **kwargs):  # noqa: ANN001
            return SimpleNamespace(
                metrics={
                    'win_rate_pct': 0.0,
                    'profit_factor': 0.0,
                    'max_drawdown_pct': 0.0,
                    'total_return_pct': 0.0,
                    'total_trades': 0,
                }
            )

    monkeypatch.setattr('app.tasks.strategy_backtest_task.SessionLocal', lambda: db)
    monkeypatch.setattr('app.tasks.strategy_backtest_task.BacktestEngine', FakeEngine)

    execute(strategy_db_id=2)

    assert strategy.score == 0.0
    assert strategy.status == 'REJECTED'
    assert strategy.metrics['trades'] == 0
    assert 'insufficient_sample_no_trades' in strategy.metrics.get('validation_flags', [])
    assert strategy.metrics.get('validation_trace', {}).get('path')


def test_strategy_backtest_task_uses_crypto_lookback_window(monkeypatch, tmp_path) -> None:
    task_module = _load_strategy_backtest_task(monkeypatch)
    monkeypatch.setattr(task_module, 'TRACE_DIR', str(tmp_path))
    execute = task_module.execute

    strategy = SimpleNamespace(
        id=20,
        strategy_id='STRAT-020',
        name='crypto validation window',
        description='should validate crypto over aligned lookback window',
        status='BACKTESTING',
        score=0.0,
        template='ema_crossover',
        symbol='BTCUSD',
        timeframe='H1',
        params={'ema_fast': 7, 'ema_slow': 18, 'rsi_filter': 25},
        metrics={},
        prompt_history=[],
        is_monitoring=False,
        monitoring_mode='simulation',
        monitoring_risk_percent=1.0,
        last_signal_key=None,
        last_backtest_id=None,
        created_by_id=42,
    )
    db = _FakeDB(strategy)
    captured: dict[str, object] = {}

    class FakeEngine:
        def run(self, *args, **kwargs):  # noqa: ANN001
            captured['args'] = args
            captured['kwargs'] = kwargs
            return SimpleNamespace(
                metrics={
                    'win_rate_pct': 35.0,
                    'profit_factor': 1.1,
                    'max_drawdown_pct': 12.0,
                    'total_return_pct': 2.0,
                    'total_trades': 40,
                }
            )

    monkeypatch.setattr('app.tasks.strategy_backtest_task.SessionLocal', lambda: db)
    monkeypatch.setattr('app.tasks.strategy_backtest_task.BacktestEngine', FakeEngine)

    execute(strategy_db_id=20)

    start_date = date.fromisoformat(captured['args'][2])
    end_date = date.fromisoformat(captured['args'][3])
    assert (end_date - start_date).days == 90


def test_strategy_backtest_task_penalizes_small_sample_sizes(monkeypatch, tmp_path) -> None:
    task_module = _load_strategy_backtest_task(monkeypatch)
    monkeypatch.setattr(task_module, 'TRACE_DIR', str(tmp_path))
    execute = task_module.execute

    strategy = SimpleNamespace(
        id=3,
        strategy_id='STRAT-003',
        name='small sample strategy',
        description='should be down-weighted for low trade count',
        status='BACKTESTING',
        score=0.0,
        template='ema_crossover',
        symbol='EURUSD.PRO',
        timeframe='H1',
        params={'ema_fast': 9, 'ema_slow': 21, 'rsi_filter': 30},
        metrics={},
        prompt_history=[],
        is_monitoring=False,
        monitoring_mode='simulation',
        monitoring_risk_percent=1.0,
        last_signal_key=None,
        last_backtest_id=None,
        created_by_id=42,
    )
    db = _FakeDB(strategy)

    class FakeEngine:
        def run(self, *args, **kwargs):  # noqa: ANN001
            return SimpleNamespace(
                metrics={
                    'win_rate_pct': 80.0,
                    'profit_factor': 2.0,
                    'max_drawdown_pct': 5.0,
                    'total_return_pct': 25.0,
                    'total_trades': 5,
                }
            )

    monkeypatch.setattr('app.tasks.strategy_backtest_task.SessionLocal', lambda: db)
    monkeypatch.setattr('app.tasks.strategy_backtest_task.BacktestEngine', FakeEngine)

    execute(strategy_db_id=3)

    # Same quality metrics as the validated case above, but with too few trades.
    assert strategy.score < 50.0
    assert strategy.status == 'REJECTED'
    assert strategy.metrics.get('sample_size_factor', 1.0) < 1.0
    assert 'insufficient_sample_low_trades' in strategy.metrics.get('validation_flags', [])


def test_strategy_backtest_task_validates_profitable_volatile_profile_with_quality_gates(monkeypatch, tmp_path) -> None:
    task_module = _load_strategy_backtest_task(monkeypatch)
    monkeypatch.setattr(task_module, 'TRACE_DIR', str(tmp_path))
    execute = task_module.execute

    strategy = SimpleNamespace(
        id=30,
        strategy_id='STRAT-030',
        name='profitable volatile profile',
        description='should pass revised validation gate',
        status='BACKTESTING',
        score=0.0,
        template='macd_rsi_combo',
        symbol='ETHUSD',
        timeframe='H4',
        params={'macd_fast': 10, 'macd_slow': 24, 'macd_signal': 9, 'rsi_period': 14, 'rsi_oversold': 35, 'rsi_overbought': 65},
        metrics={},
        prompt_history=[],
        is_monitoring=False,
        monitoring_mode='simulation',
        monitoring_risk_percent=1.0,
        last_signal_key=None,
        last_backtest_id=None,
        created_by_id=42,
    )
    db = _FakeDB(strategy)

    class FakeEngine:
        def run(self, *args, **kwargs):  # noqa: ANN001
            return SimpleNamespace(
                metrics={
                    'win_rate_pct': 42.9,
                    'profit_factor': 1.4,
                    'max_drawdown_pct': -27.52,
                    'total_return_pct': 28.49,
                    'total_trades': 49,
                }
            )

    monkeypatch.setattr('app.tasks.strategy_backtest_task.SessionLocal', lambda: db)
    monkeypatch.setattr('app.tasks.strategy_backtest_task.BacktestEngine', FakeEngine)

    execute(strategy_db_id=30)

    assert strategy.score >= 40.0
    assert strategy.metrics['validation_gate_passed'] is True
    assert strategy.status == 'VALIDATED'


def test_validation_trace_updates_linked_generation_trace(monkeypatch, tmp_path) -> None:
    task_module = _load_strategy_backtest_task(monkeypatch)
    monkeypatch.setattr(task_module, 'TRACE_DIR', str(tmp_path))
    execute = task_module.execute

    linked_generation = tmp_path / 'strategy-EURUSDPRO-H1-20260404T120000Z.json'
    linked_generation.write_text(
        json.dumps(
            {
                'schema_version': 2,
                'type': 'strategy_generation',
                'generated_at': '2026-04-04T12:00:00+00:00',
                'result': {'template': 'ema_crossover'},
            }
        ),
        encoding='utf-8',
    )

    strategy = SimpleNamespace(
        id=4,
        strategy_id='STRAT-004',
        name='trace linkage strategy',
        description='should update linked generation trace',
        status='BACKTESTING',
        score=0.0,
        template='ema_crossover',
        symbol='EURUSD.PRO',
        timeframe='H1',
        params={'ema_fast': 9, 'ema_slow': 21, 'rsi_filter': 30},
        metrics={},
        prompt_history=[],
        is_monitoring=False,
        monitoring_mode='simulation',
        monitoring_risk_percent=1.0,
        last_signal_key=None,
        last_backtest_id=None,
        created_by_id=42,
        created_at=datetime(2026, 4, 4, 12, 0, 2),
    )
    db = _FakeDB(strategy)

    class FakeEngine:
        def run(self, *args, **kwargs):  # noqa: ANN001
            return SimpleNamespace(
                metrics={
                    'win_rate_pct': 70.0,
                    'profit_factor': 1.8,
                    'max_drawdown_pct': 6.0,
                    'total_return_pct': 10.0,
                    'total_trades': 40,
                }
            )

    monkeypatch.setattr('app.tasks.strategy_backtest_task.SessionLocal', lambda: db)
    monkeypatch.setattr('app.tasks.strategy_backtest_task.BacktestEngine', FakeEngine)

    execute(strategy_db_id=4)

    linked_updated = json.loads(linked_generation.read_text(encoding='utf-8'))
    validation_trace_path = Path(strategy.metrics['validation_trace']['path'])
    validation_trace = json.loads(validation_trace_path.read_text(encoding='utf-8'))
    assert linked_updated.get('validation') is not None
    assert 'backend/debug-strategy' in linked_updated.get('tags', [])
    assert strategy.metrics.get('validation_trace', {}).get('linked_generation_trace') == str(linked_generation)
    assert validation_trace.get('strategy_id') == 'STRAT-004'
    assert validation_trace.get('linked_generation_trace') == str(linked_generation)
