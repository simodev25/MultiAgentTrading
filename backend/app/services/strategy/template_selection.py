from __future__ import annotations

import re
from typing import Iterable

from app.services.strategy.template_catalog import EXECUTABLE_STRATEGY_TEMPLATES

_CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    'trend': ('trend following', 'trend-following', 'trend'),
    'mean_reversion': ('mean reversion', 'mean-reversion', 'contrarian', 'reversion'),
    'breakout': ('breakout', 'squeeze'),
    'momentum': ('momentum',),
    'hybrid': ('hybrid', 'combined', 'multi indicator'),
}

_CATEGORY_DEFAULT_TEMPLATE: dict[str, str] = {
    'trend': 'ema_crossover',
    'mean_reversion': 'rsi_mean_reversion',
    'breakout': 'bollinger_breakout',
    'momentum': 'macd_divergence',
    'hybrid': 'macd_rsi_combo',
}

_BEST_FIT_PATTERNS: tuple[str, ...] = (
    'best current fit',
    'best fit for current market',
    'best strategy for current market',
    'best template for current market',
    'fit current market conditions',
)

_FREEFORM_ARCHETYPE_PATTERN = re.compile(
    r'\b(?:create|generate|design|build|want|need|use)\s+(?:an?\s+)?([a-z0-9][a-z0-9_\- ]{1,40})\s+strategy\b',
    re.IGNORECASE,
)
_FREEFORM_SKIP_PHRASES: tuple[str, ...] = (
    'best current fit',
    'best fit',
    'current market',
    'this market',
)

_MARKET_FIT_MATRIX: dict[str, dict[str, str]] = {
    'trending_up': {
        'trend': 'strong',
        'breakout': 'good',
        'momentum': 'good',
        'mean_reversion': 'poor',
        'hybrid': 'good',
    },
    'trending_down': {
        'trend': 'strong',
        'breakout': 'good',
        'momentum': 'good',
        'mean_reversion': 'poor',
        'hybrid': 'good',
    },
    'ranging': {
        'trend': 'poor',
        'breakout': 'watch',
        'momentum': 'watch',
        'mean_reversion': 'strong',
        'hybrid': 'good',
    },
    'volatile': {
        'trend': 'watch',
        'breakout': 'strong',
        'momentum': 'good',
        'mean_reversion': 'poor',
        'hybrid': 'watch',
    },
    'calm': {
        'trend': 'watch',
        'breakout': 'poor',
        'momentum': 'watch',
        'mean_reversion': 'good',
        'hybrid': 'good',
    },
}


def _normalize_text(text: str) -> str:
    lowered = str(text or '').lower()
    return re.sub(r'\s+', ' ', lowered).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    pattern = rf'(?<![a-z0-9_]){re.escape(phrase)}(?![a-z0-9_])'
    return re.search(pattern, text) is not None


def _extract_requested_template(prompt: str, available_templates: set[str]) -> str | None:
    for template in sorted(available_templates):
        variants = {
            template,
            template.replace('_', ' '),
            template.replace('_', '-'),
        }
        if any(_contains_phrase(prompt, variant) for variant in variants):
            return template
    return None


def _extract_requested_category(prompt: str) -> str | None:
    for category, aliases in _CATEGORY_ALIASES.items():
        if any(_contains_phrase(prompt, alias) for alias in aliases):
            return category
    return None


def _extract_freeform_requested_archetype(prompt: str) -> str | None:
    match = _FREEFORM_ARCHETYPE_PATTERN.search(prompt)
    if not match:
        return None
    archetype = match.group(1).strip().replace('-', ' ').replace('_', ' ')
    if not archetype:
        return None
    archetype = re.sub(r'\s+', ' ', archetype)
    if any(phrase in archetype for phrase in _FREEFORM_SKIP_PHRASES):
        return None
    return archetype


def _best_current_fit_requested(prompt: str) -> bool:
    return any(pattern in prompt for pattern in _BEST_FIT_PATTERNS)


def _category_templates(category: str, available_templates: set[str]) -> list[str]:
    return [
        template
        for template in sorted(available_templates)
        if EXECUTABLE_STRATEGY_TEMPLATES.get(template)
        and EXECUTABLE_STRATEGY_TEMPLATES[template].category == category
    ]


def _compute_market_fit(selected_template: str | None, market_regime: str | None) -> str:
    if not selected_template:
        return 'unknown'
    template_spec = EXECUTABLE_STRATEGY_TEMPLATES.get(selected_template)
    if template_spec is None:
        return 'unknown'
    regime_key = _normalize_text(market_regime or '')
    if not regime_key:
        return 'unknown'
    return _MARKET_FIT_MATRIX.get(regime_key, {}).get(template_spec.category, 'unknown')


