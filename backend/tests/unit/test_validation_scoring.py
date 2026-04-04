from app.services.strategy.validation_scoring import compute_validation_score, should_validate_strategy


def test_compute_validation_score_rewards_profitable_robust_sample() -> None:
    score, raw_score, sample_factor = compute_validation_score(
        win_rate=42.9,
        profit_factor=1.4,
        max_dd=27.52,
        total_return=28.49,
        trades=49,
    )

    assert sample_factor == 1.0
    assert raw_score >= 40.0
    assert score >= 40.0


def test_compute_validation_score_keeps_negative_return_profile_rejected() -> None:
    score, raw_score, sample_factor = compute_validation_score(
        win_rate=24.5,
        profit_factor=0.39,
        max_dd=28.43,
        total_return=-24.4,
        trades=49,
    )

    assert sample_factor == 1.0
    assert raw_score < 25.0
    assert score < 20.0


def test_should_validate_strategy_accepts_profitable_volatile_profile_with_sufficient_quality() -> None:
    assert should_validate_strategy(
        score=40.892,
        total_return=28.49,
        profit_factor=1.4,
        max_dd=27.52,
    ) is True


def test_should_validate_strategy_rejects_negative_return_even_if_score_is_near_threshold() -> None:
    assert should_validate_strategy(
        score=43.301,
        total_return=-0.6427,
        profit_factor=0.8907,
        max_dd=2.54,
    ) is False


def test_should_validate_strategy_rejects_excessive_drawdown() -> None:
    assert should_validate_strategy(
        score=47.86,
        total_return=20.2639,
        profit_factor=1.3475,
        max_dd=42.2035,
    ) is False
