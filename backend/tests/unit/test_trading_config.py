"""Unit tests for runtime trading configuration."""

from app.services.config.trading_config import (
    get_current_values,
    get_effective_gating_policy,
    get_effective_risk_limits,
    get_effective_sizing,
    get_param_catalog,
)


def test_catalog_has_three_sections() -> None:
    catalog = get_param_catalog()
    assert "gating" in catalog
    assert "risk_limits" in catalog
    assert "sizing" in catalog


def test_catalog_params_have_descriptions() -> None:
    catalog = get_param_catalog()
    for section, params in catalog.items():
        for param in params:
            assert "key" in param, f"Missing key in {section}"
            assert "label" in param, f"Missing label in {section}/{param.get('key')}"
            assert "description" in param, f"Missing description in {section}/{param.get('key')}"
            assert len(param["description"]) > 10, f"Description too short for {section}/{param['key']}"


def test_gating_defaults_match_constants() -> None:
    """Without runtime overrides, should return the code defaults."""
    policy = get_effective_gating_policy("balanced")
    assert policy.min_combined_score == 0.22
    assert policy.min_confidence == 0.28


def test_gating_conservative_defaults() -> None:
    policy = get_effective_gating_policy("conservative")
    assert policy.min_combined_score == 0.32
    assert policy.min_aligned_sources == 2


def test_risk_limits_defaults_match() -> None:
    limits = get_effective_risk_limits("live")
    assert limits.max_daily_loss_pct == 3.0
    assert limits.max_positions == 3
    assert limits.max_currency_notional_exposure_pct_warn == 12.0
    assert limits.max_currency_notional_exposure_pct_block == 15.0
    assert limits.max_currency_open_risk_pct == 6.0


def test_sizing_defaults_match() -> None:
    sizing = get_effective_sizing()
    assert sizing["sl_atr_multiplier"] == 1.5
    assert sizing["tp_atr_multiplier"] == 2.5


def test_current_values_structure() -> None:
    values = get_current_values("balanced", "simulation")
    assert "gating" in values
    assert "risk_limits" in values
    assert "sizing" in values
    assert "min_combined_score" in values["gating"]
    assert "max_daily_loss_pct" in values["risk_limits"]
    assert "sl_atr_multiplier" in values["sizing"]


def test_unknown_mode_falls_back() -> None:
    """Unknown decision mode → balanced, unknown risk mode → live."""
    policy = get_effective_gating_policy("nonexistent")
    assert policy.min_combined_score == 0.22  # balanced default

    limits = get_effective_risk_limits("nonexistent")
    assert limits.max_daily_loss_pct == 3.0  # live default
