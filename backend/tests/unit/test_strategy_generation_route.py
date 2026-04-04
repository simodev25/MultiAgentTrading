from __future__ import annotations

import json
import asyncio
import sys
from types import ModuleType
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.routes.strategies import generate_strategy
from app.core.security import Role
from app.schemas.strategy import StrategyGenerateRequest


class _FakeDB:
    def __init__(self) -> None:
        self.added = []

    def add(self, obj) -> None:  # noqa: ANN001
        obj.id = 999
        self.added.append(obj)

    def commit(self) -> None:
        return None

    def refresh(self, obj) -> None:  # noqa: ANN001
        obj.is_monitoring = False
        obj.monitoring_mode = 'simulation'
        obj.monitoring_risk_percent = 1.0
        obj.created_at = datetime(2026, 4, 4, 12, 0, 0)
        obj.updated_at = datetime(2026, 4, 4, 12, 0, 0)
        return None


@pytest.mark.asyncio
async def test_generate_strategy_uses_llm_optimized_candidate(monkeypatch) -> None:
    fake_db = _FakeDB()
    fake_user = SimpleNamespace(id=1, role=Role.ADMIN)

    async def _fake_run_strategy_designer(**kwargs):  # noqa: ANN003
        return {
            'template': 'ema_crossover',
            'params': {'ema_fast': 20, 'ema_slow': 50, 'rsi_filter': 50},
            'name': 'BTCUSD H1 EMA Crossover with RSI Filter',
            'description': 'base strategy',
            'symbol': 'BTCUSD',
            'timeframe': 'H1',
            'prompt_history': [{'role': 'user', 'content': 'EMA crossover trend following with RSI filter'}],
            'market_regime': 'calm',
        }

    evaluations = [
        {
            'metrics': {
                'win_rate_pct': 0.0,
                'profit_factor': 0.0,
                'max_drawdown_pct': 0.0,
                'total_return_pct': 0.0,
                'total_trades': 0,
            },
            'backtest_window': {'start_date': '2026-01-01', 'end_date': '2026-04-01', 'lookback_days': 90},
            'generation_score': 0.0,
        },
        {
            'metrics': {
                'win_rate_pct': 30.95,
                'profit_factor': 1.1532,
                'max_drawdown_pct': 14.5088,
                'total_return_pct': 4.2407,
                'total_trades': 42,
            },
            'backtest_window': {'start_date': '2026-01-01', 'end_date': '2026-04-01', 'lookback_days': 90},
            'generation_score': 32.261,
        },
    ]

    def _fake_evaluate_generation_candidate(**kwargs):  # noqa: ANN003
        return evaluations.pop(0)

    async def _fake_llm_generate_param_candidates(**kwargs):  # noqa: ANN003
        return [
            {
                'params': {'ema_fast': 9, 'ema_slow': 21, 'rsi_filter': 30},
                'reason': 'increase activity while keeping trend-following behaviour',
                'warnings': [],
            }
        ]

    monkeypatch.setattr('app.api.routes.strategies._next_strategy_id', lambda db: 'STRAT-999')
    monkeypatch.setattr('app.api.routes.strategies._evaluate_generation_candidate', _fake_evaluate_generation_candidate)
    monkeypatch.setattr('app.api.routes.strategies._llm_generate_param_candidates', _fake_llm_generate_param_candidates)
    fake_designer_module = ModuleType('app.services.strategy.designer')
    fake_designer_module.run_strategy_designer = _fake_run_strategy_designer
    monkeypatch.setitem(sys.modules, 'app.services.strategy.designer', fake_designer_module)

    result = await generate_strategy(
        payload=StrategyGenerateRequest(prompt='EMA crossover trend following with RSI filter', pair='BTCUSD', timeframe='H1'),
        db=fake_db,
        user=fake_user,
    )

    assert result.params == {'ema_fast': 9, 'ema_slow': 21, 'rsi_filter': 30}
    assert result.metrics['generation_optimization']['optimized'] is True
    assert result.metrics['generation_optimization']['selected_source'] == 'llm_candidate_1'


