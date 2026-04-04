from app.services.strategy.template_benchmark_defaults import benchmark_params_for_template
from app.services.strategy.template_catalog import EXECUTABLE_STRATEGY_TEMPLATES


def test_benchmark_params_exist_for_all_executable_templates() -> None:
    for template in EXECUTABLE_STRATEGY_TEMPLATES:
        params = benchmark_params_for_template(template)
        assert isinstance(params, dict)
        assert params


def test_benchmark_params_include_pivot_type_for_pivot_points() -> None:
    params = benchmark_params_for_template('pivot_points')
    assert params['pivot_type'] == 'standard'
    assert params['lookback'] >= 1
