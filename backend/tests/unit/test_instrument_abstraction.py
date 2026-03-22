"""
Tests for the Instrument Abstraction Layer

This module tests:
1. InstrumentDescriptor and InstrumentClassifier
2. Symbol provider adapters
3. Instrument-aware news analysis
4. Multi-asset classification correctness
"""

import pytest

from app.services.market.instrument import (
    AssetClass,
    InstrumentClassifier,
    InstrumentDescriptor,
    InstrumentType,
    normalize_instrument,
    is_instrument_fx_like,
    is_instrument_crypto_like,
    is_instrument_pair_based,
    get_instrument_direction_assets,
)
from app.services.market.symbol_providers import resolve_symbol_for_provider


class TestInstrumentClassifier:
    """Test instrument classification for various symbol formats."""

    # ========== FX Pairs ==========

    @pytest.mark.parametrize('symbol,expected_base,expected_quote', [
        ('EURUSD', 'EUR', 'USD'),
        ('EURUSD.PRO', 'EUR', 'USD'),
        ('EUR/USD', 'EUR', 'USD'),
        ('GBPJPY', 'GBP', 'JPY'),
        ('USDJPY', 'USD', 'JPY'),
        ('AUDCAD', 'AUD', 'CAD'),
    ])
    def test_fx_pair_classification(self, symbol, expected_base, expected_quote):
        """Test that FX pairs are correctly classified with base/quote assets."""
        result = InstrumentClassifier.classify(symbol)

        assert result.asset_class == AssetClass.FOREX
        assert result.instrument_type == InstrumentType.FX_PAIR
        assert result.base_asset == expected_base
        assert result.quote_asset == expected_quote
        assert result.has_base_quote is True
        assert result.is_fx_like() is True

    def test_fx_pair_cfd_suffix(self):
        """Test that .PRO suffix marks instrument as CFD."""
        result = InstrumentClassifier.classify('EURUSD.PRO')
        assert result.is_cfd is True
        assert result.canonical_symbol == 'EURUSD'

    def test_equity_cfd_suffix_still_classifies_as_equity(self):
        """Test that provider suffixes do not degrade equity classification."""
        result = InstrumentClassifier.classify('AAPL.PRO')
        assert result.asset_class == AssetClass.EQUITY
        assert result.instrument_type == InstrumentType.EQUITY_CFD
        assert result.canonical_symbol == 'AAPL'

    # ========== Crypto Pairs ==========

    @pytest.mark.parametrize('symbol,expected_base,expected_quote', [
        ('BTCUSD', 'BTC', 'USD'),
        ('BTC-USD', 'BTC', 'USD'),
        ('ETHUSD', 'ETH', 'USD'),
        ('ETH-USD', 'ETH', 'USD'),
        ('SOLUSDT', 'SOL', 'USDT'),
        ('ADAUSDC', 'ADA', 'USDC'),
    ])
    def test_crypto_pair_classification(self, symbol, expected_base, expected_quote):
        """Test that crypto pairs are correctly classified."""
        result = InstrumentClassifier.classify(symbol)

        assert result.asset_class == AssetClass.CRYPTO
        assert result.instrument_type == InstrumentType.CRYPTO_PAIR
        assert result.base_asset == expected_base
        assert result.quote_asset == expected_quote
        assert result.has_base_quote is True
        assert result.is_crypto_like() is True

    # ========== Indices ==========

    @pytest.mark.parametrize('symbol', [
        '^GSPC',
        '^NDX',
        '^DJI',
        'SPX500',
        'US500',
        'NAS100',
        'GER40',
        'DE40',
        'UK100',
        '^VIX',
    ])
    def test_index_classification(self, symbol):
        """Test that index symbols are correctly classified."""
        result = InstrumentClassifier.classify(symbol)

        assert result.asset_class == AssetClass.INDEX
        assert result.has_base_quote is False
        assert result.is_index_like() is True
        assert result.base_asset is None
        assert result.quote_asset is None
        assert result.reference_asset is not None

    # ========== Metals ==========

    @pytest.mark.parametrize('symbol', [
        'XAUUSD',
        'XAUUSD.PRO',
        'XAU/USD',
        'GC=F',
        'XAGUSD',
        'SI=F',
    ])
    def test_metal_classification(self, symbol):
        """Test that metal symbols are correctly classified."""
        result = InstrumentClassifier.classify(symbol)

        assert result.asset_class == AssetClass.METAL
        assert result.has_base_quote is True
        assert result.is_metal_like() is True
        assert result.base_asset in ('XAU', 'XAG')
        assert result.quote_asset == 'USD'

    # ========== Energy Commodities ==========

    @pytest.mark.parametrize('symbol', [
        'CL=F',
        'BZ=F',
        'NG=F',
        'CL',
        'BZ',
    ])
    def test_energy_classification(self, symbol):
        """Test that energy commodity symbols are correctly classified."""
        result = InstrumentClassifier.classify(symbol)

        assert result.asset_class == AssetClass.ENERGY
        assert result.is_energy_like() is True
        assert result.reference_asset is not None

    # ========== Equities ==========

    @pytest.mark.parametrize('symbol', [
        'AAPL',
        'TSLA',
        'MSFT',
        'GOOGL',
        'AMZN',
    ])
    def test_equity_classification(self, symbol):
        """Test that equity symbols are correctly classified."""
        result = InstrumentClassifier.classify(symbol)

        assert result.asset_class == AssetClass.EQUITY
        assert result.has_base_quote is False
        assert result.is_equity_like() is True
        assert result.base_asset == symbol
        assert result.reference_asset == symbol

    # ========== ETFs ==========

    @pytest.mark.parametrize('symbol', [
        'SPY',
        'QQQ',
        'IWM',
        'GLD',
        'SLV',
    ])
    def test_etf_classification(self, symbol):
        """Test that ETF symbols are correctly classified."""
        result = InstrumentClassifier.classify(symbol)

        assert result.asset_class == AssetClass.ETF
        assert result.instrument_type == InstrumentType.ETF_SPOT

    # ========== Unknown/Generic ==========

    def test_unknown_symbol(self):
        """Test that unknown symbols are classified as UNKNOWN."""
        result = InstrumentClassifier.classify('UNKNOWN_SYMBOL_XYZ')

        assert result.asset_class == AssetClass.UNKNOWN
        assert result.instrument_type == InstrumentType.GENERIC_SYMBOL

    def test_empty_symbol(self):
        """Test that empty/None symbols are classified as UNKNOWN."""
        result = InstrumentClassifier.classify(None)
        assert result.asset_class == AssetClass.UNKNOWN

        result = InstrumentClassifier.classify('')
        assert result.asset_class == AssetClass.UNKNOWN


