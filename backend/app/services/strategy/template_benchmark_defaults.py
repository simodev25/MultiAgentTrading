from __future__ import annotations

from typing import Any

from app.services.strategy.template_catalog import (
    EXECUTABLE_STRATEGY_PARAM_RANGES,
    EXECUTABLE_STRATEGY_TEMPLATES,
    sanitize_strategy_params_for_template,
)


_STRING_DEFAULTS: dict[str, dict[str, Any]] = {
    'pivot_points': {'pivot_type': 'standard'},
}


def benchmark_params_for_template(template: str) -> dict[str, Any]:
    if template not in EXECUTABLE_STRATEGY_TEMPLATES:
        raise ValueError(f'Unsupported template: {template}')

    ranges = EXECUTABLE_STRATEGY_PARAM_RANGES.get(template, {})
    raw_params: dict[str, Any] = dict(_STRING_DEFAULTS.get(template, {}))

    for key, bounds in ranges.items():
        lo, hi = bounds
        midpoint = (float(lo) + float(hi)) / 2.0
        if isinstance(lo, int) and isinstance(hi, int):
            raw_params[key] = int(round(midpoint))
        else:
            raw_params[key] = round(midpoint, 2)

    sanitized, _warnings = sanitize_strategy_params_for_template(template, raw_params)
    return sanitized
