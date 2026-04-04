from app.services.strategy.template_selection import apply_template_selection_policy


def test_exact_match_wins_over_regime_preference() -> None:
    selection = apply_template_selection_policy(
        user_prompt="Generate an ema crossover strategy for EURUSD",
        proposed_template="rsi_mean_reversion",
        market_regime="ranging",
    )

    assert selection["selected_template"] == "ema_crossover"
    assert selection["request_fidelity"] == "exact"
    assert selection["match_basis"] == "explicit_template_request"
    assert selection["requested_archetype"] == "ema_crossover"


def test_approximation_is_explicitly_labeled_when_exact_template_missing() -> None:
    selection = apply_template_selection_policy(
        user_prompt="Create a martingale strategy",
        proposed_template="ema_crossover",
        market_regime="trending_up",
    )

    assert selection["selected_template"] == "ema_crossover"
    assert selection["request_fidelity"] == "approximation"
    assert selection["match_basis"] == "approximation_due_to_missing_exact_template"
    assert any("No exact template" in warning for warning in selection["warnings"])


def test_best_current_fit_only_when_user_explicitly_requests_it() -> None:
    # Without explicit best-fit intent, explicit request must not be overridden.
    selection_without_best_fit = apply_template_selection_policy(
        user_prompt="I want a supertrend strategy",
        proposed_template="rsi_mean_reversion",
        market_regime="ranging",
    )
    assert selection_without_best_fit["selected_template"] == "supertrend"
    assert selection_without_best_fit["match_basis"] == "explicit_template_request"

    # With explicit best-fit intent, model recommendation can be used.
    selection_with_best_fit = apply_template_selection_policy(
        user_prompt="Use the best current fit strategy for this market",
        proposed_template="rsi_mean_reversion",
        market_regime="ranging",
    )
    assert selection_with_best_fit["selected_template"] == "rsi_mean_reversion"
    assert selection_with_best_fit["match_basis"] == "best_current_fit_request"
    assert selection_with_best_fit["request_fidelity"] == "best_fit_requested"