class TestInstrumentHelpers:
    """Test instrument helper functions."""

    def test_is_instrument_fx_like(self):
        """Test FX-like detection."""
        fx = InstrumentClassifier.classify('EURUSD')
        crypto = InstrumentClassifier.classify('BTCUSD')
        index = InstrumentClassifier.classify('^GSPC')

        assert is_instrument_fx_like(fx) is True
        assert is_instrument_fx_like(crypto) is False  # Crypto has base/quote but is not FX
        assert is_instrument_fx_like(index) is False

    def test_is_instrument_crypto_like(self):
        """Test crypto-like detection."""
        fx = InstrumentClassifier.classify('EURUSD')
        crypto = InstrumentClassifier.classify('BTCUSD')

        assert is_instrument_crypto_like(crypto) is True
        assert is_instrument_crypto_like(fx) is False

    def test_get_instrument_direction_assets_fx(self):
        """Test direction assets for FX pairs."""
        instrument = InstrumentClassifier.classify('EURUSD')
        primary, secondary = get_instrument_direction_assets(instrument)

        assert primary == 'EUR'
        assert secondary == 'USD'

    def test_get_instrument_direction_assets_crypto(self):
        """Test direction assets for crypto pairs."""
        instrument = InstrumentClassifier.classify('BTCUSD')
        primary, secondary = get_instrument_direction_assets(instrument)

        assert primary == 'BTC'
        assert secondary == 'USD'

    def test_get_instrument_direction_assets_index(self):
        """Test direction assets for indices (no base/quote)."""
        instrument = InstrumentClassifier.classify('^GSPC')
        primary, secondary = get_instrument_direction_assets(instrument)

        assert primary == '^GSPC'
        assert secondary is None

    def test_get_instrument_direction_assets_equity(self):
        """Test direction assets for equities."""
        instrument = InstrumentClassifier.classify('AAPL')
        primary, secondary = get_instrument_direction_assets(instrument)

        assert primary == 'AAPL'
        assert secondary is None


class TestInstrumentDescriptor:
    """Test InstrumentDescriptor dataclass."""

    def test_fx_descriptor_creation(self):
        """Test creating an FX instrument descriptor."""
        desc = InstrumentDescriptor(
            raw_symbol='EURUSD.PRO',
            canonical_symbol='EURUSD',
            display_symbol='EUR/USD',
            asset_class=AssetClass.FOREX,
            instrument_type=InstrumentType.FX_PAIR,
            base_asset='EUR',
            quote_asset='USD',
            is_cfd=True,
            has_base_quote=True,
        )

        assert desc.raw_symbol == 'EURUSD.PRO'
        assert desc.canonical_symbol == 'EURUSD'
        assert desc.display_symbol == 'EUR/USD'
        assert desc.is_cfd is True
        assert desc.is_fx_like() is True

    def test_index_descriptor_creation(self):
        """Test creating an index instrument descriptor."""
        desc = InstrumentDescriptor(
            raw_symbol='^GSPC',
            canonical_symbol='^GSPC',
            display_symbol='S&P 500',
            asset_class=AssetClass.INDEX,
            instrument_type=InstrumentType.INDEX_CASH,
            reference_asset='^GSPC',
            venue='CBOT',
            is_cfd=False,
            has_base_quote=False,
        )

        assert desc.is_index_like() is True
        assert desc.base_asset is None
        assert desc.reference_asset == '^GSPC'

    def test_to_dict(self):
        """Test serialization to dictionary."""
        desc = InstrumentClassifier.classify('EURUSD')
        d = desc.to_dict()

        assert isinstance(d, dict)
        assert d['canonical_symbol'] == 'EURUSD'
        assert d['asset_class'] == 'forex'
        assert d['instrument_type'] == 'fx_pair'
        assert d['base_asset'] == 'EUR'
        assert d['quote_asset'] == 'USD'
        assert d['has_base_quote'] is True


