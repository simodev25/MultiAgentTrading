"""
Generic News Effect Analyzer - Multi-Asset Instrument-Aware

This module replaces the FX-only fx_pair_bias.py logic with a generic
instrument-aware analysis that works across all asset classes.

For FX pairs: analyzes base currency and quote currency effects separately.
For crypto: analyzes the primary crypto asset.
For indices: analyzes sector/macro effects.
For equities: analyzes company-specific and sector effects.
For commodities/metals: analyzes the commodity and its macro context.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.market.instrument import (
    AssetClass,
    InstrumentDescriptor,
    InstrumentClassifier,
    is_instrument_fx_like,
    is_instrument_crypto_like,
    get_instrument_direction_assets,
)
from app.services.news.fx_pair_bias import infer_fx_pair_bias


# Generic directional keywords for all asset classes
GENERIC_STRENGTH_KEYWORDS: dict[str, float] = {
    'rally': 1.0,
    'rebound': 0.8,
    'gain': 1.0,
    'gains': 1.0,
    'rise': 1.0,
    'rises': 1.0,
    'rising': 1.0,
    'surge': 1.1,
    'surges': 1.1,
    'surge': 1.1,
    'surges': 1.1,
    'strengthen': 0.9,
    'strengthens': 0.9,
    'firm': 0.8,
    'firmer': 0.8,
    'firms': 0.8,
    'strength': 0.8,
    'strong': 0.8,
    'stronger': 0.9,
    'higher': 0.7,
    'higher prices': 0.85,
    'bullish': 0.9,
    'upgrade': 0.8,
    'upgrades': 0.8,
    'breakout': 0.9,
    'all-time high': 1.0,
    ' ATH ': 1.0,
}

GENERIC_WEAKNESS_KEYWORDS: dict[str, float] = {
    'selloff': 1.0,
    'sell-off': 1.0,
    'drop': 1.0,
    'drops': 1.0,
    'fall': 1.0,
    'falls': 1.0,
    'falling': 1.0,
    'plunge': 1.1,
    'plunges': 1.1,
    'loss': 0.75,
    'losses': 0.75,
    'weaken': 0.9,
    'weakens': 0.9,
    'soft': 0.7,
    'softer': 0.8,
    'weak': 0.9,
    'weaker': 0.95,
    'lower': 0.7,
    'lower prices': 0.85,
    'bearish': 0.9,
    'downgrade': 0.9,
    'downgrades': 0.9,
    'breakdown': 0.9,
    'all-time low': 1.0,
    ' ATL ': 1.0,
}

# FX-specific strength/weakness (central bank, monetary policy)
FX_SPECIFIC_STRENGTH: dict[str, float] = {
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
}

FX_SPECIFIC_WEAKNESS: dict[str, float] = {
    'dovish': 0.9,
    'rate cut': 0.95,
    'rate cuts': 0.95,
    'cuts': 0.7,
    'cooler inflation': 0.7,
    'recession': 1.0,
    'lower yields': 0.8,
}

# Crypto-specific catalysts
CRYPTO_CATALYST_KEYWORDS: dict[str, float] = {
    'etf approval': 1.0,
    'etf approve': 1.0,
    'spot etf': 0.95,
    'sec approve': 0.9,
    'regulation': -0.3,  # Context-dependent
    'adoption': 0.8,
    'listing': 0.7,
    'delisting': -0.7,
    'hack': -0.8,
    'exploit': -0.8,
    'upgrade': 0.6,
    'fork': 0.5,
    'airdrop': 0.4,
    'burn': 0.5,
    'unlock': -0.4,
    'staking': 0.4,
    'validator': 0.3,
    'on-chain': 0.2,
    'whale': -0.2,
}

# Equity/Index-specific
EQUITY_STRENGTH_KEYWORDS: dict[str, float] = {
    'earnings beat': 0.9,
    'revenue beat': 0.8,
    'guidance raise': 0.85,
    'buy rating': 0.8,
    'outperform': 0.7,
    'market share gain': 0.7,
    'product launch': 0.5,
    'partnership': 0.5,
    'acquisition': 0.4,
}

EQUITY_WEAKNESS_KEYWORDS: dict[str, float] = {
    'earnings miss': -0.9,
    'revenue miss': -0.8,
    'guidance cut': -0.85,
    'sell rating': -0.8,
    'underperform': -0.7,
    'lawsuit': -0.7,
    'investigation': -0.6,
    'recall': -0.5,
    'bankruptcy': -1.0,
}

# Commodity/Energy-specific
COMMODITY_STRENGTH_KEYWORDS: dict[str, float] = {
    'supply shortage': 0.9,
    'supply disruption': 0.85,
    'demand surge': 0.9,
    'opec cut': 0.8,
    'inventory draw': 0.7,
    'storage decline': 0.7,
}

COMMODITY_WEAKNESS_KEYWORDS: dict[str, float] = {
    'supply glut': -0.9,
    'demand slowdown': -0.85,
    'opec increase': -0.8,
    'inventory build': -0.7,
    'storage increase': -0.7,
}


def _boundary_pattern(term: str) -> str:
    """Create a word boundary regex pattern for a term."""
    token = str(term or '').strip().lower()
    if not token:
        return ''
    return rf'(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])'


def _keyword_weight(text: str, weights: dict[str, float]) -> float:
    """Calculate weighted keyword score for text."""
    lowered = str(text or '').lower()
    total = 0.0
    for keyword, weight in weights.items():
        pattern = _boundary_pattern(keyword)
        if pattern and re.search(pattern, lowered):
            total += weight
    return total


def _keyword_balance(text: str, positive: dict[str, float], negative: dict[str, float]) -> float:
    """Calculate signed keyword balance from positive and negative dictionaries."""
    return _keyword_weight(text, positive) - _keyword_weight(text, negative)


def _score_to_asset_effect(score: float, threshold: float = 0.4) -> str:
    """Convert a signed score to strengthening/weakening semantics."""
    if abs(score) < threshold:
        return 'unknown'
    return 'strengthening' if score > 0 else 'weakening'


def _score_to_directional_bias(score: float, threshold: float = 0.25) -> str:
    """Convert a signed score to bullish/bearish/neutral semantics."""
    if score >= threshold:
        return 'bullish'
    if score <= -threshold:
        return 'bearish'
    return 'neutral'


def _get_asset_aliases(asset: str | None) -> tuple[str, ...]:
    """Get search aliases for an asset (currency, crypto, commodity)."""
    if not asset:
        return tuple()

    key = str(asset).strip().upper()
    mapping: dict[str, tuple[str, ...]] = {
        # Fiat currencies
        'USD': ('usd', 'dollar', 'greenback', 'fed', 'treasury', 'us yields', 'us inflation', 'us cpi', 'us payrolls'),
        'EUR': ('eur', 'euro', 'ecb'),
        'GBP': ('gbp', 'sterling', 'pound', 'boe'),
        'JPY': ('jpy', 'yen', 'boj'),
        'CHF': ('chf', 'swiss franc', 'snb'),
        'CAD': ('cad', 'canadian dollar', 'loonie', 'boc'),
        'AUD': ('aud', 'aussie', 'rba'),
        'NZD': ('nzd', 'kiwi', 'rbnz'),
        # Cryptos
        'BTC': ('btc', 'bitcoin'),
        'ETH': ('eth', 'ethereum'),
        'ADA': ('ada', 'cardano'),
        'AVAX': ('avax', 'avalanche'),
        'SOL': ('sol', 'solana'),
        'XRP': ('xrp', 'ripple'),
        'DOGE': ('doge', 'dogecoin'),
        # Commodities
        'XAU': ('xau', 'gold'),
        'XAG': ('xag', 'silver'),
        'CL': ('oil', 'crude', 'petroleum'),
        'BZ': ('brent', 'crude oil'),
    }

    if key in mapping:
        return mapping[key]
    return (key.lower(),)


def _detect_market_regime(text: str) -> dict[str, Any]:
    """Detect general market regime from text (risk-on/risk-off, volatility)."""
    lowered = str(text or '').lower()
    regime = 'neutral'
    confidence = 0.5

    risk_on_indicators = ['risk appetite', 'risk-on', 'risk on', 'bull market', 'rally', 'sentiment improves']
    risk_off_indicators = ['risk-off', 'risk off', 'risk aversion', 'bear market', 'flight to safety', 'selloff']

    risk_on_hits = sum(1 for phrase in risk_on_indicators if phrase in lowered)
    risk_off_hits = sum(1 for phrase in risk_off_indicators if phrase in lowered)

    if risk_on_hits > risk_off_hits:
        regime = 'risk_on'
        confidence = min(0.5 + risk_on_hits * 0.15, 0.9)
    elif risk_off_hits > risk_on_hits:
        regime = 'risk_off'
        confidence = min(0.5 + risk_off_hits * 0.15, 0.9)

    return {'regime': regime, 'confidence': confidence}


class InstrumentAwareNewsAnalyzer:
    """
    Analyzes news impact generically across all asset classes.

    This replaces the FX-only infer_fx_pair_bias logic with a unified
    approach that adapts its analysis based on the instrument type.
    """

    @staticmethod
    def analyze(
        text: str,
        instrument: InstrumentDescriptor,
        *,
        base_relevance: float = 0.0,
        quote_relevance: float = 0.0,
        macro_relevance: float = 0.0,
    ) -> dict[str, Any]:
        """
        Analyze news impact for a given instrument.

        Args:
            text: The news text to analyze
            instrument: The instrument descriptor
            base_relevance: Provider-provided relevance score for base asset mentions
            quote_relevance: Provider-provided relevance score for quote asset mentions
            macro_relevance: Provider-provided relevance for macro relevance

        Returns:
            Dict with directional analysis results appropriate for the instrument type.
        """
        if not text or not text.strip():
            return InstrumentAwareNewsAnalyzer._empty_result(instrument)

        lowered = str(text).lower()

        # Get primary/secondary assets for directional analysis
        primary_asset, secondary_asset = get_instrument_direction_assets(instrument)

        # Build keyword sets based on asset class
        if is_instrument_fx_like(instrument):
            return InstrumentAwareNewsAnalyzer._analyze_fx(
                lowered, instrument, primary_asset, secondary_asset,
                base_relevance=base_relevance, quote_relevance=quote_relevance, macro_relevance=macro_relevance
            )
        elif is_instrument_crypto_like(instrument):
            return InstrumentAwareNewsAnalyzer._analyze_crypto(
                lowered, instrument, primary_asset, secondary_asset,
                base_relevance=base_relevance, quote_relevance=quote_relevance, macro_relevance=macro_relevance
            )
        elif instrument.asset_class == AssetClass.INDEX:
            return InstrumentAwareNewsAnalyzer._analyze_index(
                lowered, instrument, primary_asset,
                base_relevance=base_relevance, macro_relevance=macro_relevance
            )
        elif instrument.asset_class == AssetClass.EQUITY:
            return InstrumentAwareNewsAnalyzer._analyze_equity(
                lowered, instrument, primary_asset,
                base_relevance=base_relevance, macro_relevance=macro_relevance
            )
        elif instrument.asset_class == AssetClass.METAL:
            return InstrumentAwareNewsAnalyzer._analyze_metal(
                lowered, instrument, primary_asset,
                base_relevance=base_relevance, macro_relevance=macro_relevance
            )
        elif instrument.asset_class == AssetClass.ENERGY:
            return InstrumentAwareNewsAnalyzer._analyze_energy(
                lowered, instrument, primary_asset,
                base_relevance=base_relevance, macro_relevance=macro_relevance
            )
        else:
            return InstrumentAwareNewsAnalyzer._analyze_generic(
                lowered, instrument, primary_asset,
                base_relevance=base_relevance, macro_relevance=macro_relevance
            )

    @staticmethod
    def _empty_result(instrument: InstrumentDescriptor) -> dict[str, Any]:
        """Return empty result structure."""
        return {
            'instrument_type': instrument.instrument_type.value,
            'asset_class': instrument.asset_class.value,
            'primary_asset_effect': 'unknown',
            'secondary_asset_effect': 'unknown',
            'instrument_directional_effect': 'neutral',
            'instrument_bias_score': 0.0,
            'confidence': 0.0,
            'impacted_assets': [],
            'regime_context': 'unknown',
            'signal_case': 'no_signal',
        }

    @staticmethod
    def _analyze_fx(
        text: str,
        instrument: InstrumentDescriptor,
        base_asset: str | None,
        quote_asset: str | None,
        *,
        base_relevance: float = 0.0,
        quote_relevance: float = 0.0,
        macro_relevance: float = 0.0,
    ) -> dict[str, Any]:
        if not base_asset or not quote_asset:
            return InstrumentAwareNewsAnalyzer._empty_result(instrument)

        base_aliases = _get_asset_aliases(base_asset)
        quote_aliases = _get_asset_aliases(quote_asset)
        fx_bias = infer_fx_pair_bias(
            text,
            base_currency=base_asset,
            quote_currency=quote_asset,
            base_aliases=base_aliases,
            quote_aliases=quote_aliases,
            base_relevance=base_relevance,
            quote_relevance=quote_relevance,
        )

        base_hits = sum(len(re.findall(_boundary_pattern(alias), text)) for alias in base_aliases if _boundary_pattern(alias))
        quote_hits = sum(len(re.findall(_boundary_pattern(alias), text)) for alias in quote_aliases if _boundary_pattern(alias))
        base_effect = str(fx_bias.get('impact_on_base') or 'unknown')
        quote_effect = str(fx_bias.get('impact_on_quote') or 'unknown')
        pair_effect = str(fx_bias.get('pair_directional_effect') or 'neutral')
        pair_score = float(fx_bias.get('pair_bias_score') or 0.0)
        base_support = min(max(base_relevance, base_hits * 0.18), 1.0)
        quote_support = min(max(quote_relevance, quote_hits * 0.18), 1.0)
        impacted_assets = [str(item) for item in fx_bias.get('impacted_currencies', []) if str(item).strip()]

        if pair_effect in {'bullish', 'bearish'} and abs(pair_score) >= 0.2:
            signal_case = 'directional_signal'
        elif impacted_assets or base_hits or quote_hits or max(base_support, quote_support) >= 0.2:
            signal_case = 'weak_signal'
        else:
            signal_case = 'no_signal'

        regime_info = _detect_market_regime(text)

        return {
            'instrument_type': instrument.instrument_type.value,
            'asset_class': instrument.asset_class.value,
            'primary_asset': base_asset,
            'secondary_asset': quote_asset,
            'primary_asset_effect': base_effect,
            'secondary_asset_effect': quote_effect,
            'instrument_directional_effect': pair_effect,
            'instrument_bias_score': round(pair_score, 3),
            'confidence': round(min(max(base_support, quote_support) * 0.7 + min(base_support, quote_support) * 0.3 + abs(pair_score) * 0.25, 0.95), 3),
            'impacted_assets': impacted_assets,
            'regime_context': regime_info['regime'],
            'signal_case': signal_case,
            'base_hits': base_hits,
            'quote_hits': quote_hits,
        }

    @staticmethod
    def _analyze_crypto(
        text: str,
        instrument: InstrumentDescriptor,
        base_asset: str | None,
        quote_asset: str | None,
        *,
        base_relevance: float = 0.0,
        quote_relevance: float = 0.0,
        macro_relevance: float = 0.0,
    ) -> dict[str, Any]:
        """
        Analyze news for crypto pairs.

        For crypto, we focus on:
        1. Direct mentions of the base crypto
        2. Crypto sector catalysts (ETF, regulation, adoption)
        3. General crypto market sentiment
        """
        if not base_asset:
            return InstrumentAwareNewsAnalyzer._empty_result(instrument)

        base_aliases = _get_asset_aliases(base_asset)
        crypto_hits = 0
        crypto_score = 0.0

        for alias in base_aliases:
            pattern = _boundary_pattern(alias)
            if pattern:
                matches = list(re.finditer(pattern, text))
                crypto_hits += len(matches)
                for match in matches:
                    window_start = max(match.start() - 48, 0)
                    window_end = min(match.end() + 48, len(text))
                    window = text[window_start:window_end]
                    crypto_score += _keyword_balance(window, GENERIC_STRENGTH_KEYWORDS, GENERIC_WEAKNESS_KEYWORDS)
                    crypto_score += _keyword_weight(window, CRYPTO_CATALYST_KEYWORDS)

        # Check for crypto-specific catalysts in broader text
        catalyst_score = _keyword_weight(text, CRYPTO_CATALYST_KEYWORDS)
        sector_hits = 0
        sector_keywords = ['crypto', 'cryptocurrency', 'digital asset', 'token', 'blockchain', 'exchange']
        for keyword in sector_keywords:
            pattern = _boundary_pattern(keyword)
            if pattern and re.search(pattern, text):
                sector_hits += 1

        # Calculate effect
        support = min(max(base_relevance, crypto_hits * 0.18, sector_hits * 0.15), 1.0)
        combined_score = crypto_score + catalyst_score * 0.5

        effect = _score_to_asset_effect(combined_score, threshold=0.25)
        directional_effect = _score_to_directional_bias(combined_score, threshold=0.22)

        # Detect regime context
        regime_info = _detect_market_regime(text)

        # Determine signal case
        if crypto_hits == 0 and sector_hits == 0:
            signal_case = 'no_signal'
        elif crypto_hits > 0 and abs(crypto_score) >= 0.3:
            signal_case = 'directional_signal'
        elif sector_hits > 0 and catalyst_score != 0:
            signal_case = 'weak_signal'
        else:
            signal_case = 'no_signal'

        return {
            'instrument_type': instrument.instrument_type.value,
            'asset_class': instrument.asset_class.value,
            'primary_asset': base_asset,
            'secondary_asset': quote_asset,
            'primary_asset_effect': effect,
            'secondary_asset_effect': 'unknown',
            'instrument_directional_effect': directional_effect,
            'instrument_bias_score': round(combined_score, 3),
            'confidence': round(min(support + abs(catalyst_score) * 0.2, 0.95), 3),
            'impacted_assets': [base_asset] if directional_effect != 'neutral' else [],
            'regime_context': regime_info['regime'],
            'signal_case': signal_case,
            'base_hits': crypto_hits,
            'sector_hits': sector_hits,
        }

    @staticmethod
    def _analyze_index(
        text: str,
        instrument: InstrumentDescriptor,
        reference_asset: str | None,
        *,
        base_relevance: float = 0.0,
        macro_relevance: float = 0.0,
    ) -> dict[str, Any]:
        """
        Analyze news for index instruments.

        For indices, we focus on:
        1. Market-wide sentiment
        2. Sector/industry trends
        3. Macro economic factors
        4. Volatility context
        """
        ref_aliases = _get_asset_aliases(reference_asset)

        # Check for index mentions
        index_hits = 0
        for alias in ref_aliases:
            pattern = _boundary_pattern(alias)
            if pattern:
                index_hits += len(re.findall(pattern, text))

        # Calculate generic market sentiment
        sentiment_score = _keyword_balance(text, GENERIC_STRENGTH_KEYWORDS, GENERIC_WEAKNESS_KEYWORDS)

        # Macro factors
        macro_score = _keyword_balance(text, FX_SPECIFIC_STRENGTH, FX_SPECIFIC_WEAKNESS)

        # Volatility indicators
        vol_hits = 0
        vol_keywords = ['vix', 'volatility', 'uncertainty', 'fear', 'gauge']
        for keyword in vol_keywords:
            pattern = _boundary_pattern(keyword)
            if pattern and re.search(pattern, text):
                vol_hits += 1

        # Calculate effect
        support = min(max(macro_relevance, base_relevance, index_hits * 0.15), 1.0)
        combined_score = sentiment_score + macro_score * 0.5

        effect = _score_to_asset_effect(combined_score, threshold=0.25)
        directional_effect = 'neutral' if vol_hits > 2 else _score_to_directional_bias(combined_score, threshold=0.22)

        regime_info = _detect_market_regime(text)

        if index_hits == 0 and macro_relevance < 0.2:
            signal_case = 'no_signal'
        elif abs(combined_score) >= 0.4:
            signal_case = 'directional_signal'
        else:
            signal_case = 'weak_signal'

        return {
            'instrument_type': instrument.instrument_type.value,
            'asset_class': instrument.asset_class.value,
            'primary_asset': reference_asset,
            'secondary_asset': None,
            'primary_asset_effect': effect,
            'secondary_asset_effect': 'unknown',
            'instrument_directional_effect': directional_effect,
            'instrument_bias_score': round(combined_score, 3),
            'confidence': round(min(support, 0.9), 3),
            'impacted_assets': [reference_asset] if directional_effect != 'neutral' and reference_asset else [],
            'regime_context': regime_info['regime'],
            'signal_case': signal_case,
            'index_hits': index_hits,
            'volatility_hits': vol_hits,
        }

    @staticmethod
    def _analyze_equity(
        text: str,
        instrument: InstrumentDescriptor,
        equity_asset: str | None,
        *,
        base_relevance: float = 0.0,
        macro_relevance: float = 0.0,
    ) -> dict[str, Any]:
        """
        Analyze news for equity instruments.

        For equities, we focus on:
        1. Company-specific news
        2. Sector trends
        3. Market sentiment
        """
        equity_aliases = _get_asset_aliases(equity_asset)

        # Company/symbol hits
        equity_hits = 0
        for alias in equity_aliases:
            pattern = _boundary_pattern(alias)
            if pattern:
                equity_hits += len(re.findall(pattern, text))

        # Company-specific sentiment
        company_score = _keyword_balance(text, EQUITY_STRENGTH_KEYWORDS, {k: abs(v) for k, v in EQUITY_WEAKNESS_KEYWORDS.items()})

        # Market sentiment
        market_score = _keyword_balance(text, GENERIC_STRENGTH_KEYWORDS, GENERIC_WEAKNESS_KEYWORDS)

        # Determine effect
        support = min(max(base_relevance, macro_relevance, equity_hits * 0.2), 1.0)
        combined_score = company_score * 0.7 + market_score * 0.3

        effect = _score_to_asset_effect(combined_score, threshold=0.25)
        directional_effect = _score_to_directional_bias(combined_score, threshold=0.22)

        regime_info = _detect_market_regime(text)

        if equity_hits == 0 and macro_relevance < 0.2:
            signal_case = 'no_signal'
        elif equity_hits > 0 and abs(company_score) >= 0.3:
            signal_case = 'directional_signal'
        else:
            signal_case = 'weak_signal'

        return {
            'instrument_type': instrument.instrument_type.value,
            'asset_class': instrument.asset_class.value,
            'primary_asset': equity_asset,
            'secondary_asset': None,
            'primary_asset_effect': effect,
            'secondary_asset_effect': 'unknown',
            'instrument_directional_effect': directional_effect,
            'instrument_bias_score': round(combined_score, 3),
            'confidence': round(min(support, 0.9), 3),
            'impacted_assets': [equity_asset] if directional_effect != 'neutral' and equity_asset else [],
            'regime_context': regime_info['regime'],
            'signal_case': signal_case,
            'equity_hits': equity_hits,
        }

    @staticmethod
    def _analyze_metal(
        text: str,
        instrument: InstrumentDescriptor,
        metal_asset: str | None,
        *,
        base_relevance: float = 0.0,
        macro_relevance: float = 0.0,
    ) -> dict[str, Any]:
        """
        Analyze news for metal commodities (gold, silver).

        For metals, we focus on:
        1. Metal-specific news (gold demand, mining, ETF flows)
        2. Inflation/macro context (gold as inflation hedge)
        3. USD context (gold inversely correlated with USD)
        4. Safe-haven demand
        """
        metal_aliases = _get_asset_aliases(metal_asset)

        # Metal hits
        metal_hits = 0
        for alias in metal_aliases:
            pattern = _boundary_pattern(alias)
            if pattern:
                metal_hits += len(re.findall(pattern, text))

        # Metal-specific score
        metal_score = _keyword_balance(text, GENERIC_STRENGTH_KEYWORDS, GENERIC_WEAKNESS_KEYWORDS)

        # Inflation hedge context
        inflation_keywords = ['inflation', 'cpi', 'ppi', 'real yields', 'negative real rates']
        inflation_score = 0.0
        for keyword in inflation_keywords:
            pattern = _boundary_pattern(keyword)
            if pattern:
                matches = list(re.finditer(pattern, text))
                inflation_score += len(matches) * 0.4

        # Safe haven keywords
        safe_haven_keywords = ['safe haven', 'flight to safety', 'uncertainty', 'crisis', 'geopolitical']
        safe_haven_score = 0.0
        for keyword in safe_haven_keywords:
            pattern = _boundary_pattern(keyword)
            if pattern:
                safe_haven_score += len(re.findall(pattern, text)) * 0.5

        # USD context (gold inversely related)
        usd_keywords = ['dollar strength', 'usd rally', 'dxy', 'us dollar']
        usd_score = 0.0
        for keyword in usd_keywords:
            pattern = _boundary_pattern(keyword)
            if pattern:
                usd_score += len(re.findall(pattern, text)) * 0.3

        # Calculate effect
        support = min(max(base_relevance, macro_relevance, metal_hits * 0.2, inflation_score * 0.3), 1.0)
        combined_score = metal_score + inflation_score * 0.4 + safe_haven_score * 0.3 - usd_score * 0.2

        effect = _score_to_asset_effect(combined_score, threshold=0.2)
        directional_effect = _score_to_directional_bias(combined_score, threshold=0.18)

        regime_info = _detect_market_regime(text)

        if metal_hits == 0 and macro_relevance < 0.2:
            signal_case = 'no_signal'
        elif abs(combined_score) >= 0.35:
            signal_case = 'directional_signal'
        else:
            signal_case = 'weak_signal'

        return {
            'instrument_type': instrument.instrument_type.value,
            'asset_class': instrument.asset_class.value,
            'primary_asset': metal_asset,
            'secondary_asset': 'USD',
            'primary_asset_effect': effect,
            'secondary_asset_effect': 'unknown',
            'instrument_directional_effect': directional_effect,
            'instrument_bias_score': round(combined_score, 3),
            'confidence': round(min(support, 0.9), 3),
            'impacted_assets': [metal_asset] if directional_effect != 'neutral' and metal_asset else [],
            'regime_context': regime_info['regime'],
            'signal_case': signal_case,
            'metal_hits': metal_hits,
        }

    @staticmethod
    def _analyze_energy(
        text: str,
        instrument: InstrumentDescriptor,
        energy_asset: str | None,
        *,
        base_relevance: float = 0.0,
        macro_relevance: float = 0.0,
    ) -> dict[str, Any]:
        """
        Analyze news for energy commodities (oil, natural gas).

        For energy, we focus on:
        1. Supply/demand dynamics
        2. OPEC and production decisions
        3. Inventory data
        4. Global growth expectations
        """
        energy_aliases = _get_asset_aliases(energy_asset)

        # Energy hits
        energy_hits = 0
        for alias in energy_aliases:
            pattern = _boundary_pattern(alias)
            if pattern:
                energy_hits += len(re.findall(pattern, text))

        # Energy-specific score
        energy_score = _keyword_balance(
            text,
            COMMODITY_STRENGTH_KEYWORDS,
            {k: abs(v) for k, v in COMMODITY_WEAKNESS_KEYWORDS.items()},
        )

        # Calculate effect
        support = min(max(base_relevance, macro_relevance, energy_hits * 0.2), 1.0)

        effect = _score_to_asset_effect(energy_score, threshold=0.25)
        directional_effect = _score_to_directional_bias(energy_score, threshold=0.22)

        regime_info = _detect_market_regime(text)

        if energy_hits == 0 and macro_relevance < 0.2:
            signal_case = 'no_signal'
        elif abs(energy_score) >= 0.35:
            signal_case = 'directional_signal'
        else:
            signal_case = 'weak_signal'

        return {
            'instrument_type': instrument.instrument_type.value,
            'asset_class': instrument.asset_class.value,
            'primary_asset': energy_asset,
            'secondary_asset': 'USD',
            'primary_asset_effect': effect,
            'secondary_asset_effect': 'unknown',
            'instrument_directional_effect': directional_effect,
            'instrument_bias_score': round(energy_score, 3),
            'confidence': round(min(support, 0.9), 3),
            'impacted_assets': [energy_asset] if directional_effect != 'neutral' and energy_asset else [],
            'regime_context': regime_info['regime'],
            'signal_case': signal_case,
            'energy_hits': energy_hits,
        }

    @staticmethod
    def _analyze_generic(
        text: str,
        instrument: InstrumentDescriptor,
        primary_asset: str | None,
        *,
        base_relevance: float = 0.0,
        macro_relevance: float = 0.0,
    ) -> dict[str, Any]:
        """
        Generic fallback analysis for unknown instrument types.

        Uses basic keyword sentiment without asset-specific logic.
        """
        aliases = _get_asset_aliases(primary_asset)
        asset_hits = 0
        asset_score = 0.0

        for alias in aliases:
            pattern = _boundary_pattern(alias)
            if pattern:
                matches = list(re.finditer(pattern, text))
                asset_hits += len(matches)
                for match in matches:
                    window_start = max(match.start() - 48, 0)
                    window_end = min(match.end() + 48, len(text))
                    window = text[window_start:window_end]
                    asset_score += _keyword_balance(window, GENERIC_STRENGTH_KEYWORDS, GENERIC_WEAKNESS_KEYWORDS)

        # Generic sentiment
        sentiment_score = _keyword_balance(text, GENERIC_STRENGTH_KEYWORDS, GENERIC_WEAKNESS_KEYWORDS)

        support = min(max(base_relevance, macro_relevance, asset_hits * 0.15), 1.0)
        combined_score = asset_score * 0.6 + sentiment_score * 0.4

        effect = _score_to_asset_effect(combined_score, threshold=0.2)
        directional_effect = _score_to_directional_bias(combined_score, threshold=0.18)

        regime_info = _detect_market_regime(text)

        if asset_hits == 0:
            signal_case = 'no_signal'
        elif abs(combined_score) >= 0.3:
            signal_case = 'directional_signal'
        else:
            signal_case = 'weak_signal'

        return {
            'instrument_type': instrument.instrument_type.value,
            'asset_class': instrument.asset_class.value,
            'primary_asset': primary_asset,
            'secondary_asset': None,
            'primary_asset_effect': effect,
            'secondary_asset_effect': 'unknown',
            'instrument_directional_effect': directional_effect,
            'instrument_bias_score': round(combined_score, 3),
            'confidence': round(min(support, 0.85), 3),
            'impacted_assets': [primary_asset] if directional_effect != 'neutral' and primary_asset else [],
            'regime_context': regime_info['regime'],
            'signal_case': signal_case,
        }


# Convenience function
def analyze_news_for_instrument(
    text: str,
    instrument: InstrumentDescriptor | str,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Analyze news text for a given instrument.

    Args:
        text: The news text to analyze
        instrument: InstrumentDescriptor or raw symbol string
        **kwargs: Additional relevance scores (base_relevance, quote_relevance, macro_relevance)

    Returns:
        Instrument-aware directional analysis
    """
    if isinstance(instrument, str):
        instrument = InstrumentClassifier.classify(instrument)

    return InstrumentAwareNewsAnalyzer.analyze(text, instrument, **kwargs)
