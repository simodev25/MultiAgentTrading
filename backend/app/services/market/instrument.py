"""
Canonical Instrument Model - Generic Multi-Asset Abstraction

This module provides a unified InstrumentDescriptor that represents any tradeable
instrument regardless of asset class (forex, crypto, index, equity, metal, energy, etc.)
and provider-specific symbol format.

Architecture principles:
- The system reasons first about 'instrument', then applies asset-class-specific rules
- base_asset/quote_asset only exist when semantically meaningful (FX pairs, crypto pairs)
- reference_asset captures the underlying for indices, equities, ETFs, commodities
- provider_symbols maps to each data provider's expected format
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AssetClass(str, Enum):
    """Supported asset classes in the trading platform."""
    FOREX = "forex"
    CRYPTO = "crypto"
    INDEX = "index"
    EQUITY = "equity"
    ETF = "etf"
    METAL = "metal"
    ENERGY = "energy"
    COMMODITY = "commodity"
    FUTURE = "future"
    CFD = "cfd"
    UNKNOWN = "unknown"


class InstrumentType(str, Enum):
    """Detailed instrument type for asset-class-specific logic."""
    FX_PAIR = "fx_pair"
    CRYPTO_PAIR = "crypto_pair"
    SPOT_CRYPTO = "spot_crypto"
    INDEX_CASH = "index_cash"
    INDEX_CFD = "index_cfd"
    EQUITY_SPOT = "equity_spot"
    EQUITY_CFD = "equity_cfd"
    ETF_SPOT = "etf_spot"
    METAL_SPOT = "metal_spot"
    METAL_CFD = "metal_cfd"
    ENERGY_FUTURE = "energy_future"
    COMMODITY_FUTURE = "commodity_future"
    CFD_GENERIC = "cfd_generic"
    GENERIC_SYMBOL = "generic_symbol"


# Known index symbols for classification
INDEX_SYMBOLS: set[str] = {
    '^GSPC', '^NDX', '^DJI', '^GDAXI', '^FTSE', '^FCHI',
    '^N225', '^VIX', 'SPX500', 'US500', 'NSDQ100', 'NAS100',
    'US30', 'DJI30', 'GER40', 'DE40', 'UK100', 'FRA40',
    'JP225', 'NIKKEI225',
}

# Known metal symbols
METAL_SYMBOLS: set[str] = {'XAU', 'XAG', 'GC=F', 'SI=F', 'PL=F'}

# Known energy symbols
ENERGY_SYMBOLS: set[str] = {
    'CL=F', 'BZ=F', 'NG=F', 'HO=F', 'RB=F',  # YFinance format
    'CL', 'BZ', 'NG', 'HO', 'RB',  # Standalone symbols
    'CRUDE', 'BRENT', 'WTI',  # Alternative names
}

# Fiat currencies for FX pair detection
FIAT_CURRENCIES: set[str] = {'USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD'}

# Crypto assets
CRYPTO_ASSETS: set[str] = {
    'BTC', 'ETH', 'ADA', 'AVAX', 'BCH', 'BNB', 'DOGE', 'DOT',
    'LINK', 'LTC', 'MATIC', 'SOL', 'UNI', 'XRP',
}

# Crypto quote assets
CRYPTO_QUOTES: tuple[str, ...] = ('USDT', 'USDC', 'USD', 'BTC', 'ETH')


@dataclass(frozen=True)
class InstrumentDescriptor:
    """
    Canonical representation of a tradeable instrument.

    This object is the single source of truth for instrument properties across
    all agents and services. It normalizes provider-specific symbols into a
    consistent internal format.

    Attributes:
        raw_symbol: The original symbol as provided by the user/provider
        canonical_symbol: Uppercase normalized symbol (e.g., "EURUSD", "BTCUSD")
        display_symbol: Human-readable display form (e.g., "EUR/USD", "BTC/USD")
        asset_class: High-level asset class (forex, crypto, index, equity, etc.)
        instrument_type: Detailed type for asset-class-specific logic
        provider_symbols: Mapping of provider name to provider-specific symbol format
        base_asset: Primary asset (e.g., EUR in EURUSD) - only meaningful for pairs
        quote_asset: Quote asset (e.g., USD in EURUSD) - only meaningful for pairs
        reference_asset: Underlying reference (e.g., ^GSPC for US500) - for derivatives/indices
        venue: Exchange or venue where the instrument trades (e.g., "NYSE", "CME")
        is_cfd: Whether this is a CFD instrument
        has_base_quote: Whether this instrument has a meaningful base/quote structure
    """
    raw_symbol: str
    canonical_symbol: str
    display_symbol: str
    asset_class: AssetClass
    instrument_type: InstrumentType
    market: str | None = None
    provider: str | None = None
    provider_symbol: str | None = None
    provider_symbols: dict[str, str] = field(default_factory=dict)
    base_asset: str | None = None
    quote_asset: str | None = None
    reference_asset: str | None = None
    venue: str | None = None
    is_cfd: bool = False
    has_base_quote: bool = False
    classification_trace: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Validate base/quote only when has_base_quote is True
        if self.has_base_quote:
            assert self.base_asset is not None, "base_asset required when has_base_quote=True"
            assert self.quote_asset is not None, "quote_asset required when has_base_quote=True"

    def is_fx_like(self) -> bool:
        """Check if this is an FX-like pair instrument."""
        return self.asset_class == AssetClass.FOREX and self.instrument_type == InstrumentType.FX_PAIR

    def is_crypto_like(self) -> bool:
        """Check if this is a crypto pair."""
        return self.asset_class == AssetClass.CRYPTO and self.instrument_type in (
            InstrumentType.CRYPTO_PAIR,
            InstrumentType.SPOT_CRYPTO,
        )

    def is_index_like(self) -> bool:
        """Check if this is an index instrument."""
        return self.asset_class == AssetClass.INDEX or self.instrument_type in (
            InstrumentType.INDEX_CASH,
            InstrumentType.INDEX_CFD,
        )

    def is_equity_like(self) -> bool:
        """Check if this is an equity instrument."""
        return self.asset_class == AssetClass.EQUITY or self.instrument_type in (
            InstrumentType.EQUITY_SPOT,
            InstrumentType.EQUITY_CFD,
        )

    def is_metal_like(self) -> bool:
        """Check if this is a metal (gold/silver) instrument."""
        return self.asset_class == AssetClass.METAL or self.instrument_type in (
            InstrumentType.METAL_SPOT,
            InstrumentType.METAL_CFD,
        )

    def is_energy_like(self) -> bool:
        """Check if this is an energy future/commodity."""
        return self.asset_class == AssetClass.ENERGY or self.instrument_type in (
            InstrumentType.ENERGY_FUTURE,
            InstrumentType.COMMODITY_FUTURE,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON output."""
        return {
            'raw_symbol': self.raw_symbol,
            'canonical_symbol': self.canonical_symbol,
            'display_symbol': self.display_symbol,
            'asset_class': self.asset_class.value,
            'instrument_type': self.instrument_type.value,
            'market': self.market,
            'provider': self.provider,
            'provider_symbol': self.provider_symbol,
            'provider_symbols': dict(self.provider_symbols),
            'base_asset': self.base_asset,
            'quote_asset': self.quote_asset,
            'reference_asset': self.reference_asset,
            'venue': self.venue,
            'is_cfd': self.is_cfd,
            'has_base_quote': self.has_base_quote,
            'classification_trace': list(self.classification_trace),
        }


