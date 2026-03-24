from __future__ import annotations

import re
from typing import Any, Sequence


FX_STRENGTH_KEYWORDS: dict[str, float] = {
    'rally': 1.0,
    'rallies': 1.0,
    'rebound': 0.8,
    'rebounds': 0.8,
    'gain': 1.0,
    'gains': 1.0,
    'rise': 1.0,
    'rises': 1.0,
    'rising': 1.0,
    'climb': 0.9,
    'climbs': 0.9,
    'jump': 0.95,
    'jumps': 0.95,
    'advance': 0.8,
    'advances': 0.8,
    'soar': 1.0,
    'soars': 1.0,
    'surge': 1.0,
    'surges': 1.0,
    'strengthen': 0.9,
    'strengthens': 0.9,
    'firm': 0.8,
    'firmer': 0.8,
    'firms': 0.8,
    'strength': 0.8,
    'strong': 0.8,
    'stronger': 0.9,
    'hawkish': 0.9,
    'restrictive': 0.7,
    'tightening': 0.75,
    'tighter': 0.75,
    'higher rates': 0.85,
    'rate hike': 0.95,
    'rate hikes': 0.95,
    'hike': 0.7,
    'hikes': 0.7,
    'sticky inflation': 0.7,
    'higher yields': 0.8,
    'stable': 0.5,
    'stability': 0.5,
    'resilient': 0.65,
    'resilience': 0.65,
    'anchored': 0.5,
    'supported': 0.6,
    'support': 0.55,
    'outperform': 0.85,
    'outperforms': 0.85,
    'outperformance': 0.8,
    'tailwind': 0.7,
    'tailwinds': 0.7,
    'upbeat': 0.65,
    'optimism': 0.6,
    'optimistic': 0.65,
    'surplus': 0.6,
    'inflows': 0.7,
    'recovering': 0.7,
    'recovery': 0.65,
    'buoyant': 0.6,
    'appreciation': 0.75,
    'appreciates': 0.75,
    'bullish': 0.85,
    'bid': 0.5,
    'demand': 0.5,
}

FX_WEAKNESS_KEYWORDS: dict[str, float] = {
    'selloff': 1.0,
    'sell-off': 1.0,
    'selling': 0.75,
    'drop': 1.0,
    'drops': 1.0,
    'fall': 1.0,
    'falls': 1.0,
    'falling': 1.0,
    'dip': 0.8,
    'dips': 0.8,
    'slide': 0.9,
    'slides': 0.9,
    'slip': 0.75,
    'slips': 0.75,
    'retreat': 0.8,
    'retreats': 0.8,
    'tumble': 0.95,
    'tumbles': 0.95,
    'loss': 0.75,
    'losses': 0.75,
    'weaken': 0.9,
    'weakens': 0.9,
    'soft': 0.7,
    'softer': 0.8,
    'weak': 0.9,
    'weaker': 0.95,
    'weakness': 0.85,
    'dovish': 0.9,
    'rate cut': 0.95,
    'rate cuts': 0.95,
    'cuts': 0.7,
    'cooler inflation': 0.7,
    'recession': 1.0,
    'pressure': 0.6,
    'pressured': 0.65,
    'headwind': 0.7,
    'headwinds': 0.7,
    'underperform': 0.85,
    'underperforms': 0.85,
    'pessimism': 0.6,
    'pessimistic': 0.65,
    'outflows': 0.7,
    'depreciation': 0.75,
    'depreciates': 0.75,
    'bearish': 0.85,
    'slump': 0.9,
    'slumps': 0.9,
    'decline': 0.8,
    'declines': 0.8,
    'declining': 0.8,
    'deficit': 0.55,
    'drag': 0.55,
    'fragile': 0.6,
    'vulnerable': 0.6,
}


def _boundary_pattern(term: str) -> str:
    token = str(term or '').strip().lower()
    if not token:
        return ''
    return rf'(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])'


def _keyword_weight(text: str, weights: dict[str, float]) -> float:
    lowered = str(text or '').lower()
    total = 0.0
    for keyword, weight in weights.items():
        pattern = _boundary_pattern(keyword)
        if pattern and re.search(pattern, lowered):
            total += weight
    return total


def _keyword_hit_count(text: str, keywords: Sequence[str]) -> int:
    lowered = str(text or '').lower()
    hits = 0
    for keyword in keywords:
        pattern = _boundary_pattern(keyword)
        if pattern and re.search(pattern, lowered):
            hits += 1
    return hits


def _local_currency_score(text: str, aliases: Sequence[str]) -> tuple[float, int]:
    lowered = str(text or '').lower()
    total = 0.0
    mentions = 0
    for alias in aliases:
        pattern = _boundary_pattern(alias)
        if not pattern:
            continue
        for match in re.finditer(pattern, lowered):
            mentions += 1
            start = max(match.start() - 48, 0)
            end = min(match.end() + 48, len(lowered))
            window = lowered[start:end]
            total += _keyword_weight(window, FX_STRENGTH_KEYWORDS)
            total -= _keyword_weight(window, FX_WEAKNESS_KEYWORDS)
    return total, mentions


