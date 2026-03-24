"""
Provider Symbol Adapters - Generic Multi-Asset Symbol Normalization

This module provides adapters to convert between canonical InstrumentDescriptor
and provider-specific symbol formats for different data providers (Yahoo Finance,
MetaApi, NewsAPI, etc.).

Each adapter must explicitly declare what it can resolve and what it cannot.
Fallbacks are traced but never silent.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from app.services.market.instrument import (
    AssetClass,
    InstrumentDescriptor,
    InstrumentClassifier,
)

logger = logging.getLogger(__name__)


class SymbolResolutionResult:
    """Result of a symbol resolution attempt with explicit status."""

    def __init__(
        self,
        success: bool,
        provider_symbol: str | None = None,
        canonical_symbol: str | None = None,
        asset_class: AssetClass | None = None,
        reason: str | None = None,
        fallback_used: bool = False,
        resolution_path: list[str] | None = None,
    ) -> None:
        self.success = success
        self.provider_symbol = provider_symbol
        self.canonical_symbol = canonical_symbol
        self.asset_class = asset_class
        self.reason = reason
        self.fallback_used = fallback_used
        self.resolution_path = resolution_path or []

    def to_dict(self) -> dict[str, Any]:
        return {
            'success': self.success,
            'provider_symbol': self.provider_symbol,
            'canonical_symbol': self.canonical_symbol,
            'asset_class': self.asset_class.value if self.asset_class else None,
            'reason': self.reason,
            'fallback_used': self.fallback_used,
            'resolution_path': self.resolution_path,
        }


class ProviderSymbolAdapter(ABC):
    """
    Abstract base for provider symbol adapters.

    Each adapter knows how to convert between the canonical instrument model
    and a specific provider's symbol format.
    """

    provider_name: str = "unknown"

    @abstractmethod
    def to_provider_symbol(self, instrument: InstrumentDescriptor) -> SymbolResolutionResult:
        """
        Convert a canonical InstrumentDescriptor to a provider-specific symbol.

        Returns a SymbolResolutionResult with explicit success/failure and
        the resolved symbol or reason for failure.
        """
        pass

    @abstractmethod
    def from_provider_symbol(self, provider_symbol: str) -> SymbolResolutionResult:
        """
        Convert a provider-specific symbol to a canonical InstrumentDescriptor.

        Returns a SymbolResolutionResult with explicit success/failure and
        the resolved instrument or reason for failure.
        """
        pass

    def _trace_resolution(
        self,
        method: str,
        input_symbol: str,
        output_symbol: str | None,
        success: bool,
        reason: str | None = None,
    ) -> None:
        """Log symbol resolution attempts for traceability."""
        status = "RESOLVED" if success else "FAILED"
        msg = f"[{self.provider_name}] {method}: {input_symbol} -> {output_symbol} [{status}]"
        if reason:
            msg += f" | Reason: {reason}"
        if success:
            logger.debug(msg)
        else:
            logger.warning(msg)


class YFinanceSymbolAdapter(ProviderSymbolAdapter):
    """
    Yahoo Finance symbol adapter.

    YFinance symbol conventions:
    - FX pairs: EURUSD=X (with =X suffix)
    - Crypto: BTC-USD (with hyphen separator)
    - Indices: ^GSPC, ^NDX, etc.
    - Metals: GC=F (gold future), SI=F (silver)
    - Energy: CL=F (crude oil)
    - Equities: AAPL, TSLA, etc.
    """

    provider_name = "yfinance"

    # Known index mappings for YFinance
    INDEX_MAPPINGS: dict[str, str] = {
        'SPX500': '^GSPC',
        'US500': '^GSPC',
        'NSDQ100': '^NDX',
        'NAS100': '^NDX',
        'US30': '^DJI',
        'DJI30': '^DJI',
        'GER40': '^GDAXI',
        'DE40': '^GDAXI',
        'UK100': '^FTSE',
        'FRA40': '^FCHI',
        'JP225': '^N225',
        'NIKKEI225': '^N225',
    }

    # FX news fallback symbols by currency
    FX_NEWS_FALLBACK: dict[str, list[str]] = {
        'USD': ['DX-Y.NYB', '^DXY', 'UUP'],
        'EUR': ['FXE'],
        'GBP': ['FXB'],
        'JPY': ['FXY'],
        'CHF': ['FXF'],
        'CAD': ['FXC'],
        'AUD': ['FXA'],
        'NZD': ['BNZL'],
    }

    # Metal symbols in YFinance format
    METAL_YF_SYMBOLS: dict[str, str] = {
        'XAU': 'GC=F',
        'XAG': 'SI=F',
    }

    def to_provider_symbol(self, instrument: InstrumentDescriptor) -> SymbolResolutionResult:
        """Convert canonical instrument to YFinance symbol."""
        resolution_path = [f"canonical:{instrument.canonical_symbol}"]

        # FX pairs: add =X suffix
        if instrument.asset_class == AssetClass.FOREX and instrument.instrument_type.value == "fx_pair":
            yf_symbol = f"{instrument.canonical_symbol}=X"
            resolution_path.append(f"fx_rule:{yf_symbol}")
            self._trace_resolution("to_provider", instrument.canonical_symbol, yf_symbol, True)
            return SymbolResolutionResult(
                success=True,
                provider_symbol=yf_symbol,
                canonical_symbol=instrument.canonical_symbol,
                asset_class=instrument.asset_class,
                resolution_path=resolution_path,
            )

        # Crypto pairs: use hyphen separator (BTC-USD)
        if instrument.asset_class == AssetClass.CRYPTO:
            base = instrument.base_asset or ''
            quote = instrument.quote_asset or 'USD'
            yf_symbol = f"{base}-{quote}"
            resolution_path.append(f"crypto_rule:{yf_symbol}")
            self._trace_resolution("to_provider", instrument.canonical_symbol, yf_symbol, True)
            return SymbolResolutionResult(
                success=True,
                provider_symbol=yf_symbol,
                canonical_symbol=instrument.canonical_symbol,
                asset_class=instrument.asset_class,
                resolution_path=resolution_path,
            )

        # Indices: use caret prefix
        if instrument.asset_class == AssetClass.INDEX:
            yf_symbol = self.INDEX_MAPPINGS.get(instrument.canonical_symbol.upper(), instrument.canonical_symbol)
            if not yf_symbol.startswith('^'):
                yf_symbol = f"^{yf_symbol}"
            resolution_path.append(f"index_rule:{yf_symbol}")
            self._trace_resolution("to_provider", instrument.canonical_symbol, yf_symbol, True)
            return SymbolResolutionResult(
                success=True,
                provider_symbol=yf_symbol,
                canonical_symbol=instrument.canonical_symbol,
                asset_class=instrument.asset_class,
                resolution_path=resolution_path,
            )

        # Metals: use futures format (GC=F, SI=F)
        if instrument.asset_class == AssetClass.METAL:
            base = instrument.base_asset or 'XAU'
            yf_symbol = self.METAL_YF_SYMBOLS.get(base, f"{base}=X")
            resolution_path.append(f"metal_rule:{yf_symbol}")
            self._trace_resolution("to_provider", instrument.canonical_symbol, yf_symbol, True)
            return SymbolResolutionResult(
                success=True,
                provider_symbol=yf_symbol,
                canonical_symbol=instrument.canonical_symbol,
                asset_class=instrument.asset_class,
                resolution_path=resolution_path,
            )

        # Energy commodities: use futures format
        if instrument.asset_class == AssetClass.ENERGY:
            yf_symbol = instrument.canonical_symbol
            if not any(instrument.canonical_symbol.endswith(suffix) for suffix in ['=F', '-F']):
                # Try to map known energy symbols
                energy_map = {'CL': 'CL=F', 'BZ': 'BZ=F', 'NG': 'NG=F'}
                yf_symbol = energy_map.get(instrument.canonical_symbol, instrument.canonical_symbol)
            resolution_path.append(f"energy_rule:{yf_symbol}")
            self._trace_resolution("to_provider", instrument.canonical_symbol, yf_symbol, True)
            return SymbolResolutionResult(
                success=True,
                provider_symbol=yf_symbol,
                canonical_symbol=instrument.canonical_symbol,
                asset_class=instrument.asset_class,
                resolution_path=resolution_path,
            )

        # Equities and ETFs: use as-is
        if instrument.asset_class in (AssetClass.EQUITY, AssetClass.ETF):
            yf_symbol = instrument.canonical_symbol
            resolution_path.append(f"equity_etf_rule:{yf_symbol}")
            self._trace_resolution("to_provider", instrument.canonical_symbol, yf_symbol, True)
            return SymbolResolutionResult(
                success=True,
                provider_symbol=yf_symbol,
                canonical_symbol=instrument.canonical_symbol,
                asset_class=instrument.asset_class,
                resolution_path=resolution_path,
            )

        # Unknown: return as-is with warning
        resolution_path.append(f"unknown_rule:{instrument.canonical_symbol}")
        self._trace_resolution(
            "to_provider",
            instrument.canonical_symbol,
            instrument.canonical_symbol,
            True,
            reason="unknown_asset_class",
        )
        return SymbolResolutionResult(
            success=True,
            provider_symbol=instrument.canonical_symbol,
            canonical_symbol=instrument.canonical_symbol,
            asset_class=instrument.asset_class,
            reason="unknown_asset_class_used_as_is",
            resolution_path=resolution_path,
        )

    def from_provider_symbol(self, provider_symbol: str) -> SymbolResolutionResult:
        """Convert YFinance symbol to canonical instrument."""
        resolution_path = [f"yf_symbol:{provider_symbol}"]

        # Remove common YFinance suffixes
        cleaned = provider_symbol
        original = provider_symbol

        # Handle =X suffix for FX
        is_fx_format = cleaned.endswith('=X')
        if is_fx_format:
            cleaned = cleaned[:-2]
            resolution_path.append(f"fx_suffix_removed:{cleaned}")

        # Handle - separator for crypto
        is_crypto_format = '-' in cleaned and not cleaned.startswith('^')
        if is_crypto_format:
            parts = cleaned.split('-')
            if len(parts) == 2:
                resolution_path.append(f"crypto_format_detected:{parts}")

        # Handle ^ prefix for indices
        is_index_format = cleaned.startswith('^')
        if is_index_format:
            resolution_path.append(f"index_prefix_detected:{cleaned}")

        # Handle .F suffix for futures
        is_future_format = '.F' in cleaned
        if is_future_format:
            resolution_path.append(f"future_format_detected:{cleaned}")

        # Now classify the cleaned symbol
        instrument = InstrumentClassifier.classify(cleaned)

        # For indices, also check the index alias map
        if instrument.asset_class == AssetClass.UNKNOWN and original.upper() in self.INDEX_MAPPINGS:
            canonical = self.INDEX_MAPPINGS[original.upper()]
            instrument = InstrumentClassifier.classify(canonical)
            resolution_path.append(f"index_alias_resolved:{canonical}")

        # Build result
        success = instrument.asset_class != AssetClass.UNKNOWN
        reason = None if success else f"Could not classify symbol: {cleaned}"

        self._trace_resolution(
            "from_provider",
            original,
            instrument.canonical_symbol,
            success,
            reason,
        )

        return SymbolResolutionResult(
            success=success,
            provider_symbol=original,
            canonical_symbol=instrument.canonical_symbol,
            asset_class=instrument.asset_class,
            reason=reason,
            resolution_path=resolution_path,
        )

    def get_news_symbol_candidates(
        self,
        instrument: InstrumentDescriptor,
    ) -> list[dict[str, Any]]:
        """
        Get candidate symbols for news retrieval for a given instrument.

        For FX pairs, also returns currency-focused news symbols.
        For crypto, also returns BTC/ETH fallbacks.
        For indices, returns index + sector proxies.

        Returns a list of dicts with 'symbol' and 'asset_class' keys.
        """
        candidates: list[dict[str, Any]] = []

        # Primary symbol
        primary_result = self.to_provider_symbol(instrument)
        if primary_result.success:
            candidates.append({
                'symbol': primary_result.provider_symbol,
                'asset_class': instrument.asset_class.value,
                'type': 'primary',
            })

        # FX pair: add currency-focused fallbacks
        if instrument.asset_class == AssetClass.FOREX:
            base = instrument.base_asset
            quote = instrument.quote_asset
            if base in self.FX_NEWS_FALLBACK:
                for fallback in self.FX_NEWS_FALLBACK[base]:
                    candidates.append({
                        'symbol': fallback,
                        'asset_class': 'fx_currency',
                        'type': 'currency_fallback',
                        'currency': base,
                    })
            if quote in self.FX_NEWS_FALLBACK:
                for fallback in self.FX_NEWS_FALLBACK[quote]:
                    if not any(c['symbol'] == fallback for c in candidates):
                        candidates.append({
                            'symbol': fallback,
                            'asset_class': 'fx_currency',
                            'type': 'currency_fallback',
                            'currency': quote,
                        })

        # Crypto: add BTC/ETH fallbacks for sector news
        if instrument.asset_class == AssetClass.CRYPTO:
            base = instrument.base_asset
            # Don't add self as fallback
            if base not in ('BTC', 'ETH'):
                candidates.append({
                    'symbol': 'BTC-USD',
                    'asset_class': 'crypto_sector',
                    'type': 'sector_fallback',
                })
                candidates.append({
                    'symbol': 'ETH-USD',
                    'asset_class': 'crypto_sector',
                    'type': 'sector_fallback',
                })

        # Index: add macro proxies
        if instrument.asset_class == AssetClass.INDEX:
            candidates.append({
                'symbol': '^GSPC',
                'asset_class': 'index_macro',
                'type': 'macro_proxy',
            })
            candidates.append({
                'symbol': '^VIX',
                'asset_class': 'volatility',
                'type': 'volatility_proxy',
            })

        return candidates


class MetaApiSymbolAdapter(ProviderSymbolAdapter):
    """
    MetaApi (MT4/MT5/cTrader) symbol adapter.

    MetaApi conventions:
    - FX pairs: EURUSD (no suffix, no separator)
    - Crypto: BTCUSD (no separator)
    - Indices: May have prefix like GER40, NAS100
    - Metals: XAUUSD, XAGUSD
    - CFD suffix: .PRO
    """

    provider_name = "metaapi"

    def to_provider_symbol(self, instrument: InstrumentDescriptor) -> SymbolResolutionResult:
        """Convert canonical instrument to MetaApi symbol."""
        resolution_path = [f"canonical:{instrument.canonical_symbol}"]

        # FX pairs: use canonical (EURUSD)
        if instrument.asset_class == AssetClass.FOREX:
            meta_symbol = instrument.canonical_symbol
            if instrument.is_cfd:
                meta_symbol = f"{meta_symbol}.PRO"
            resolution_path.append(f"fx_rule:{meta_symbol}")
            self._trace_resolution("to_provider", instrument.canonical_symbol, meta_symbol, True)
            return SymbolResolutionResult(
                success=True,
                provider_symbol=meta_symbol,
                canonical_symbol=instrument.canonical_symbol,
                asset_class=instrument.asset_class,
                resolution_path=resolution_path,
            )

        # Crypto: BTCUSD format
        if instrument.asset_class == AssetClass.CRYPTO:
            base = instrument.base_asset or ''
            quote = instrument.quote_asset or 'USD'
            meta_symbol = f"{base}{quote}"
            resolution_path.append(f"crypto_rule:{meta_symbol}")
            self._trace_resolution("to_provider", instrument.canonical_symbol, meta_symbol, True)
            return SymbolResolutionResult(
                success=True,
                provider_symbol=meta_symbol,
                canonical_symbol=instrument.canonical_symbol,
                asset_class=instrument.asset_class,
                resolution_path=resolution_path,
            )

        # Metals: XAUUSD format
        if instrument.asset_class == AssetClass.METAL:
            base = instrument.base_asset or 'XAU'
            meta_symbol = f"{base}USD"
            resolution_path.append(f"metal_rule:{meta_symbol}")
            self._trace_resolution("to_provider", instrument.canonical_symbol, meta_symbol, True)
            return SymbolResolutionResult(
                success=True,
                provider_symbol=meta_symbol,
                canonical_symbol=instrument.canonical_symbol,
                asset_class=instrument.asset_class,
                resolution_path=resolution_path,
            )

        # Indices: may need mapping
        if instrument.asset_class == AssetClass.INDEX:
            # MetaApi uses names like GER40, NAS100
            index_meta_map = {
                '^GSPC': 'US500',
                'SPX500': 'US500',
                'US500': 'US500',
                '^NDX': 'NAS100',
                'NSDQ100': 'NAS100',
                'NAS100': 'NAS100',
                '^DJI': 'US30',
                'US30': 'US30',
                'DJI30': 'US30',
                '^GDAXI': 'GER40',
                'GER40': 'GER40',
                'DE40': 'GER40',
                '^FTSE': 'UK100',
                'UK100': 'UK100',
                '^FCHI': 'FRA40',
                'FRA40': 'FRA40',
                '^N225': 'JP225',
                'JP225': 'JP225',
                'NIKKEI225': 'JP225',
            }
            meta_symbol = index_meta_map.get(instrument.canonical_symbol, instrument.canonical_symbol)
            if instrument.is_cfd:
                meta_symbol = f"{meta_symbol}.PRO"
            resolution_path.append(f"index_rule:{meta_symbol}")
            self._trace_resolution("to_provider", instrument.canonical_symbol, meta_symbol, True)
            return SymbolResolutionResult(
                success=True,
                provider_symbol=meta_symbol,
                canonical_symbol=instrument.canonical_symbol,
                asset_class=instrument.asset_class,
                resolution_path=resolution_path,
            )

        # Equities: use as-is with CFD suffix if applicable
        if instrument.asset_class == AssetClass.EQUITY:
            meta_symbol = instrument.canonical_symbol
            if instrument.is_cfd:
                meta_symbol = f"{meta_symbol}.PRO"
            resolution_path.append(f"equity_rule:{meta_symbol}")
            self._trace_resolution("to_provider", instrument.canonical_symbol, meta_symbol, True)
            return SymbolResolutionResult(
                success=True,
                provider_symbol=meta_symbol,
                canonical_symbol=instrument.canonical_symbol,
                asset_class=instrument.asset_class,
                resolution_path=resolution_path,
            )

        # Unknown
        self._trace_resolution(
            "to_provider",
            instrument.canonical_symbol,
            instrument.canonical_symbol,
            True,
            reason="unknown_asset_class",
        )
        return SymbolResolutionResult(
            success=True,
            provider_symbol=instrument.canonical_symbol,
            canonical_symbol=instrument.canonical_symbol,
            asset_class=instrument.asset_class,
            reason="unknown_asset_class",
            resolution_path=resolution_path,
        )

    def from_provider_symbol(self, provider_symbol: str) -> SymbolResolutionResult:
        """Convert MetaApi symbol to canonical instrument."""
        resolution_path = [f"meta_symbol:{provider_symbol}"]

        # Remove CFD suffix
        cleaned = re.sub(r'\.PRO$', '', provider_symbol, flags=re.IGNORECASE)
        if cleaned != provider_symbol:
            resolution_path.append(f"cfd_suffix_removed:{cleaned}")

        # Remove common separators
        no_sep = cleaned.replace('/', '').replace('-', '')
        if no_sep != cleaned:
            resolution_path.append(f"separator_removed:{no_sep}")

        # Classify
        instrument = InstrumentClassifier.classify(cleaned)

        success = instrument.asset_class != AssetClass.UNKNOWN
        reason = None if success else f"Could not classify MetaApi symbol: {cleaned}"

        self._trace_resolution(
            "from_provider",
            provider_symbol,
            instrument.canonical_symbol,
            success,
            reason,
        )

        return SymbolResolutionResult(
            success=success,
            provider_symbol=provider_symbol,
            canonical_symbol=instrument.canonical_symbol,
            asset_class=instrument.asset_class,
            reason=reason,
            resolution_path=resolution_path,
        )


class NewsApiSymbolAdapter(ProviderSymbolAdapter):
    """
    NewsAPI symbol adapter for news retrieval.

    NewsAPI typically uses natural language search rather than symbols,
    but we can map instruments to relevant search queries and ticker symbols.
    """

    provider_name = "newsapi"

    def to_provider_symbol(self, instrument: InstrumentDescriptor) -> SymbolResolutionResult:
        """For NewsAPI, we return a search query rather than a symbol."""
        resolution_path = [f"canonical:{instrument.canonical_symbol}"]

        # Build search query based on instrument
        if instrument.asset_class == AssetClass.FOREX:
            # For FX, search by currency pair
            query = f"{instrument.base_asset}/{instrument.quote_asset} forex"
        elif instrument.asset_class == AssetClass.CRYPTO:
            # For crypto, search by crypto name
            query = f"{instrument.base_asset} cryptocurrency"
        elif instrument.asset_class == AssetClass.INDEX:
            query = f"{instrument.display_symbol} stock market"
        elif instrument.asset_class == AssetClass.EQUITY:
            query = f"{instrument.canonical_symbol} stock"
        elif instrument.asset_class == AssetClass.METAL:
            base = instrument.base_asset or 'gold'
            query = f"{base} precious metals commodity"
        elif instrument.asset_class == AssetClass.ENERGY:
            query = f"{instrument.display_symbol} energy commodity"
        else:
            query = instrument.canonical_symbol

        resolution_path.append(f"news_query:{query}")
        self._trace_resolution("to_provider", instrument.canonical_symbol, query, True)

        return SymbolResolutionResult(
            success=True,
            provider_symbol=query,  # NewsAPI uses query string
            canonical_symbol=instrument.canonical_symbol,
            asset_class=instrument.asset_class,
            resolution_path=resolution_path,
        )

    def from_provider_symbol(self, provider_symbol: str) -> SymbolResolutionResult:
        """Convert a news search term to an instrument."""
        resolution_path = [f"news_query:{provider_symbol}"]

        # Try to classify the query
        instrument = InstrumentClassifier.classify(provider_symbol)

        success = instrument.asset_class != AssetClass.UNKNOWN
        reason = None if success else f"Could not classify news query: {provider_symbol}"

        self._trace_resolution(
            "from_provider",
            provider_symbol,
            instrument.canonical_symbol,
            success,
            reason,
        )

        return SymbolResolutionResult(
            success=success,
            provider_symbol=provider_symbol,
            canonical_symbol=instrument.canonical_symbol,
            asset_class=instrument.asset_class,
            reason=reason,
            resolution_path=resolution_path,
        )


# Registry of available adapters
PROVIDER_ADAPTERS: dict[str, ProviderSymbolAdapter] = {
    'yfinance': YFinanceSymbolAdapter(),
    'metaapi': MetaApiSymbolAdapter(),
    'newsapi': NewsApiSymbolAdapter(),
}


def get_provider_adapter(provider_name: str) -> ProviderSymbolAdapter | None:
    """Get the adapter for a specific provider."""
    return PROVIDER_ADAPTERS.get(provider_name.lower())


def resolve_symbol_for_provider(
    raw_symbol: str | None,
    provider: str,
    instrument: InstrumentDescriptor | None = None,
) -> SymbolResolutionResult:
    """
    Resolve a symbol for a specific provider.

    If an instrument is provided, uses to_provider_symbol.
    Otherwise, first classifies the raw_symbol then converts.
    """
    adapter = get_provider_adapter(provider)
    if not adapter:
        return SymbolResolutionResult(
            success=False,
            reason=f"Unknown provider: {provider}",
        )

    if instrument is not None:
        return adapter.to_provider_symbol(instrument)

    if raw_symbol is None:
        return SymbolResolutionResult(
            success=False,
            reason="No symbol or instrument provided",
        )

    # First classify, then convert
    instr = InstrumentClassifier.classify(raw_symbol)
    return adapter.to_provider_symbol(instr)


def get_news_candidates_for_instrument(
    instrument: InstrumentDescriptor,
    provider: str = 'yfinance',
) -> list[dict[str, Any]]:
    """Get news symbol candidates for an instrument and provider."""
    adapter = get_provider_adapter(provider)
    if not adapter or not hasattr(adapter, 'get_news_symbol_candidates'):
        return [{'symbol': instrument.canonical_symbol, 'asset_class': instrument.asset_class.value, 'type': 'default'}]

    return adapter.get_news_symbol_candidates(instrument)