class InstrumentClassifier:
    """
    Classifies raw symbols into asset classes and instrument types.

    This is the single entry point for symbol classification. It applies
    heuristics and pattern matching to determine the appropriate InstrumentDescriptor
    properties without requiring external data provider lookups.
    """

    @staticmethod
    def classify(raw_symbol: str | None) -> InstrumentDescriptor:
        """
        Classify a raw symbol into a canonical InstrumentDescriptor.

        Args:
            raw_symbol: The symbol as provided (e.g., "EURUSD.PRO", "BTC-USD", "^GSPC")

        Returns:
            InstrumentDescriptor with all properties populated
        """
        if not raw_symbol:
            return InstrumentClassifier._unknown(str(raw_symbol or ''))

        symbol = str(raw_symbol).strip()
        upper_symbol = symbol.upper()

        # Check for CFD suffix
        is_cfd = '.PRO' in upper_symbol or upper_symbol.endswith('CFD')

        # Try classification in order of specificity
        # 1. Check if it's a known index
        if upper_symbol in INDEX_SYMBOLS or InstrumentClassifier._matches_index_pattern(upper_symbol):
            return InstrumentClassifier._classify_index(symbol, upper_symbol, is_cfd)

        # 2. Check if it's a metal
        if upper_symbol in METAL_SYMBOLS or any(symbol.upper().startswith(m) for m in METAL_SYMBOLS):
            return InstrumentClassifier._classify_metal(symbol, upper_symbol, is_cfd)

        # 3. Check if it's an energy commodity
        if upper_symbol in ENERGY_SYMBOLS or any(symbol.upper().startswith(m) for m in ENERGY_SYMBOLS):
            return InstrumentClassifier._classify_energy(symbol, upper_symbol, is_cfd)

        # 4. Check if it's a crypto pair (BTCUSD, ETH-USD, etc.)
        if InstrumentClassifier._is_crypto_pair(upper_symbol):
            return InstrumentClassifier._classify_crypto(symbol, upper_symbol, is_cfd)

        # 5. Check if it's an FX pair (EURUSD, GBPJPY, etc.)
        if InstrumentClassifier._is_fx_pair(upper_symbol):
            return InstrumentClassifier._classify_fx(symbol, upper_symbol, is_cfd)

        # 6. Check if it's an ETF (before equity - SPY, QQQ are ETFs, not equities)
        if InstrumentClassifier._is_etf(upper_symbol):
            return InstrumentClassifier._classify_etf(symbol, upper_symbol)

        # 7. Check if it's an equity (single letter code like AAPL, or known equity)
        if InstrumentClassifier._is_equity(symbol, upper_symbol):
            return InstrumentClassifier._classify_equity(symbol, upper_symbol, is_cfd)

        # Default: unknown/generic
        return InstrumentClassifier._unknown(symbol)

    @staticmethod
    def _matches_index_pattern(symbol: str) -> bool:
        """Check if symbol matches common index patterns."""
        upper = symbol.upper()
        # First check exact known indices
        if upper in INDEX_SYMBOLS:
            return True
        # Common index patterns - must have caret prefix or specific multi-letter patterns
        # Exclude regular equity tickers (1-5 letters)
        if len(upper) <= 5:
            return False
        # Then check patterns only for longer symbols
        patterns = [
            r'^GSPC$', r'^NDX$', r'^DJI$', r'^VIX$',  # Single letter with caret
            r'^[A-Z]{2}40$',  # GER40, FRA40 (but not AAPL, TSLA, etc.)
            r'^[A-Z]{2}\d{2}$',  # DE40, UK100 (but not AAPL)
            r'^US\d{3}$',  # US500, US30
            r'^NAS100$', r'^NSDQ100$',
        ]
        return any(re.match(p, upper) for p in patterns)

    @staticmethod
    def _is_crypto_pair(symbol: str) -> bool:
        """Detect crypto pair by pattern (BASEQUOTE where BASE is crypto asset)."""
        if len(symbol) < 6:
            return False

        # Remove common separators AND suffixes
        cleaned = symbol.replace('-', '').replace('/', '').replace('.PRO', '').upper()

        # Check if it ends with a known crypto quote
        for quote in sorted(CRYPTO_QUOTES, key=len, reverse=True):
            if cleaned.endswith(quote) and len(cleaned) > len(quote):
                base = cleaned[:-len(quote)]
                if base in CRYPTO_ASSETS:
                    return True

        # Check if it matches BTCUSD pattern (6 chars, all letters)
        if len(cleaned) == 6 and cleaned.isalpha():
            if cleaned[:3] in CRYPTO_ASSETS and cleaned[3:] in ('USD', 'USDT', 'USDC'):
                return True

        return False

    @staticmethod
    def _is_fx_pair(symbol: str) -> bool:
        """Detect FX pair by pattern (6 uppercase letters, first 3 and last 3 are currencies)."""
        cleaned = symbol.upper().replace('/', '').replace('-', '').replace('.PRO', '')

        # Must be exactly 6 alphabetic characters
        if len(cleaned) != 6 or not cleaned.isalpha():
            return False

        base = cleaned[:3]
        quote = cleaned[3:]

        # Both must be fiat currencies
        return base in FIAT_CURRENCIES and quote in FIAT_CURRENCIES

    @staticmethod
    def _is_equity(symbol: str, upper: str) -> bool:
        """Detect equity by pattern (1-5 uppercase letters, not matching FX/crypto/energy)."""
        # Remove common suffixes
        cleaned = re.sub(r'(\.PRO|CFD)$', '', upper, flags=re.IGNORECASE)
        cleaned = re.sub(r'\.(EX|OB|PM|IO|NMS|CME|GLOB|ARCA|PAC|NYSE|NASDAQ)$', '', cleaned, flags=re.IGNORECASE)

        # Single letter or 2-5 letters, all alpha
        if len(cleaned) <= 5 and len(cleaned) >= 1 and cleaned.isalpha():
            # Not a currency pair (6 letters)
            if len(cleaned) == 6:
                return False
            # Not a crypto asset
            if cleaned in CRYPTO_ASSETS:
                return False
            # Not an energy commodity symbol
            if cleaned in {'CL', 'BZ', 'NG', 'HO', 'RB'}:
                return False
            return True

        return False

    @staticmethod
    def _is_etf(symbol: str) -> bool:
        """Detect ETF by common suffixes/prefixes."""
        upper = re.sub(r'(\.PRO|CFD)$', '', symbol.upper(), flags=re.IGNORECASE)
        # Known single-ticker ETFs (these are NOT indices)
        known_etfs = {
            'SPY', 'QQQ', 'IWM', 'EFA', 'EEM', 'GLD', 'SLV', 'TLT', 'IAU',
            'XLK', 'XLE', 'XLV', 'XLF', 'XLU', 'XLP', 'XLY', 'XLI', 'XLB',
            'DIA', 'SSO', 'SDS', 'SPXL', 'SPXS', 'TQQQ', 'SQQQ', 'UVXY',
        }
        if upper in known_etfs:
            return True
        # LSE-listed ETFs with .LI suffix
        if upper.endswith('.LI'):
            return True
        return False

    @staticmethod
    def _classify_fx(raw: str, upper: str, is_cfd: bool) -> InstrumentDescriptor:
        """Classify as FX pair."""
        cleaned = upper.replace('/', '').replace('-', '').replace('.PRO', '')
        base = cleaned[:3]
        quote = cleaned[3:]
        canonical = f"{base}{quote}"
        display = f"{base}/{quote}"

        # Determine venue based on common conventions
        venue = "OTC"  # Will be updated based on .PRO suffix

        return InstrumentDescriptor(
            raw_symbol=raw,
            canonical_symbol=canonical,
            display_symbol=display,
            asset_class=AssetClass.FOREX,
            instrument_type=InstrumentType.FX_PAIR,
            market='broker_cfd' if is_cfd else 'otc',
            provider=None,
            provider_symbol=raw,
            provider_symbols=InstrumentClassifier._build_fx_provider_symbols(canonical, is_cfd),
            base_asset=base,
            quote_asset=quote,
            reference_asset=quote,  # For FX, reference is the quote currency
            venue=venue,
            is_cfd=is_cfd,
            has_base_quote=True,
            classification_trace=[
                f'raw:{raw}',
                f'normalized:{canonical}',
                f'asset_class:{AssetClass.FOREX.value}',
                f'instrument_type:{InstrumentType.FX_PAIR.value}',
                f'is_cfd:{is_cfd}',
            ],
        )

    @staticmethod
    def _classify_crypto(raw: str, upper: str, is_cfd: bool) -> InstrumentDescriptor:
        """Classify as crypto pair."""
        cleaned = upper.replace('-', '').replace('/', '').replace('.PRO', '')
        base, quote = None, None

        for q in sorted(CRYPTO_QUOTES, key=len, reverse=True):
            if cleaned.endswith(q) and len(cleaned) > len(q):
                base = cleaned[:-len(q)]
                quote = q
                break

        if not base or not quote:
            base = cleaned[:3] if len(cleaned) >= 6 else cleaned
            quote = 'USD'

        canonical = f"{base}{quote}"
        display = f"{base}/{quote}"

        return InstrumentDescriptor(
            raw_symbol=raw,
            canonical_symbol=canonical,
            display_symbol=display,
            asset_class=AssetClass.CRYPTO,
            instrument_type=InstrumentType.CRYPTO_PAIR,
            market='broker_cfd' if is_cfd else 'crypto_spot',
            provider=None,
            provider_symbol=raw,
            provider_symbols=InstrumentClassifier._build_crypto_provider_symbols(canonical, raw),
            base_asset=base,
            quote_asset=quote,
            reference_asset=base,  # Crypto pair: reference is the base (e.g., Bitcoin)
            venue="CRYPTO",
            is_cfd=is_cfd,
            has_base_quote=True,
            classification_trace=[
                f'raw:{raw}',
                f'normalized:{canonical}',
                f'asset_class:{AssetClass.CRYPTO.value}',
                f'instrument_type:{InstrumentType.CRYPTO_PAIR.value}',
                f'is_cfd:{is_cfd}',
            ],
        )

    @staticmethod
    def _classify_index(raw: str, upper: str, is_cfd: bool) -> InstrumentDescriptor:
        """Classify as index."""
        # Map common index names to their symbols
        index_map = {
            '^GSPC': ('SPX', 'S&P 500', 'CBOT'),
            '^NDX': ('NDX', 'NASDAQ 100', 'NASDAQ'),
            '^DJI': ('DJI', 'Dow Jones 30', 'NYSE'),
            '^GDAXI': ('DAX', 'DAX 40', 'XETRA'),
            '^FTSE': ('FTSE', 'FTSE 100', 'LSE'),
            '^FCHI': ('CAC', 'CAC 40', 'EURONEXT'),
            '^N225': ('NIKKEI', 'Nikkei 225', 'TSE'),
            '^VIX': ('VIX', 'VIX', 'CBOE'),
        }

        canonical_upper = upper.replace('.PRO', '')
        mapped = index_map.get(canonical_upper, (upper.replace('^', ''), upper, None))

        reference = mapped[0]
        display = mapped[1]
        venue = mapped[2] or ('CFD' if is_cfd else 'INDEX')

        return InstrumentDescriptor(
            raw_symbol=raw,
            canonical_symbol=canonical_upper,
            display_symbol=display,
            asset_class=AssetClass.INDEX,
            instrument_type=InstrumentType.INDEX_CFD if is_cfd else InstrumentType.INDEX_CASH,
            market='broker_cfd' if is_cfd else 'cash_index',
            provider=None,
            provider_symbol=raw,
            provider_symbols=InstrumentClassifier._build_index_provider_symbols(canonical_upper, raw, is_cfd),
            base_asset=None,  # Indices don't have base/quote
            quote_asset=None,
            reference_asset=canonical_upper,
            venue=venue,
            is_cfd=is_cfd,
            has_base_quote=False,
            classification_trace=[
                f'raw:{raw}',
                f'normalized:{canonical_upper}',
                f'asset_class:{AssetClass.INDEX.value}',
                f'instrument_type:{InstrumentType.INDEX_CFD.value if is_cfd else InstrumentType.INDEX_CASH.value}',
                f'is_cfd:{is_cfd}',
            ],
        )

    @staticmethod
    def _classify_metal(raw: str, upper: str, is_cfd: bool) -> InstrumentDescriptor:
        """Classify as metal (gold, silver, etc.)."""
        # Mapping from YFinance/canonical symbols to display info
        # Key is the recognized symbol, value is (canonical_base, display_name, venue)
        metal_info_map = {
            'XAU': ('XAU', 'Gold', 'COMEX'),
            'XAG': ('XAG', 'Silver', 'COMEX'),
            'GC=F': ('XAU', 'Gold', 'COMEX'),   # YFinance gold future
            'SI=F': ('XAG', 'Silver', 'COMEX'),  # YFinance silver future
        }

        # Find matching metal
        base = None
        for metal in METAL_SYMBOLS:
            if upper.startswith(metal):
                base = metal
                break

        if not base:
            base = 'XAU'  # Default to gold

        # Get canonical info
        canonical_info = metal_info_map.get(base, (base, base, 'COMEX'))
        canonical_base = canonical_info[0]
        display_name = canonical_info[1]
        venue = canonical_info[2]

        display = f"{display_name}/USD"

        return InstrumentDescriptor(
            raw_symbol=raw,
            canonical_symbol=f"{canonical_base}USD",
            display_symbol=display,
            asset_class=AssetClass.METAL,
            instrument_type=InstrumentType.METAL_SPOT if not is_cfd else InstrumentType.METAL_CFD,
            market='broker_cfd' if is_cfd else ('futures' if '=F' in upper else 'spot'),
            provider=None,
            provider_symbol=raw,
            provider_symbols=InstrumentClassifier._build_metal_provider_symbols(canonical_base),
            base_asset=canonical_base,  # Use canonical base (XAU/XAG), not the raw symbol
            quote_asset='USD',
            reference_asset=canonical_base,
            venue=venue,
            is_cfd=is_cfd,
            has_base_quote=True,  # Metals are quoted as XAU/USD
            classification_trace=[
                f'raw:{raw}',
                f'normalized:{canonical_base}USD',
                f'asset_class:{AssetClass.METAL.value}',
                f'instrument_type:{InstrumentType.METAL_CFD.value if is_cfd else InstrumentType.METAL_SPOT.value}',
                f'is_cfd:{is_cfd}',
            ],
        )

    @staticmethod
    def _classify_energy(raw: str, upper: str, is_cfd: bool) -> InstrumentDescriptor:
        """Classify as energy commodity/future."""
        energy_map = {
            'CL=F': ('CL', 'Crude Oil WTI', 'NYMEX'),
            'BZ=F': ('BZ', 'Brent Crude', 'ICE'),
            'NG=F': ('NG', 'Natural Gas', 'NYMEX'),
        }

        base = None
        for energy in ENERGY_SYMBOLS:
            if upper.startswith(energy):
                base = energy
                break

        if not base:
            base = 'CL=F'

        mapped = energy_map.get(base, (base, base, 'NYMEX'))
        display = f"{mapped[1]}/USD"

        return InstrumentDescriptor(
            raw_symbol=raw,
            canonical_symbol=base,
            display_symbol=display,
            asset_class=AssetClass.ENERGY,
            instrument_type=InstrumentType.ENERGY_FUTURE,
            market='broker_cfd' if is_cfd else 'futures',
            provider=None,
            provider_symbol=raw,
            provider_symbols={'yfinance': base},
            base_asset=None,
            quote_asset=None,
            reference_asset=mapped[0],
            venue=mapped[2],
            is_cfd=is_cfd,
            has_base_quote=False,  # Energy futures don't have traditional base/quote
            classification_trace=[
                f'raw:{raw}',
                f'normalized:{base}',
                f'asset_class:{AssetClass.ENERGY.value}',
                f'instrument_type:{InstrumentType.ENERGY_FUTURE.value}',
                f'is_cfd:{is_cfd}',
            ],
        )

    @staticmethod
    def _classify_equity(raw: str, upper: str, is_cfd: bool) -> InstrumentDescriptor:
        """Classify as equity."""
        cleaned = re.sub(r'(\.PRO|CFD)$', '', upper, flags=re.IGNORECASE)
        cleaned = re.sub(r'\.(EX|OB|PM|IO|NMS|CME|GLOB|ARCA|PAC|NYSE|NASDAQ)$', '', cleaned, flags=re.IGNORECASE)

        return InstrumentDescriptor(
            raw_symbol=raw,
            canonical_symbol=cleaned,
            display_symbol=cleaned,
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.EQUITY_CFD if is_cfd else InstrumentType.EQUITY_SPOT,
            market='broker_cfd' if is_cfd else 'equity',
            provider=None,
            provider_symbol=raw,
            provider_symbols={'yfinance': cleaned},
            base_asset=cleaned,
            quote_asset='USD',
            reference_asset=cleaned,
            venue='NYSE',  # Would need actual lookup for real venue
            is_cfd=is_cfd,
            has_base_quote=False,  # Equity is single asset, not a pair
            classification_trace=[
                f'raw:{raw}',
                f'normalized:{cleaned}',
                f'asset_class:{AssetClass.EQUITY.value}',
                f'instrument_type:{InstrumentType.EQUITY_CFD.value if is_cfd else InstrumentType.EQUITY_SPOT.value}',
                f'is_cfd:{is_cfd}',
            ],
        )

    @staticmethod
    def _classify_etf(raw: str, upper: str) -> InstrumentDescriptor:
        """Classify as ETF."""
        return InstrumentDescriptor(
            raw_symbol=raw,
            canonical_symbol=upper,
            display_symbol=upper,
            asset_class=AssetClass.ETF,
            instrument_type=InstrumentType.ETF_SPOT,
            market='etf',
            provider=None,
            provider_symbol=raw,
            provider_symbols={'yfinance': upper},
            base_asset=upper,
            quote_asset='USD',
            reference_asset=upper,
            venue='ETF',
            is_cfd=False,
            has_base_quote=False,
            classification_trace=[
                f'raw:{raw}',
                f'normalized:{upper}',
                f'asset_class:{AssetClass.ETF.value}',
                f'instrument_type:{InstrumentType.ETF_SPOT.value}',
                'is_cfd:False',
            ],
        )

    @staticmethod
    def _unknown(raw: str) -> InstrumentDescriptor:
        """Return an unknown/generic instrument classification."""
        upper = raw.upper()
        return InstrumentDescriptor(
            raw_symbol=raw,
            canonical_symbol=upper,
            display_symbol=upper,
            asset_class=AssetClass.UNKNOWN,
            instrument_type=InstrumentType.GENERIC_SYMBOL,
            market='unknown',
            provider=None,
            provider_symbol=raw,
            provider_symbols={'raw': upper},
            base_asset=None,
            quote_asset=None,
            reference_asset=None,
            venue=None,
            is_cfd=False,
            has_base_quote=False,
            classification_trace=[
                f'raw:{raw}',
                f'normalized:{upper}',
                f'asset_class:{AssetClass.UNKNOWN.value}',
                f'instrument_type:{InstrumentType.GENERIC_SYMBOL.value}',
                'is_cfd:False',
            ],
        )

    @staticmethod
    def _build_fx_provider_symbols(canonical: str, is_cfd: bool) -> dict[str, str]:
        """Build provider-specific symbols for FX pairs."""
        return {
            'yfinance': f"{canonical}=X",
            'metaapi': f"{canonical}.PRO" if is_cfd else canonical,
            'internal': canonical,
        }

    @staticmethod
    def _build_crypto_provider_symbols(canonical: str, raw: str) -> dict[str, str]:
        """Build provider-specific symbols for crypto pairs."""
        # For YFinance, crypto pairs use hyphen separator (BTC-USD)
        base = canonical
        quote = 'USD'
        for candidate_quote in sorted(CRYPTO_QUOTES, key=len, reverse=True):
            if canonical.endswith(candidate_quote) and len(canonical) > len(candidate_quote):
                base = canonical[:-len(candidate_quote)]
                quote = candidate_quote
                break
        yf_symbol = f"{base}-{quote}"
        return {
            'yfinance': yf_symbol,
            'metaapi': canonical,
            'internal': canonical,
        }

    @staticmethod
    def _build_index_provider_symbols(canonical: str, raw: str, is_cfd: bool) -> dict[str, str]:
        """Build provider-specific symbols for indices."""
        yfinance_map = {
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
        metaapi_map = {
            '^GSPC': 'US500',
            'SPX500': 'US500',
            'US500': 'US500',
            '^NDX': 'NAS100',
            'NSDQ100': 'NAS100',
            'NAS100': 'NAS100',
            '^DJI': 'US30',
            'DJI30': 'US30',
            'US30': 'US30',
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
        yfinance_symbol = yfinance_map.get(canonical, canonical)
        metaapi_symbol = metaapi_map.get(canonical, canonical)
        if is_cfd:
            metaapi_symbol = f'{metaapi_symbol}.PRO'
        if is_cfd:
            return {
                'yfinance': yfinance_symbol,
                'metaapi': metaapi_symbol,
                'internal': canonical,
            }
        return {
            'yfinance': yfinance_symbol,
            'metaapi': metaapi_symbol,
            'internal': canonical,
        }

    @staticmethod
    def _build_metal_provider_symbols(base: str) -> dict[str, str]:
        """Build provider-specific symbols for metals."""
        yfinance_map = {
            'XAU': 'GC=F',
            'XAG': 'SI=F',
        }
        return {
            'yfinance': yfinance_map.get(base, f"{base}=X"),
            'metaapi': f"{base}USD",
            'internal': f"{base}USD",
        }


def normalize_instrument(raw_symbol: str | None) -> InstrumentDescriptor:
    """
    Convenience function to normalize a raw symbol into an InstrumentDescriptor.

    This is the main entry point for instrument normalization across the codebase.
    """
    return InstrumentClassifier.classify(raw_symbol)


def is_instrument_fx_like(instrument: InstrumentDescriptor) -> bool:
    """Check if an instrument is FX-like (has meaningful base/quote structure)."""
    return instrument.has_base_quote and instrument.asset_class == AssetClass.FOREX


def is_instrument_crypto_like(instrument: InstrumentDescriptor) -> bool:
    """Check if an instrument is crypto-like."""
    return instrument.has_base_quote and instrument.asset_class == AssetClass.CRYPTO


def is_instrument_pair_based(instrument: InstrumentDescriptor) -> bool:
    """Check if an instrument is based on a trading pair structure."""
    return instrument.has_base_quote and instrument.base_asset is not None and instrument.quote_asset is not None


def get_instrument_direction_assets(instrument: InstrumentDescriptor) -> tuple[str | None, str | None]:
    """
    Get the primary and secondary assets for directional analysis.

    For FX pairs: returns (base_currency, quote_currency)
    For crypto pairs: returns (base_crypto, quote_currency)
    For indices/equities: returns (reference_asset, None)
    For metals: returns (metal_asset, quote_currency)

    Returns:
        Tuple of (primary_asset, secondary_asset) - secondary may be None
    """
    if instrument.asset_class == AssetClass.FOREX:
        return instrument.base_asset, instrument.quote_asset
    elif instrument.asset_class == AssetClass.CRYPTO:
        return instrument.base_asset, instrument.quote_asset
    elif instrument.asset_class == AssetClass.METAL:
        return instrument.base_asset, instrument.quote_asset
    elif instrument.asset_class in (AssetClass.INDEX, AssetClass.EQUITY, AssetClass.ETF):
        return instrument.reference_asset, None
    else:
        return instrument.reference_asset, instrument.quote_asset