def _compute_deployment_quality(
    request_fidelity: str,
    market_fit: str,
    custom_strategy_required: bool,
) -> str:
    if custom_strategy_required:
        return 'blocked_custom_strategy_required'
    if request_fidelity == 'approximation':
        return 'review_required_approximation'
    if market_fit in {'poor'}:
        return 'degraded_market_alignment'
    if market_fit in {'watch'}:
        return 'deploy_with_caution'
    if request_fidelity in {'best_fit_requested', 'exact', 'archetype'} and market_fit in {'strong', 'good'}:
        return 'ready'
    if market_fit == 'unknown':
        return 'unknown_market_fit'
    return 'review_required'


def apply_template_selection_policy(
    *,
    user_prompt: str,
    proposed_template: str | None,
    market_regime: str | None = None,
    available_templates: Iterable[str] | None = None,
) -> dict:
    available_set = set(available_templates or EXECUTABLE_STRATEGY_TEMPLATES.keys())
    prompt = _normalize_text(user_prompt)
    warnings: list[str] = []

    requested_template = _extract_requested_template(prompt, available_set)
    requested_category = _extract_requested_category(prompt)
    requested_freeform = _extract_freeform_requested_archetype(prompt)
    requested_archetype = requested_template or requested_category or requested_freeform
    best_fit_requested = _best_current_fit_requested(prompt)

    selected_template: str | None = None
    match_basis = 'model_recommendation'
    request_fidelity = 'inferred'
    custom_strategy_required = False

    # 1) Explicit user request match
    if requested_template:
        selected_template = requested_template
        match_basis = 'explicit_template_request'
        request_fidelity = 'exact'
        if proposed_template and proposed_template != requested_template:
            warnings.append(
                f'Explicit request "{requested_template}" enforced; model proposal "{proposed_template}" ignored.'
            )

    # 2) Direct template availability for explicit archetype categories
    elif requested_category:
        category_templates = _category_templates(requested_category, available_set)
        if category_templates:
            if proposed_template in category_templates:
                selected_template = proposed_template
            else:
                preferred = _CATEGORY_DEFAULT_TEMPLATE.get(requested_category)
                selected_template = preferred if preferred in category_templates else category_templates[0]
            match_basis = 'explicit_archetype_request'
            request_fidelity = 'archetype'

    # No exact template match
    elif requested_freeform:
        if proposed_template in available_set:
            selected_template = proposed_template
            match_basis = 'approximation_due_to_missing_exact_template'
            request_fidelity = 'approximation'
            warnings.append(
                f'No exact template for requested archetype "{requested_freeform}". '
                f'Approximated with "{selected_template}".'
            )
        else:
            custom_strategy_required = True
            match_basis = 'custom_strategy_required'
            request_fidelity = 'custom_required'
            warnings.append(
                f'No exact template for requested archetype "{requested_freeform}". '
                'custom_strategy_required=true'
            )

    # 3) Implementation fidelity baseline when no explicit intent
    if selected_template is None and proposed_template in available_set:
        selected_template = proposed_template
        if best_fit_requested:
            match_basis = 'best_current_fit_request'
            request_fidelity = 'best_fit_requested'
        else:
            match_basis = 'model_recommendation'
            request_fidelity = 'inferred'

    if selected_template is None and not custom_strategy_required:
        selected_template = sorted(available_set)[0] if available_set else None
        match_basis = 'fallback_first_available'
        request_fidelity = 'approximation'
        if selected_template:
            warnings.append(
                f'No direct template proposal available. Falling back to "{selected_template}" as approximation.'
            )
        else:
            custom_strategy_required = True
            match_basis = 'custom_strategy_required'
            request_fidelity = 'custom_required'

    # 4) Market regime fit (qualification only, no silent override)
    market_fit = _compute_market_fit(selected_template, market_regime)
    if request_fidelity in {'exact', 'archetype'} and market_fit in {'poor', 'watch'}:
        warnings.append(
            f'Requested template "{selected_template}" has {market_fit} fit for current regime "{market_regime}".'
        )

    # 5) Warning generation + deployment quality
    deployment_quality = _compute_deployment_quality(request_fidelity, market_fit, custom_strategy_required)

    return {
        'selected_template': selected_template,
        'requested_archetype': requested_archetype,
        'match_basis': match_basis,
        'request_fidelity': request_fidelity,
        'market_fit': market_fit,
        'deployment_quality': deployment_quality,
        'warnings': warnings,
        'custom_strategy_required': custom_strategy_required,
    }
