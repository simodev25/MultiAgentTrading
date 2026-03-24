"""
Instrument-aware helpers for orchestrator agents and traces.

This module centralizes:
- canonical instrument prompt variables
- provider resolution traces
- instrument-aware news evidence profiling

It keeps backward-compatible aliases for legacy FX-centric payloads while adding
generic instrument fields used by the refactored multi-asset flow.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.market.instrument import (
    AssetClass,
    InstrumentClassifier,
    InstrumentDescriptor,
    get_instrument_direction_assets,
    is_instrument_fx_like,
    normalize_instrument,
)
from app.services.market.symbol_providers import (
    SymbolResolutionResult,
    get_news_candidates_for_instrument,
    resolve_symbol_for_provider,
)
from app.services.news.instrument_news import analyze_news_for_instrument


MACRO_TAGS = (
    'inflation',
    'cpi',
    'ppi',
    'rates',
    'yield',
    'employment',
    'payroll',
    'gdp',
    'growth',
    'pmi',
    'energy',
    'oil',
    'gas',
    'risk-on',
    'risk off',
    'risk-off',
    'volatility',
    'geopolitical',
    'war',
)

CRYPTO_SECTOR_TAGS = (
    'crypto',
    'cryptocurrency',
    'digital asset',
    'token',
    'exchange',
    'wallet',
    'stablecoin',
    'etf',
    'regulation',
    'sec',
    'staking',
    'validator',
    'listing',
    'delisting',
    'hack',
    'exploit',
    'on-chain',
    'onchain',
)

INDEX_TAGS = (
    'stocks',
    'equity market',
    'equity markets',
    'risk appetite',
    'broad market',
    'index',
    'volatility',
    'earnings season',
)

COMMODITY_TAGS = (
    'supply',
    'demand',
    'inventory',
    'storage',
    'opec',
    'output',
    'production',
    'shipment',
    'refinery',
)

KNOWN_ALIASES: dict[str, tuple[str, ...]] = {
    'USD': ('usd', 'dollar', 'greenback', 'fed', 'treasury', 'u.s. yields', 'us yields'),
    'EUR': ('eur', 'euro', 'ecb'),
    'GBP': ('gbp', 'sterling', 'pound', 'boe'),
    'JPY': ('jpy', 'yen', 'boj'),
    'CHF': ('chf', 'swiss franc', 'snb'),
    'CAD': ('cad', 'canadian dollar', 'loonie', 'boc'),
    'AUD': ('aud', 'aussie', 'rba'),
    'NZD': ('nzd', 'kiwi', 'rbnz'),
    'BTC': ('btc', 'bitcoin'),
    'ETH': ('eth', 'ethereum', 'ether'),
    'SOL': ('sol', 'solana'),
    'ADA': ('ada', 'cardano'),
    'DOT': ('dot', 'polkadot'),
    'LTC': ('ltc', 'litecoin'),
    'XRP': ('xrp', 'ripple'),
    'DOGE': ('doge', 'dogecoin'),
    'XAU': ('xau', 'gold', 'bullion'),
    'XAG': ('xag', 'silver'),
    'CL': ('cl', 'crude', 'crude oil', 'wti', 'oil'),
    'BZ': ('bz', 'brent', 'brent crude'),
    'NG': ('ng', 'natural gas', 'natgas'),
    '^GSPC': ('s&p 500', 'spx', 'sp500'),
    '^NDX': ('nasdaq 100', 'ndx', 'nas100', 'nasdaq'),
    '^DJI': ('dow jones', 'dow 30', 'us30'),
    '^GDAXI': ('dax', 'ger40', 'de40'),
    '^FTSE': ('ftse 100', 'uk100'),
    '^FCHI': ('cac 40', 'fra40'),
    '^N225': ('nikkei', 'jp225', 'nikkei 225'),
    '^VIX': ('vix', 'fear gauge'),
    'SPX500': ('s&p 500', 'spx', 'sp500', 'us500'),
    'US500': ('s&p 500', 'spx', 'sp500', 'us500'),
    'NAS100': ('nasdaq 100', 'nas100', 'nsdq100'),
    'NSDQ100': ('nasdaq 100', 'nas100', 'nsdq100'),
    'GER40': ('dax', 'ger40', 'de40'),
    'DE40': ('dax', 'ger40', 'de40'),
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(float(value), low), high)


def _boundary_pattern(term: str) -> str:
    token = str(term or '').strip().lower()
    if not token:
        return ''
    return rf'(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])'


def _hit_count(text: str, aliases: tuple[str, ...]) -> int:
    lowered = str(text or '').lower()
    hits = 0
    for alias in aliases:
        pattern = _boundary_pattern(alias)
        if pattern and re.search(pattern, lowered):
            hits += 1
    return hits


def _extract_tags(text: str, candidates: tuple[str, ...], *, limit: int = 6) -> list[str]:
    lowered = str(text or '').lower()
    tags: list[str] = []
    for candidate in candidates:
        if len(tags) >= limit:
            break
        pattern = _boundary_pattern(candidate)
        if pattern and re.search(pattern, lowered):
            tags.append(candidate)
    return tags


def _asset_aliases(asset: str | None, instrument: InstrumentDescriptor) -> tuple[str, ...]:
    key = str(asset or '').strip().upper()
    if not key:
        return tuple()

    aliases = list(KNOWN_ALIASES.get(key, tuple()))
    aliases.append(key.lower())

    if instrument.display_symbol:
        display = str(instrument.display_symbol).strip()
        if display and display.lower() not in aliases:
            aliases.append(display.lower())

    deduped: list[str] = []
    for item in aliases:
        value = str(item or '').strip().lower()
        if value and value not in deduped:
            deduped.append(value)
    return tuple(deduped)


def _normalize_directional_effect(value: Any) -> str:
    effect = str(value or 'neutral').strip().lower()
    if effect in {'bullish', 'bearish', 'neutral'}:
        return effect
    if effect == 'strengthening':
        return 'bullish'
    if effect == 'weakening':
        return 'bearish'
    return 'neutral'


def _normalize_asset_effect(value: Any) -> str:
    effect = str(value or 'unknown').strip().lower()
    if effect in {'strengthening', 'weakening', 'unknown'}:
        return effect
    return 'unknown'


def _resolution_to_dict(result: SymbolResolutionResult | None) -> dict[str, Any] | None:
    return result.to_dict() if result is not None else None


def _source_matches_instrument(source_symbol: str | None, instrument: InstrumentDescriptor) -> bool:
    if not str(source_symbol or '').strip():
        return False
    source_instrument = normalize_instrument(source_symbol)
    if source_instrument.canonical_symbol == instrument.canonical_symbol:
        return True
    if source_instrument.reference_asset and instrument.reference_asset:
        return source_instrument.reference_asset == instrument.reference_asset
    if source_instrument.base_asset and instrument.base_asset:
        return source_instrument.base_asset == instrument.base_asset
    return False


def get_instrument_for_pair(pair: str | None) -> InstrumentDescriptor:
    if not pair:
        return InstrumentClassifier.classify(None)
    return normalize_instrument(pair)


def build_instrument_context(
    pair: str | None,
    *,
    provider: str | None = None,
    provider_symbol: str | None = None,
) -> dict[str, Any]:
    instrument = get_instrument_for_pair(pair)
    primary_asset, secondary_asset = get_instrument_direction_assets(instrument)
    resolution = resolve_symbol_for_provider(pair, provider, instrument) if provider else None

    resolved_provider_symbol = (
        str(provider_symbol or '').strip()
        or (resolution.provider_symbol if resolution and resolution.success else None)
        or (instrument.provider_symbols.get(provider) if provider else None)
        or instrument.provider_symbol
        or instrument.raw_symbol
    )

    instrument_dict = instrument.to_dict()
    instrument_dict['provider'] = provider or instrument.provider
    instrument_dict['provider_symbol'] = resolved_provider_symbol

    return {
        'instrument': instrument,
        'instrument_dict': instrument_dict,
        'pair': str(pair or ''),
        'canonical_symbol': instrument.canonical_symbol,
        'display_symbol': instrument.display_symbol,
        'asset_class': instrument.asset_class.value,
        'instrument_type': instrument.instrument_type.value,
        'market': instrument.market,
        'venue': instrument.venue,
        'primary_asset': primary_asset,
        'secondary_asset': secondary_asset,
        'reference_asset': instrument.reference_asset,
        'base_asset': instrument.base_asset,
        'quote_asset': instrument.quote_asset,
        'provider': provider or instrument.provider,
        'provider_symbol': resolved_provider_symbol,
        'provider_resolution': _resolution_to_dict(resolution),
        'has_base_quote': instrument.has_base_quote,
        'is_cfd': instrument.is_cfd,
    }


def build_instrument_prompt_variables(
    pair: str | None,
    *,
    provider: str | None = None,
    provider_symbol: str | None = None,
) -> dict[str, Any]:
    context = build_instrument_context(pair, provider=provider, provider_symbol=provider_symbol)
    return {
        'pair': context['pair'],
        'raw_symbol': context['instrument_dict']['raw_symbol'],
        'canonical_symbol': context['canonical_symbol'],
        'display_symbol': context['display_symbol'],
        'asset_class': context['asset_class'],
        'instrument_type': context['instrument_type'],
        'market': context['market'] or 'unknown',
        'venue': context['venue'] or 'unknown',
        'provider': context['provider'] or 'internal',
        'provider_symbol': context['provider_symbol'] or context['pair'],
        'primary_asset': context['primary_asset'] or 'N/A',
        'secondary_asset': context['secondary_asset'] or 'N/A',
        'reference_asset': context['reference_asset'] or 'N/A',
        'base_asset': context['base_asset'] or 'N/A',
        'quote_asset': context['quote_asset'] or 'N/A',
        'has_base_quote': context['has_base_quote'],
        'is_cfd': context['is_cfd'],
    }


def instrument_aware_asset_class(pair: str | None) -> str:
    return get_instrument_for_pair(pair).asset_class.value


def instrument_aware_effects_for_item(
    item: dict[str, Any],
    *,
    pair: str,
) -> dict[str, Any]:
    instrument = get_instrument_for_pair(pair)
    primary_asset, secondary_asset = get_instrument_direction_assets(instrument)
    title = str(item.get('title') or item.get('event_name') or '')
    summary = str(item.get('summary') or item.get('description') or '')
    text = f'{title} {summary}'.strip()

    analysis_result = analyze_news_for_instrument(
        text,
        instrument,
        base_relevance=_clamp(_safe_float(item.get('base_currency_relevance'), 0.0), 0.0, 1.0),
        quote_relevance=_clamp(_safe_float(item.get('quote_currency_relevance'), 0.0), 0.0, 1.0),
        macro_relevance=_clamp(_safe_float(item.get('macro_relevance'), 0.0), 0.0, 1.0),
    )

    directional_effect = _normalize_directional_effect(analysis_result.get('instrument_directional_effect'))
    primary_effect = _normalize_asset_effect(analysis_result.get('primary_asset_effect'))
    secondary_effect = _normalize_asset_effect(analysis_result.get('secondary_asset_effect'))
    impacted_assets = [str(asset).strip().upper() for asset in analysis_result.get('impacted_assets', []) if str(asset).strip()]

    payload = {
        'asset_class': instrument.asset_class.value,
        'instrument_type': instrument.instrument_type.value,
        'primary_asset': primary_asset,
        'secondary_asset': secondary_asset,
        'primary_asset_effect': primary_effect,
        'secondary_asset_effect': secondary_effect,
        'instrument_directional_effect': directional_effect,
        'instrument_bias_score': round(_safe_float(analysis_result.get('instrument_bias_score'), 0.0), 3),
        'signal_case': str(analysis_result.get('signal_case') or 'no_signal'),
        'confidence': round(_clamp(_safe_float(analysis_result.get('confidence'), 0.0), 0.0, 1.0), 3),
        'impacted_assets': impacted_assets,
        'regime_context': str(analysis_result.get('regime_context') or 'unknown'),
        'impacted_currencies': impacted_assets if is_instrument_fx_like(instrument) else [],
        'impact_on_base': primary_effect,
        'impact_on_quote': secondary_effect,
        'base_currency_effect': primary_effect if is_instrument_fx_like(instrument) else 'unknown',
        'quote_currency_effect': secondary_effect if is_instrument_fx_like(instrument) else 'unknown',
        'pair_directional_effect': directional_effect if is_instrument_fx_like(instrument) else None,
        'pair_bias_score': round(_safe_float(analysis_result.get('instrument_bias_score'), 0.0), 3)
        if is_instrument_fx_like(instrument)
        else 0.0,
    }
    return payload


def instrument_aware_evidence_profile(
    item: dict[str, Any],
    *,
    pair: str,
    provider_symbol: str | None = None,
    macro: bool = False,
) -> dict[str, Any]:
    instrument = get_instrument_for_pair(pair)
    instrument_ctx = build_instrument_context(pair, provider_symbol=provider_symbol)
    primary_asset = instrument_ctx['primary_asset']
    secondary_asset = instrument_ctx['secondary_asset']
    title = str(item.get('title') or item.get('event_name') or '')
    summary = str(item.get('summary') or item.get('description') or '')
    text = f'{title} {summary}'.strip().lower()

    provider_pair_relevance = _clamp(_safe_float(item.get('pair_relevance'), 0.0), 0.0, 1.0)
    base_relevance = _clamp(_safe_float(item.get('base_currency_relevance'), 0.0), 0.0, 1.0)
    quote_relevance = _clamp(_safe_float(item.get('quote_currency_relevance'), 0.0), 0.0, 1.0)
    macro_relevance = _clamp(_safe_float(item.get('macro_relevance'), 0.0), 0.0, 1.0)
    freshness_raw = item.get('freshness_score')
    credibility_raw = item.get('credibility_score')
    freshness = _clamp(_safe_float(freshness_raw, 0.5 if freshness_raw is None else 0.0), 0.0, 1.0)
    credibility = _clamp(_safe_float(credibility_raw, 0.5 if credibility_raw is None else 0.0), 0.0, 1.0)
    source_symbol = str(item.get('source_symbol') or provider_symbol or '').strip()

    primary_hits = _hit_count(text, _asset_aliases(primary_asset, instrument))
    secondary_hits = _hit_count(text, _asset_aliases(secondary_asset, instrument)) if secondary_asset else 0
    canonical_hits = _hit_count(text, (instrument.canonical_symbol.lower(),))
    source_matches = _source_matches_instrument(source_symbol, instrument)

    effects = instrument_aware_effects_for_item(item, pair=pair)
    directional_effect = effects['instrument_directional_effect']
    analysis_confidence = _safe_float(effects.get('confidence'), 0.0)
    signal_case = str(effects.get('signal_case') or 'no_signal')

    asset_class = instrument.asset_class.value
    if is_instrument_fx_like(instrument):
        if primary_hits > 0 and secondary_hits > 0:
            category = 'direct_pair'
        elif primary_hits > 0:
            category = 'direct_primary_asset'
        elif secondary_hits > 0:
            category = 'direct_secondary_asset'
        elif macro and macro_relevance >= 0.35:
            category = 'relevant_macro'
        elif signal_case == 'weak_signal' or macro_relevance >= 0.18 or provider_pair_relevance >= 0.18:
            category = 'weakly_indirect'
        else:
            category = 'irrelevant'
    elif asset_class == AssetClass.CRYPTO.value:
        if source_matches or primary_hits > 0 or canonical_hits > 0:
            category = 'direct_instrument'
        elif signal_case == 'directional_signal':
            category = 'sector_related'
        elif signal_case == 'weak_signal' or secondary_hits > 0 or macro_relevance >= 0.18:
            category = 'weakly_indirect'
        else:
            category = 'irrelevant'
    elif asset_class in {AssetClass.INDEX.value, AssetClass.EQUITY.value, AssetClass.ETF.value}:
        if source_matches or primary_hits > 0 or canonical_hits > 0:
            category = 'direct_instrument'
        elif macro and (signal_case == 'directional_signal' or macro_relevance >= 0.3):
            category = 'relevant_macro'
        elif signal_case == 'weak_signal' or provider_pair_relevance >= 0.18:
            category = 'weakly_indirect'
        else:
            category = 'irrelevant'
    elif asset_class in {AssetClass.METAL.value, AssetClass.ENERGY.value, AssetClass.COMMODITY.value, AssetClass.FUTURE.value}:
        if source_matches or primary_hits > 0 or canonical_hits > 0:
            category = 'direct_instrument'
        elif signal_case in {'directional_signal', 'weak_signal'} or macro_relevance >= 0.25:
            category = 'relevant_macro'
        elif provider_pair_relevance >= 0.18:
            category = 'weakly_indirect'
        else:
            category = 'irrelevant'
    else:
        if source_matches or primary_hits > 0 or canonical_hits > 0:
            category = 'direct_instrument'
        elif signal_case == 'directional_signal':
            category = 'sector_related'
        elif provider_pair_relevance >= 0.18 or macro_relevance >= 0.18:
            category = 'weakly_indirect'
        else:
            category = 'irrelevant'

    category_floor = {
        'direct_pair': 0.84,
        'direct_primary_asset': 0.72,
        'direct_secondary_asset': 0.72,
        'direct_instrument': 0.80,
        'relevant_macro': 0.56,
        'sector_related': 0.36,
        'weakly_indirect': 0.18,
        'irrelevant': 0.04,
    }.get(category, 0.04)

    support = max(provider_pair_relevance, base_relevance, quote_relevance, macro_relevance, analysis_confidence)
    final_pair_relevance = category_floor * 0.50 + support * 0.25 + freshness * 0.15 + credibility * 0.10
    if category == 'sector_related':
        final_pair_relevance *= 0.82
    elif category == 'weakly_indirect':
        final_pair_relevance *= 0.58
    elif category == 'irrelevant':
        final_pair_relevance *= 0.15
    if source_symbol and not source_matches and category not in {'direct_pair', 'direct_primary_asset', 'direct_secondary_asset', 'direct_instrument'}:
        final_pair_relevance *= 0.88
    final_pair_relevance = round(_clamp(final_pair_relevance, 0.0, 1.0), 3)

    directional_eligible = (
        directional_effect in {'bullish', 'bearish'}
        and category in {
            'direct_pair',
            'direct_primary_asset',
            'direct_secondary_asset',
            'direct_instrument',
            'relevant_macro',
            'sector_related',
        }
        and final_pair_relevance >= 0.5
    )

    tags = _extract_tags(text, MACRO_TAGS)
    if asset_class == AssetClass.CRYPTO.value:
        tags = tags or _extract_tags(text, CRYPTO_SECTOR_TAGS)
    elif asset_class == AssetClass.INDEX.value:
        tags = tags or _extract_tags(text, INDEX_TAGS)
    elif asset_class in {AssetClass.METAL.value, AssetClass.ENERGY.value, AssetClass.COMMODITY.value}:
        tags = tags or _extract_tags(text, COMMODITY_TAGS)

    asset_symbols_detected: list[str] = []
    if primary_hits > 0 and primary_asset:
        asset_symbols_detected.append(str(primary_asset))
    if secondary_hits > 0 and secondary_asset and secondary_asset not in asset_symbols_detected:
        asset_symbols_detected.append(str(secondary_asset))
    if source_matches and instrument.canonical_symbol not in asset_symbols_detected:
        asset_symbols_detected.append(instrument.canonical_symbol)

    return {
        'asset_class': asset_class,
        'instrument_type': instrument.instrument_type.value,
        'relevance_category': category,
        'direct_pair_relevance': round(_clamp(max(provider_pair_relevance, base_relevance, quote_relevance), 0.0, 1.0), 3),
        'indirect_macro_relevance': round(_clamp(max(macro_relevance, analysis_confidence * 0.5), 0.0, 1.0), 3),
        'final_pair_relevance': final_pair_relevance,
        'asset_symbols_detected': asset_symbols_detected,
        'macro_tags': tags,
        'directional_eligible': directional_eligible,
        'provider_pair_relevance': round(provider_pair_relevance, 3),
        'source_symbol_match': source_matches,
        **effects,
    }


def instrument_aware_headline_sentiment(
    headlines: str,
    *,
    pair: str | None = None,
) -> tuple[str, float]:
    lines = [
        str(line).strip().lstrip('-').strip()
        for line in str(headlines or '').splitlines()
        if str(line).strip()
    ]
    if not lines or not pair:
        return 'neutral', 0.0

    instrument = get_instrument_for_pair(pair)
    combined = 0.0
    weight_sum = 0.0

    for line in lines:
        analysis = analyze_news_for_instrument(line, instrument)
        effect = _normalize_directional_effect(analysis.get('instrument_directional_effect'))
        score = _safe_float(analysis.get('instrument_bias_score'), 0.0)
        confidence = max(_safe_float(analysis.get('confidence'), 0.0), 0.1 if effect in {'bullish', 'bearish'} else 0.0)
        if confidence <= 0.0:
            continue
        combined += score * confidence
        weight_sum += confidence

    if weight_sum == 0.0:
        return 'neutral', 0.0

    normalized = combined / weight_sum
    if normalized >= 0.06:
        signal = 'bullish'
    elif normalized <= -0.06:
        signal = 'bearish'
    else:
        signal = 'neutral'
    return signal, round(_clamp(normalized, -0.2, 0.2), 3)