class TestNormalizeInstrument:
    """Test the normalize_instrument convenience function."""

    def test_normalize_fx(self):
        """Test normalizing FX symbol."""
        result = normalize_instrument('EURUSD')
        assert result.asset_class == AssetClass.FOREX
        assert result.canonical_symbol == 'EURUSD'

    def test_normalize_crypto(self):
        """Test normalizing crypto symbol."""
        result = normalize_instrument('BTC-USD')
        assert result.asset_class == AssetClass.CRYPTO
        assert result.canonical_symbol == 'BTCUSD'

    def test_normalize_with_cfd_suffix(self):
        """Test normalizing with CFD suffix."""
        result = normalize_instrument('BTCUSD.PRO')
        assert result.asset_class == AssetClass.CRYPTO
        assert result.is_cfd is True


class TestDisplaySymbols:
    """Test display symbol generation."""

    def test_fx_display_symbol(self):
        """Test FX display symbol formatting."""
        fx = InstrumentClassifier.classify('EURUSD')
        assert fx.display_symbol == 'EUR/USD'

    def test_crypto_display_symbol(self):
        """Test crypto display symbol formatting."""
        crypto = InstrumentClassifier.classify('BTCUSD')
        assert crypto.display_symbol == 'BTC/USD'

    def test_index_display_symbol(self):
        """Test index display symbol is human-readable."""
        index = InstrumentClassifier.classify('^GSPC')
        assert index.display_symbol == 'S&P 500'

    def test_equity_display_symbol(self):
        """Test equity display symbol is the ticker."""
        equity = InstrumentClassifier.classify('AAPL')
        assert equity.display_symbol == 'AAPL'


class TestProviderSymbols:
    """Test provider symbol mapping."""

    def test_fx_yfinance_symbol(self):
        """Test FX YFinance symbol format."""
        fx = InstrumentClassifier.classify('EURUSD')
        assert 'yfinance' in fx.provider_symbols
        # YFinance uses =X suffix for FX
        assert fx.provider_symbols['yfinance'] == 'EURUSD=X'

    def test_crypto_yfinance_symbol(self):
        """Test crypto YFinance symbol format."""
        crypto = InstrumentClassifier.classify('BTCUSD')
        assert 'yfinance' in crypto.provider_symbols
        # YFinance uses hyphen separator
        assert crypto.provider_symbols['yfinance'] == 'BTC-USD'

    def test_crypto_yfinance_symbol_supports_long_base_asset(self):
        """Test crypto provider symbol mapping for non-3-letter assets."""
        crypto = InstrumentClassifier.classify('AVAXUSDT')
        assert crypto.provider_symbols['yfinance'] == 'AVAX-USDT'

    def test_index_yfinance_symbol(self):
        """Test index YFinance symbol format."""
        index = InstrumentClassifier.classify('^GSPC')
        assert 'yfinance' in index.provider_symbols
        assert index.provider_symbols['yfinance'] == '^GSPC'

    def test_fx_cfd_provider_symbols_keep_provider_specific_formats(self):
        """Test that market-data and execution providers keep distinct symbol formats."""
        fx = InstrumentClassifier.classify('EURUSD.PRO')
        assert fx.provider_symbols['yfinance'] == 'EURUSD=X'
        assert fx.provider_symbols['metaapi'] == 'EURUSD.PRO'

    def test_metal_yfinance_symbol(self):
        """Test metal YFinance symbol format."""
        metal = InstrumentClassifier.classify('XAUUSD')
        assert 'yfinance' in metal.provider_symbols
        assert metal.provider_symbols['yfinance'] == 'GC=F'

    def test_provider_adapters_resolve_index_aliases(self):
        """Test provider adapters for canonical index alias conversion."""
        yf = resolve_symbol_for_provider('SPX500', 'yfinance')
        metaapi = resolve_symbol_for_provider('SPX500', 'metaapi')
        assert yf.success is True
        assert yf.provider_symbol == '^GSPC'
        assert metaapi.success is True
        assert metaapi.provider_symbol == 'US500'