@pytest.mark.asyncio
async def test_generate_strategy_uses_crypto_fallback_candidates_when_llm_returns_none(monkeypatch) -> None:
    fake_db = _FakeDB()
    fake_user = SimpleNamespace(id=1, role=Role.ADMIN)

    async def _fake_run_strategy_designer(**kwargs):  # noqa: ANN003
        return {
            'template': 'ema_crossover',
            'params': {'ema_fast': 20, 'ema_slow': 50, 'rsi_filter': 50},
            'name': 'BTCUSD H1 EMA Crossover with RSI Filter',
            'description': 'base strategy',
            'symbol': 'BTCUSD',
            'timeframe': 'H1',
            'prompt_history': [{'role': 'user', 'content': 'EMA crossover trend following with RSI filter'}],
            'market_regime': 'calm',
        }

    evaluation_by_params = {
        (20, 50, 50): {
            'metrics': {
                'win_rate_pct': 0.0,
                'profit_factor': 0.0,
                'max_drawdown_pct': 0.0,
                'total_return_pct': 0.0,
                'total_trades': 0,
            },
            'backtest_window': {'start_date': '2026-01-01', 'end_date': '2026-04-01', 'lookback_days': 90},
            'generation_score': 0.0,
        },
        (9, 21, 30): {
            'metrics': {
                'win_rate_pct': 30.95,
                'profit_factor': 1.1532,
                'max_drawdown_pct': 14.5088,
                'total_return_pct': 4.2407,
                'total_trades': 42,
            },
            'backtest_window': {'start_date': '2026-01-01', 'end_date': '2026-04-01', 'lookback_days': 90},
            'generation_score': 32.261,
        },
    }

    def _fake_evaluate_generation_candidate(**kwargs):  # noqa: ANN003
        params = kwargs['params']
        key = (params['ema_fast'], params['ema_slow'], params['rsi_filter'])
        return evaluation_by_params[key]

    async def _fake_llm_generate_param_candidates(**kwargs):  # noqa: ANN003
        return []

    monkeypatch.setattr('app.api.routes.strategies._next_strategy_id', lambda db: 'STRAT-999')
    monkeypatch.setattr('app.api.routes.strategies._evaluate_generation_candidate', _fake_evaluate_generation_candidate)
    monkeypatch.setattr('app.api.routes.strategies._llm_generate_param_candidates', _fake_llm_generate_param_candidates)
    fake_designer_module = ModuleType('app.services.strategy.designer')
    fake_designer_module.run_strategy_designer = _fake_run_strategy_designer
    monkeypatch.setitem(sys.modules, 'app.services.strategy.designer', fake_designer_module)

    result = await generate_strategy(
        payload=StrategyGenerateRequest(prompt='EMA crossover trend following with RSI filter', pair='BTCUSD', timeframe='H1'),
        db=fake_db,
        user=fake_user,
    )

    assert result.params == {'ema_fast': 9, 'ema_slow': 21, 'rsi_filter': 30}
    assert result.metrics['generation_optimization']['optimized'] is True
    assert result.metrics['generation_optimization']['selected_source'].startswith('heuristic_candidate_')


