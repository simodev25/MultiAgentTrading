from app.services.strategy.generation_optimizer import (
    choose_best_generation_candidate,
    compute_generation_candidate_score,
    should_optimize_generation,
)


def test_should_optimize_generation_when_candidate_has_no_trades() -> None:
    metrics = {
        'win_rate_pct': 0.0,
        'profit_factor': 0.0,
        'max_drawdown_pct': 0.0,
        'total_return_pct': 0.0,
        'total_trades': 0,
    }

    assert should_optimize_generation(metrics) is True


def test_should_not_optimize_generation_when_candidate_is_already_credible() -> None:
    metrics = {
        'win_rate_pct': 41.0,
        'profit_factor': 1.45,
        'max_drawdown_pct': 9.5,
        'total_return_pct': 12.0,
        'total_trades': 48,
    }

    assert should_optimize_generation(metrics) is False


def test_choose_best_generation_candidate_prefers_live_candidate_over_inactive_one() -> None:
    base_candidate = {
        'source': 'base',
        'params': {'ema_fast': 20, 'ema_slow': 50, 'rsi_filter': 50},
        'metrics': {
            'win_rate_pct': 0.0,
            'profit_factor': 0.0,
            'max_drawdown_pct': 0.0,
            'total_return_pct': 0.0,
            'total_trades': 0,
        },
    }
    adapted_candidate = {
        'source': 'llm_adapted',
        'params': {'ema_fast': 9, 'ema_slow': 21, 'rsi_filter': 30},
        'metrics': {
            'win_rate_pct': 30.95,
            'profit_factor': 1.1532,
            'max_drawdown_pct': 14.5088,
            'total_return_pct': 4.2407,
            'total_trades': 42,
        },
    }

    assert compute_generation_candidate_score(adapted_candidate['metrics']) > compute_generation_candidate_score(base_candidate['metrics'])
    assert choose_best_generation_candidate([base_candidate, adapted_candidate])['source'] == 'llm_adapted'