def _score_to_effect(score: float, support: float) -> str:
    if support < 0.15:
        return 'unknown'
    if score >= 0.35:
        return 'strengthening'
    if score <= -0.35:
        return 'weakening'
    return 'unknown'


def _pair_bias_from_effects(
    *,
    base_effect: str,
    quote_effect: str,
    base_weight: float,
    quote_weight: float,
) -> tuple[str, float]:
    score = 0.0
    if base_effect == 'strengthening':
        score += base_weight
    elif base_effect == 'weakening':
        score -= base_weight

    if quote_effect == 'strengthening':
        score -= quote_weight
    elif quote_effect == 'weakening':
        score += quote_weight

    same_direction = (
        base_effect in {'strengthening', 'weakening'}
        and quote_effect in {'strengthening', 'weakening'}
        and base_effect == quote_effect
    )
    if same_direction and abs(base_weight - quote_weight) <= 0.22:
        return 'neutral', 0.0
    if score >= 0.2:
        return 'bullish', round(min(score, 1.0), 3)
    if score <= -0.2:
        return 'bearish', round(max(score, -1.0), 3)
    return 'neutral', round(score, 3)


def map_fx_effects_to_pair_bias(
    *,
    base_effect: str,
    quote_effect: str,
    base_weight: float = 1.0,
    quote_weight: float = 1.0,
) -> dict[str, Any]:
    pair_effect, pair_bias_score = _pair_bias_from_effects(
        base_effect=base_effect,
        quote_effect=quote_effect,
        base_weight=max(float(base_weight or 0.0), 0.0),
        quote_weight=max(float(quote_weight or 0.0), 0.0),
    )
    return {
        'pair_directional_effect': pair_effect,
        'pair_bias_score': round(pair_bias_score, 3),
    }


def infer_fx_pair_bias(
    text: str,
    *,
    base_currency: str | None,
    quote_currency: str | None,
    base_aliases: Sequence[str],
    quote_aliases: Sequence[str],
    base_relevance: float = 0.0,
    quote_relevance: float = 0.0,
) -> dict[str, Any]:
    lowered = str(text or '').lower()
    if not lowered or not base_currency or not quote_currency:
        return {
            'impacted_currencies': [],
            'impact_on_base': 'unknown',
            'impact_on_quote': 'unknown',
            'base_currency_effect': 'unknown',
            'quote_currency_effect': 'unknown',
            'pair_directional_effect': 'neutral',
            'pair_bias_score': 0.0,
        }

    base_local_score, base_mentions = _local_currency_score(lowered, base_aliases)
    quote_local_score, quote_mentions = _local_currency_score(lowered, quote_aliases)
    base_presence = base_mentions > 0 or float(base_relevance or 0.0) >= 0.2
    quote_presence = quote_mentions > 0 or float(quote_relevance or 0.0) >= 0.2
    base_support = min(max(float(base_relevance or 0.0), base_mentions * 0.18), 1.0)
    quote_support = min(max(float(quote_relevance or 0.0), quote_mentions * 0.18), 1.0)

    global_score = _keyword_weight(lowered, FX_STRENGTH_KEYWORDS) - _keyword_weight(lowered, FX_WEAKNESS_KEYWORDS)
    support_delta = base_support - quote_support

    if base_local_score == 0.0 and base_presence and abs(global_score) >= 0.85 and support_delta >= 0.18:
        base_local_score = global_score
    if quote_local_score == 0.0 and quote_presence and abs(global_score) >= 0.85 and support_delta <= -0.18:
        quote_local_score = global_score

    base_effect = _score_to_effect(base_local_score, base_support)
    quote_effect = _score_to_effect(quote_local_score, quote_support)
    pair_effect, pair_bias_score = _pair_bias_from_effects(
        base_effect=base_effect,
        quote_effect=quote_effect,
        base_weight=max(base_support, 0.0),
        quote_weight=max(quote_support, 0.0),
    )

    impacted_currencies: list[str] = []
    if base_effect != 'unknown':
        impacted_currencies.append(str(base_currency))
    if quote_effect != 'unknown':
        impacted_currencies.append(str(quote_currency))

    if not impacted_currencies and ((base_presence and quote_presence) or abs(global_score) < 0.55):
        pair_effect = 'neutral'
        pair_bias_score = 0.0

    return {
        'impacted_currencies': impacted_currencies,
        'impact_on_base': base_effect,
        'impact_on_quote': quote_effect,
        'base_currency_effect': base_effect,
        'quote_currency_effect': quote_effect,
        'pair_directional_effect': pair_effect,
        'pair_bias_score': round(pair_bias_score, 3),
    }