@pytest.mark.asyncio
async def test_generate_strategy_finalizes_existing_generation_trace(monkeypatch, tmp_path) -> None:
    fake_db = _FakeDB()
    fake_user = SimpleNamespace(id=1, role=Role.ADMIN)

    trace_file = tmp_path / 'strategy-BTCUSD-H1-20260404T132134Z.json'
    trace_file.write_text(
        json.dumps(
            {
                'schema_version': 2,
                'type': 'strategy_generation',
                'generated_at': '2026-04-04T13:21:34+00:00',
                'input': {
                    'pair': 'BTCUSD',
                    'timeframe': 'H1',
                    'user_prompt': 'EMA crossover trend following with RSI filter',
                },
                'result': {
                    'template': 'ema_crossover',
                    'name': 'old',
                    'description': 'old',
                    'params': {'ema_fast': 20, 'ema_slow': 50, 'rsi_filter': 50},
                },
                'metrics': None,
                'prompt_history': [{'role': 'user', 'content': 'EMA crossover trend following with RSI filter'}],
            }
        ),
        encoding='utf-8',
    )

    async def _fake_run_strategy_designer(**kwargs):  # noqa: ANN003
        return {
            'template': 'ema_crossover',
            'params': {'ema_fast': 20, 'ema_slow': 50, 'rsi_filter': 50},
            'name': 'BTCUSD H1 EMA Crossover with RSI Filter',
            'description': 'base strategy',
            'symbol': 'BTCUSD',
            'timeframe': 'H1',
            'prompt_history': [{'role': 'user', 'content': 'EMA crossover trend following with RSI filter'}],
            'market_regime': 'calm',
        }

    evaluations = [
        {
            'metrics': {
                'win_rate_pct': 0.0,
                'profit_factor': 0.0,
                'max_drawdown_pct': 0.0,
                'total_return_pct': 0.0,
                'total_trades': 0,
            },
            'backtest_window': {'start_date': '2026-01-01', 'end_date': '2026-04-01', 'lookback_days': 90},
            'generation_score': 0.0,
        },
        {
            'metrics': {
                'win_rate_pct': 30.95,
                'profit_factor': 1.1532,
                'max_drawdown_pct': 14.5088,
                'total_return_pct': 4.2407,
                'total_trades': 42,
            },
            'backtest_window': {'start_date': '2026-01-01', 'end_date': '2026-04-01', 'lookback_days': 90},
            'generation_score': 32.261,
        },
    ]

    def _fake_evaluate_generation_candidate(**kwargs):  # noqa: ANN003
        return evaluations.pop(0)

    async def _fake_llm_generate_param_candidates(**kwargs):  # noqa: ANN003
        return [
            {
                'params': {'ema_fast': 9, 'ema_slow': 21, 'rsi_filter': 30},
                'reason': 'increase activity while keeping trend-following behaviour',
                'warnings': [],
            }
        ]

    monkeypatch.setattr('app.api.routes.strategies.TRACE_DIR', str(tmp_path))
    monkeypatch.setattr('app.api.routes.strategies._next_strategy_id', lambda db: 'STRAT-999')
    monkeypatch.setattr('app.api.routes.strategies._evaluate_generation_candidate', _fake_evaluate_generation_candidate)
    monkeypatch.setattr('app.api.routes.strategies._llm_generate_param_candidates', _fake_llm_generate_param_candidates)
    fake_designer_module = ModuleType('app.services.strategy.designer')
    fake_designer_module.run_strategy_designer = _fake_run_strategy_designer
    monkeypatch.setitem(sys.modules, 'app.services.strategy.designer', fake_designer_module)

    result = await generate_strategy(
        payload=StrategyGenerateRequest(prompt='EMA crossover trend following with RSI filter', pair='BTCUSD', timeframe='H1'),
        db=fake_db,
        user=fake_user,
    )

    trace_payload = json.loads(Path(trace_file).read_text(encoding='utf-8'))
    assert result.strategy_id == 'STRAT-999'
    assert trace_payload['strategy_id'] == 'STRAT-999'
    assert trace_payload['strategy']['symbol'] == 'BTCUSD'
    assert trace_payload['result']['params'] == {'ema_fast': 9, 'ema_slow': 21, 'rsi_filter': 30}
    assert trace_payload['metrics']['generation_optimization']['selected_source'] == 'llm_candidate_1'
    assert trace_payload['template_selection']['selected_template'] == 'ema_crossover'
    assert any('template_selection' in (item.get('content') or '') for item in trace_payload['prompt_history'])


@pytest.mark.asyncio
async def test_generate_strategy_runs_generation_backtests_off_event_loop_thread(monkeypatch) -> None:
    fake_db = _FakeDB()
    fake_user = SimpleNamespace(id=1, role=Role.ADMIN)

    async def _fake_run_strategy_designer(**kwargs):  # noqa: ANN003
        return {
            'template': 'ema_crossover',
            'params': {'ema_fast': 20, 'ema_slow': 50, 'rsi_filter': 50},
            'name': 'BTCUSD H1 EMA Crossover with RSI Filter',
            'description': 'base strategy',
            'symbol': 'BTCUSD',
            'timeframe': 'H1',
            'prompt_history': [{'role': 'user', 'content': 'EMA crossover trend following with RSI filter'}],
            'market_regime': 'calm',
        }

    def _guarded_evaluate_generation_candidate(**kwargs):  # noqa: ANN003
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            params = kwargs['params']
            if params == {'ema_fast': 20, 'ema_slow': 50, 'rsi_filter': 50}:
                return {
                    'metrics': {
                        'win_rate_pct': 0.0,
                        'profit_factor': 0.0,
                        'max_drawdown_pct': 0.0,
                        'total_return_pct': 0.0,
                        'total_trades': 0,
                    },
                    'backtest_window': {'start_date': '2026-01-01', 'end_date': '2026-04-01', 'lookback_days': 90},
                    'generation_score': 0.0,
                }
            return {
                'metrics': {
                    'win_rate_pct': 30.95,
                    'profit_factor': 1.1532,
                    'max_drawdown_pct': 14.5088,
                    'total_return_pct': 4.2407,
                    'total_trades': 42,
                },
                'backtest_window': {'start_date': '2026-01-01', 'end_date': '2026-04-01', 'lookback_days': 90},
                'generation_score': 32.261,
            }
        raise RuntimeError('sync generation backtest executed on event-loop thread')

    async def _fake_llm_generate_param_candidates(**kwargs):  # noqa: ANN003
        return [
            {
                'params': {'ema_fast': 9, 'ema_slow': 21, 'rsi_filter': 30},
                'reason': 'increase activity while keeping trend-following behaviour',
                'warnings': [],
            }
        ]

    monkeypatch.setattr('app.api.routes.strategies._next_strategy_id', lambda db: 'STRAT-999')
    monkeypatch.setattr('app.api.routes.strategies._evaluate_generation_candidate', _guarded_evaluate_generation_candidate)
    monkeypatch.setattr('app.api.routes.strategies._llm_generate_param_candidates', _fake_llm_generate_param_candidates)
    fake_designer_module = ModuleType('app.services.strategy.designer')
    fake_designer_module.run_strategy_designer = _fake_run_strategy_designer
    monkeypatch.setitem(sys.modules, 'app.services.strategy.designer', fake_designer_module)

    result = await generate_strategy(
        payload=StrategyGenerateRequest(prompt='EMA crossover trend following with RSI filter', pair='BTCUSD', timeframe='H1'),
        db=fake_db,
        user=fake_user,
    )

    assert result.params == {'ema_fast': 9, 'ema_slow': 21, 'rsi_filter': 30}
    assert result.metrics['generation_optimization']['selected_source'] == 'llm_candidate_1'


@pytest.mark.asyncio
async def test_generate_strategy_realigns_name_description_and_params_after_template_override(monkeypatch) -> None:
    fake_db = _FakeDB()
    fake_user = SimpleNamespace(id=1, role=Role.ADMIN)

    async def _fake_run_strategy_designer(**kwargs):  # noqa: ANN003
        return {
            'template': None,
            'params': {},
            'name': '',
            'description': '',
            'symbol': 'BTCUSD',
            'timeframe': 'H1',
            'prompt_history': [{'role': 'user', 'content': 'Bollinger Band squeeze breakout strategy'}],
            'market_regime': 'calm',
        }

    async def _fake_llm_generate(prompt: str) -> dict | None:
        return None

    def _fake_random_choice(options):  # noqa: ANN001
        assert 'macd_divergence' in options
        return 'macd_divergence'

    monkeypatch.setattr('app.api.routes.strategies._next_strategy_id', lambda db: 'STRAT-999')
    monkeypatch.setattr('app.api.routes.strategies._llm_generate', _fake_llm_generate)
    monkeypatch.setattr('app.api.routes.strategies.random.choice', _fake_random_choice)
    monkeypatch.setattr('app.api.routes.strategies.random.randint', lambda a, b: 624)

    captured: list[dict] = []

    def _fake_evaluate_generation_candidate(**kwargs):  # noqa: ANN003
        captured.append(kwargs)
        return {
            'metrics': {
                'win_rate_pct': 60.87,
                'profit_factor': 1.7263,
                'max_drawdown_pct': 13.4535,
                'total_return_pct': 9.9363,
                'total_trades': 23,
            },
            'backtest_window': {'start_date': '2026-01-04', 'end_date': '2026-04-04', 'lookback_days': 90},
            'generation_score': 43.5493,
        }

    monkeypatch.setattr('app.api.routes.strategies._evaluate_generation_candidate', _fake_evaluate_generation_candidate)

    fake_designer_module = ModuleType('app.services.strategy.designer')
    fake_designer_module.run_strategy_designer = _fake_run_strategy_designer
    monkeypatch.setitem(sys.modules, 'app.services.strategy.designer', fake_designer_module)

    result = await generate_strategy(
        payload=StrategyGenerateRequest(prompt='Bollinger Band squeeze breakout strategy', pair='BTCUSD', timeframe='H1'),
        db=fake_db,
        user=fake_user,
    )

    assert result.template == 'bollinger_breakout'
    assert result.name != 'macd_divergence_624'
    assert 'macd_divergence' not in result.name
    assert 'macd_divergence' not in result.description
    assert result.params == {'bb_period': 28, 'bb_std': 2.25}
    assert captured[0]['template'] == 'bollinger_breakout'
    assert captured[0]['params'] == {'bb_period': 28, 'bb_std': 2.25}
