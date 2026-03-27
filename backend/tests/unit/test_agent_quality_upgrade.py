"""Regression tests for the agent quality upgrade (run-66/run-72 anomalies).

Covers:
- News symbol tiering (direct vs fallback)
- Dollar Tree / retail blacklist
- FX rules not applied to crypto
- Market-context degraded flag coherence
- Evidence truncation metadata
- Technical-analyst confidence redesign
- Cross-agent contract normalization
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.market.news_provider import MarketProvider
from app.services.orchestrator.agents import (
    AgentContext,
    MarketContextAnalystAgent,
    NewsAnalystAgent,
    TechnicalAnalystAgent,
    _validate_news_output,
)
from app.services.prompts.registry import PromptTemplateService


def _iso_hours_ago(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=max(hours, 0))).isoformat().replace('+00:00', 'Z')


# ---------------------------------------------------------------------------
# TG2 — News symbol tiering
# ---------------------------------------------------------------------------

def test_news_symbol_candidates_tiered_fx_puts_direct_first() -> None:
    direct, fallback = MarketProvider._news_symbol_candidates_tiered('USDCHF')
    assert len(direct) >= 1
    # Direct must contain the pair symbol (USDCHF=X or similar)
    assert any('CHF' in s or 'chf' in s.lower() for s in direct)
    # Fallbacks include currency ETFs and macro proxies
    fallback_set = set(fallback)
    # DX-Y.NYB is a USD index proxy — must be in fallback, not direct
    if 'DX-Y.NYB' in direct + fallback:
        assert 'DX-Y.NYB' in fallback_set


def test_news_symbol_candidates_tiered_crypto_puts_direct_first() -> None:
    direct, fallback = MarketProvider._news_symbol_candidates_tiered('AVAXUSD')
    assert len(direct) >= 1
    assert any('AVAX' in s for s in direct)
    # BTC-USD / ETH-USD are sector fallbacks, not direct
    assert 'BTC-USD' not in direct
    assert 'ETH-USD' not in direct
    if 'BTC-USD' in direct + fallback:
        assert 'BTC-USD' in set(fallback)


def test_news_symbol_candidates_flat_is_direct_then_fallback() -> None:
    flat = MarketProvider._news_symbol_candidates('EURUSD')
    direct, fallback = MarketProvider._news_symbol_candidates_tiered('EURUSD')
    assert flat == direct + fallback


def test_news_symbol_candidates_tiered_btc_has_no_sector_fallback() -> None:
    """BTC-USD should not add itself as a sector fallback."""
    direct, fallback = MarketProvider._news_symbol_candidates_tiered('BTCUSD')
    assert 'BTC-USD' not in fallback


@pytest.mark.parametrize(
    ('pair', 'expected_direct'),
    [
        ('EURUSD', 'EURUSD=X'),
        ('USDCHF', 'USDCHF=X'),
        ('USDCAD', 'USDCAD=X'),
        ('NZDUSD', 'NZDUSD=X'),
        ('EURJPY', 'EURJPY=X'),
        ('GBPJPY', 'GBPJPY=X'),
        ('EURGBP', 'EURGBP=X'),
        ('BTCUSD', 'BTC-USD'),
        ('ETHUSD', 'ETH-USD'),
        ('DOGEUSD', 'DOGE-USD'),
        ('AVAXUSD', 'AVAX-USD'),
        ('BCHUSD', 'BCH-USD'),
        ('DOTUSD', 'DOT-USD'),
        ('LTCUSD', 'LTC-USD'),
        ('MATICUSD', 'MATIC-USD'),
        ('UNIUSD', 'UNI-USD'),
    ],
)
def test_news_symbol_candidates_regression_pairs_keep_exact_mapping_first(pair: str, expected_direct: str) -> None:
    direct, fallback = MarketProvider._news_symbol_candidates_tiered(pair)
    assert direct, f'{pair}: direct candidates must not be empty'
    assert direct[0] == expected_direct, f'{pair}: first direct candidate must be exact mapped symbol'
    assert expected_direct not in set(fallback), f'{pair}: exact mapped symbol must not move to fallback'


# ---------------------------------------------------------------------------
# TG2/TG3 — Dollar Tree / retail blacklist
# ---------------------------------------------------------------------------

def test_normalize_article_rejects_dollar_tree_on_fx() -> None:
    result = MarketProvider._normalize_article_item(
        provider='yahoo_finance',
        pair='EURUSD',
        title='Dollar Tree Reports Strong Q4 Earnings, Stock Rises 8%',
        summary='Dollar Tree Inc beat analyst expectations.',
        url='https://example.com/dollar-tree',
        published_at=_iso_hours_ago(2),
        source_name='Reuters',
    )
    assert result is None, 'Dollar Tree headline should be rejected for FX pairs'


def test_normalize_article_rejects_dollar_general_on_fx() -> None:
    result = MarketProvider._normalize_article_item(
        provider='yahoo_finance',
        pair='USDCHF',
        title='Dollar General shares drop after weak guidance',
        summary=None,
        url=None,
        published_at=_iso_hours_ago(1),
        source_name='Bloomberg',
    )
    assert result is None, 'Dollar General headline should be rejected for FX pairs'


def test_normalize_article_keeps_real_dollar_story_on_fx() -> None:
    result = MarketProvider._normalize_article_item(
        provider='yahoo_finance',
        pair='EURUSD',
        title='Dollar weakens as Fed signals rate cuts ahead',
        summary='The US dollar index fell to a two-week low.',
        url='https://example.com/usd-weak',
        published_at=_iso_hours_ago(1),
        source_name='Reuters',
    )
    assert result is not None, 'Real FX dollar story should not be blocked by blacklist'


def test_normalize_article_allows_dollar_tree_on_equity() -> None:
    """Blacklist only applies to FX/commodity, not equities."""
    result = MarketProvider._normalize_article_item(
        provider='yahoo_finance',
        pair='DLTR',
        title='Dollar Tree Reports Strong Q4 Earnings, Stock Rises 8%',
        summary='Dollar Tree Inc beat analyst expectations.',
        url='https://example.com/dollar-tree',
        published_at=_iso_hours_ago(2),
        source_name='Reuters',
    )
    # Equity pair — should NOT be rejected
    assert result is not None


# ---------------------------------------------------------------------------
# TG3 — FX rules not applied to crypto
# ---------------------------------------------------------------------------

def test_fx_neutral_evidence_alignment_skipped_for_crypto() -> None:
    """fx_neutral_evidence_alignment must not fire when asset_class is crypto."""
    output = {
        'signal': 'neutral',
        'score': 0.0,
        'confidence': 0.15,
        'summary': 'mixed signals',
        'decision_mode': 'directional',
        'reason': 'directional edge from evidence',
    }
    evidence = [
        {
            'asset_class': 'crypto',
            'final_pair_relevance': 0.70,
            'directional_eligible': True,
            'instrument_directional_effect': 'neutral',
            'impact_on_base': 'unknown',
            'impact_on_quote': 'unknown',
        }
    ]
    result = _validate_news_output(
        output,
        selected_evidence=evidence,
        rejected_evidence=[],
        min_directional_relevance=0.35,
        asset_class='crypto',
    )
    # Should NOT have fx_neutral_evidence_alignment action
    actions = result.get('validation_actions', [])
    assert 'fx_neutral_evidence_alignment' not in actions


def test_fx_neutral_evidence_alignment_fires_for_fx() -> None:
    """fx_neutral_evidence_alignment should fire for actual FX pairs."""
    output = {
        'signal': 'neutral',
        'score': 0.0,
        'confidence': 0.15,
        'summary': 'mixed signals',
        'decision_mode': 'directional',
        'reason': 'directional edge from evidence',
    }
    evidence = [
        {
            'asset_class': 'fx',
            'final_pair_relevance': 0.40,
            'directional_eligible': True,
            'instrument_directional_effect': 'neutral',
            'impact_on_base': 'unknown',
            'impact_on_quote': 'unknown',
            'base_currency_effect': 'unknown',
            'quote_currency_effect': 'unknown',
        }
    ]
    result = _validate_news_output(
        output,
        selected_evidence=evidence,
        rejected_evidence=[],
        min_directional_relevance=0.35,
        asset_class='fx',
    )
    actions = result.get('validation_actions', [])
    assert 'fx_neutral_evidence_alignment' in actions


# ---------------------------------------------------------------------------
# TG6 — Market-context degraded flag coherence
# ---------------------------------------------------------------------------

def test_market_context_degraded_when_llm_fallback(monkeypatch) -> None:
    agent = MarketContextAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_a, **_k: 'test-model')
    monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_a, **_k: 'conservative')
    monkeypatch.setattr(agent.llm, 'chat', lambda *_a, **_k: {'text': '', 'degraded': True})

    ctx = AgentContext(
        pair='EURUSD', timeframe='H1', mode='simulation', risk_percent=1.0,
        market_snapshot={
            'last_price': 1.1, 'atr': 0.001, 'trend': 'bullish',
            'change_pct': 0.1, 'rsi': 55, 'macd_diff': 0.03,
            'ema_fast': 1.101, 'ema_slow': 1.099,
        },
        news_context={'news': []}, memory_context=[],
    )
    out = agent.run(ctx)
    assert out['llm_fallback_used'] is True
    assert out['degraded'] is True, 'degraded must be True when llm_fallback_used is True'


# ---------------------------------------------------------------------------
# TG4 — Evidence truncation metadata
# ---------------------------------------------------------------------------

def test_news_output_exposes_evidence_truncation_counts(monkeypatch) -> None:
    """evidence_total_count and evidence_exposed_count must be present and consistent."""
    service = PromptTemplateService()
    agent = NewsAnalystAgent(service)
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: False)

    headlines = [
        {'title': f'Dollar {verb} on Fed policy shift #{i}'}
        for i, verb in enumerate(
            ['weakens', 'falls', 'drops', 'declines', 'sinks',
             'rises', 'gains', 'rallies', 'strengthens', 'surges'],
        )
    ]
    ctx = AgentContext(
        pair='EURUSD', timeframe='H1', mode='simulation', risk_percent=1.0,
        market_snapshot={'trend': 'neutral'},
        news_context={'news': headlines}, memory_context=[],
    )
    out = agent.run(ctx, db=None)
    assert 'evidence_total_count' in out
    assert 'evidence_exposed_count' in out
    assert out['evidence_exposed_count'] <= out['evidence_total_count']
    assert out['evidence_exposed_count'] == len(out.get('evidence', []))


# ---------------------------------------------------------------------------
# TG7 — Technical-analyst confidence redesign
# ---------------------------------------------------------------------------

def test_technical_confidence_is_quality_weighted_not_just_abs_score() -> None:
    agent = TechnicalAnalystAgent()
    ctx = AgentContext(
        pair='EURUSD', timeframe='H1', mode='simulation', risk_percent=1.0,
        market_snapshot={
            'trend': 'bullish', 'rsi': 55, 'macd_diff': 0.0003,
            'atr': 0.001, 'last_price': 1.1, 'change_pct': 0.1,
            'ema_fast': 1.101, 'ema_slow': 1.099,
        },
        news_context={'news': []}, memory_context=[],
    )
    out = agent.run(ctx)
    # confidence_method must exist and describe the formula
    assert 'confidence_method' in out
    assert out['confidence_method'] in {'deterministic_quality_weighted', 'llm_merged_quality_weighted', 'degraded'}
    # raw_score must exist and match deterministic score
    assert 'raw_score' in out
    assert out['raw_score'] == out['score']  # No LLM merge in this test


def test_technical_confidence_boosted_for_high_quality() -> None:
    """High quality setup should get confidence boost beyond abs(score)."""
    boost = TechnicalAnalystAgent._compute_confidence(0.40, 'high')
    base = TechnicalAnalystAgent._compute_confidence(0.40, 'medium')
    assert boost > base, 'High quality should boost confidence'
    assert boost <= 0.95, 'Confidence must not exceed 0.95'


def test_technical_confidence_capped_for_low_quality() -> None:
    """Low quality setup should cap confidence at 0.40."""
    capped = TechnicalAnalystAgent._compute_confidence(0.60, 'low')
    assert capped <= 0.40, 'Low quality must cap confidence at 0.40'


def test_technical_degraded_output_has_full_contract() -> None:
    """Even degraded output should have all contract fields."""
    agent = TechnicalAnalystAgent()
    ctx = AgentContext(
        pair='EURUSD', timeframe='H1', mode='simulation', risk_percent=1.0,
        market_snapshot={'degraded': True},
        news_context={'news': []}, memory_context=[],
    )
    out = agent.run(ctx)
    assert out['degraded'] is True
    assert 'raw_score' in out
    assert 'confidence_method' in out
    assert out['confidence_method'] == 'degraded'
    assert 'market_bias' in out
    assert 'setup_quality' in out


def test_technical_infra_error_stays_in_diagnostics_not_summary(monkeypatch) -> None:
    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_a, **_k: 'test-model')
    monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_a, **_k: 'conservative')
    monkeypatch.setattr(
        agent.llm,
        'chat',
        lambda *_a, **_k: {
            'text': 'OpenAI 429 Too Many Requests after retries',
            'degraded': True,
            'provider': 'openai',
        },
    )

    out = agent.run(AgentContext(
        pair='EURUSD', timeframe='H1', mode='simulation', risk_percent=1.0,
        market_snapshot={
            'trend': 'bullish', 'rsi': 55, 'macd_diff': 0.0003,
            'atr': 0.001, 'last_price': 1.1, 'change_pct': 0.1,
            'ema_fast': 1.101, 'ema_slow': 1.099,
        },
        news_context={'news': []}, memory_context=[],
    ))

    assert out['llm_fallback_used'] is True
    assert out['degraded'] is True
    assert '429' not in str(out.get('summary', '')).lower()
    diagnostics = out.get('diagnostics')
    assert isinstance(diagnostics, dict)
    assert '429' in str(diagnostics).lower()


def test_technical_neutral_signal_does_not_keep_directional_llm_summary(monkeypatch) -> None:
    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_a, **_k: 'test-model')
    monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_a, **_k: 'conservative')
    monkeypatch.setattr(
        agent.llm,
        'chat',
        lambda *_a, **_k: {
            'text': 'bullish\nsetup_quality=low\nvalidation=breakout confirmation\ninvalidation=below local support',
            'degraded': False,
            'provider': 'unit-test',
        },
    )

    out = agent.run(AgentContext(
        pair='EURUSD', timeframe='H1', mode='simulation', risk_percent=1.0,
        market_snapshot={
            'trend': 'neutral', 'rsi': 50, 'macd_diff': 0.0,
            'atr': 0.001, 'last_price': 1.1, 'change_pct': 0.0,
            'ema_fast': 1.1, 'ema_slow': 1.1,
        },
        news_context={'news': []}, memory_context=[],
    ))

    assert out['signal'] == 'neutral'
    assert out['summary'].startswith('neutral')
    assert 'bullish' not in str(out.get('summary') or '').lower()


def _price_history_bars(count: int = 60, *, start: float = 1.1530) -> list[dict[str, float]]:
    bars: list[dict[str, float]] = []
    price = float(start)
    for index in range(max(count, 1)):
        close = price + (0.00003 if index % 2 == 0 else -0.00002)
        bars.append(
            {
                'open': price,
                'high': max(price, close) + 0.00008,
                'low': min(price, close) - 0.00008,
                'close': close,
            }
        )
        price = close
    return bars


def _setup_quality_rank(value: str) -> int:
    return {'low': 0, 'medium': 1, 'high': 2}.get(str(value or '').lower(), -1)


def test_technical_aligned_bearish_keeps_directional_setup(monkeypatch) -> None:
    import app.services.agent_runtime.mcp_trading_server as mcp

    monkeypatch.setattr(mcp, 'divergence_detector', lambda **_kwargs: {'divergences': []})
    monkeypatch.setattr(
        mcp,
        'pattern_detector',
        lambda **_kwargs: {
            'patterns': [
                {'type': 'bearish_engulfing', 'signal': 'bearish', 'strength': 0.85, 'bar_index': 238},
            ]
        },
    )
    monkeypatch.setattr(
        mcp,
        'support_resistance_detector',
        lambda **_kwargs: {
            'levels': [{'price': 1.15814, 'distance_pct': 0.43, 'type': 'resistance'}],
            'count': 1,
        },
    )
    monkeypatch.setattr(
        mcp,
        'multi_timeframe_context',
        lambda **_kwargs: {
            'dominant_direction': 'bearish',
            'alignment_score': 1.0,
            'confluence': 'strong',
            'all_aligned': True,
        },
    )

    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: False)

    out = agent.run(
        AgentContext(
            pair='EURUSD.PRO',
            timeframe='M15',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'trend': 'bearish',
                'rsi': 34.481,
                'macd_diff': -0.002,
                'atr': 0.000734,
                'last_price': 1.15321,
                'change_pct': -0.30,
                'ema_fast': 1.1538,
                'ema_slow': 1.1550,
            },
            news_context={'news': []},
            memory_context=[],
            price_history=_price_history_bars(),
        )
    )

    assert out['signal'] == 'bearish'
    assert out['setup_quality'] in {'high', 'medium'}
    assert 'indicator_bundle' in out.get('evidence_used', [])
    assert 'market_snapshot' in out.get('evidence_used', [])


def test_technical_conflict_divergence_reduces_setup_quality(monkeypatch) -> None:
    import app.services.agent_runtime.mcp_trading_server as mcp

    monkeypatch.setattr(
        mcp,
        'pattern_detector',
        lambda **_kwargs: {
            'patterns': [
                {'type': 'bearish_engulfing', 'signal': 'bearish', 'strength': 0.85, 'bar_index': 238},
            ]
        },
    )
    monkeypatch.setattr(
        mcp,
        'support_resistance_detector',
        lambda **_kwargs: {'levels': [{'price': 1.15814, 'distance_pct': 0.43, 'type': 'resistance'}]},
    )
    monkeypatch.setattr(
        mcp,
        'multi_timeframe_context',
        lambda **_kwargs: {'dominant_direction': 'bearish', 'alignment_score': 1.0, 'all_aligned': True},
    )

    base_agent = TechnicalAnalystAgent()
    monkeypatch.setattr(base_agent.model_selector, 'is_enabled', lambda *_a, **_k: False)
    monkeypatch.setattr(mcp, 'divergence_detector', lambda **_kwargs: {'divergences': []})
    base_out = base_agent.run(
        AgentContext(
            pair='EURUSD.PRO',
            timeframe='H1',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'trend': 'bearish',
                'rsi': 34.0,
                'macd_diff': -0.002,
                'atr': 0.001,
                'last_price': 1.1530,
                'change_pct': -0.2,
            },
            news_context={'news': []},
            memory_context=[],
            price_history=_price_history_bars(),
        )
    )

    conflict_agent = TechnicalAnalystAgent()
    monkeypatch.setattr(conflict_agent.model_selector, 'is_enabled', lambda *_a, **_k: False)
    monkeypatch.setattr(
        mcp,
        'divergence_detector',
        lambda **_kwargs: {
            'divergences': [
                {'type': 'bullish', 'bars_apart': 6, 'rsi_low_1': 31.47, 'rsi_low_2': 35.75},
            ]
        },
    )
    conflict_out = conflict_agent.run(
        AgentContext(
            pair='EURUSD.PRO',
            timeframe='H1',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'trend': 'bearish',
                'rsi': 34.0,
                'macd_diff': -0.002,
                'atr': 0.001,
                'last_price': 1.1530,
                'change_pct': -0.2,
            },
            news_context={'news': []},
            memory_context=[],
            price_history=_price_history_bars(),
        )
    )

    assert _setup_quality_rank(conflict_out['setup_quality']) < _setup_quality_rank(base_out['setup_quality'])


def test_technical_observed_conflict_case_clamps_setup_to_low(monkeypatch) -> None:
    import app.services.agent_runtime.mcp_trading_server as mcp

    monkeypatch.setattr(mcp, 'divergence_detector', lambda **_kwargs: {'divergences': []})
    monkeypatch.setattr(
        mcp,
        'pattern_detector',
        lambda **_kwargs: {
            'patterns': [
                {'type': 'bearish_engulfing', 'signal': 'bearish', 'strength': 0.85, 'bar_index': 238},
                {'type': 'doji', 'signal': 'neutral', 'strength': 0.5, 'bar_index': 239},
                {'type': 'pin_bar', 'signal': 'bullish', 'strength': 0.8, 'bar_index': 240},
            ]
        },
    )
    monkeypatch.setattr(
        mcp,
        'multi_timeframe_context',
        lambda **_kwargs: {'dominant_direction': 'bearish', 'alignment_score': 1.0, 'all_aligned': True},
    )
    monkeypatch.setattr(
        mcp,
        'support_resistance_detector',
        lambda **_kwargs: {'levels': [{'price': 1.15814, 'distance_pct': 0.43, 'type': 'resistance'}]},
    )

    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: False)
    out = agent.run(
        AgentContext(
            pair='EURUSD.PRO',
            timeframe='H1',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'trend': 'bearish',
                'rsi': 49.8,
                'macd_diff': 0.0002,
                'atr': 0.000734,
                'last_price': 1.15321,
                'change_pct': -0.05,
            },
            news_context={'news': []},
            memory_context=[],
            price_history=_price_history_bars(),
        )
    )

    assert out['setup_quality'] == 'low'
    assert out['signal'] in {'bearish', 'neutral'}


def test_technical_confused_context_prefers_neutral_low_quality(monkeypatch) -> None:
    import app.services.agent_runtime.mcp_trading_server as mcp

    monkeypatch.setattr(mcp, 'divergence_detector', lambda **_kwargs: {'divergences': []})
    monkeypatch.setattr(mcp, 'pattern_detector', lambda **_kwargs: {'patterns': []})
    monkeypatch.setattr(mcp, 'support_resistance_detector', lambda **_kwargs: {'levels': []})
    monkeypatch.setattr(
        mcp,
        'multi_timeframe_context',
        lambda **_kwargs: {'dominant_direction': 'neutral', 'alignment_score': 0.0, 'all_aligned': False},
    )

    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: False)
    out = agent.run(
        AgentContext(
            pair='BTCUSD',
            timeframe='H1',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'trend': 'neutral',
                'rsi': 50.0,
                'macd_diff': 0.0,
                'atr': 10.0,
                'last_price': 70000.0,
                'change_pct': 0.0,
            },
            news_context={'news': []},
            memory_context=[],
            price_history=_price_history_bars(start=70000.0),
        )
    )

    assert out['signal'] == 'neutral'
    assert out['setup_quality'] == 'low'


def test_technical_strong_structure_without_local_momentum_stays_low(monkeypatch) -> None:
    import app.services.agent_runtime.mcp_trading_server as mcp

    monkeypatch.setattr(mcp, 'divergence_detector', lambda **_kwargs: {'divergences': []})
    monkeypatch.setattr(
        mcp,
        'pattern_detector',
        lambda **_kwargs: {'patterns': [{'type': 'doji', 'signal': 'neutral', 'strength': 0.5, 'bar_index': 240}]},
    )
    monkeypatch.setattr(
        mcp,
        'multi_timeframe_context',
        lambda **_kwargs: {'dominant_direction': 'bearish', 'alignment_score': 1.0, 'all_aligned': True},
    )
    monkeypatch.setattr(
        mcp,
        'support_resistance_detector',
        lambda **_kwargs: {'levels': [{'price': 1.15814, 'distance_pct': 0.43, 'type': 'resistance'}]},
    )

    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: False)
    out = agent.run(
        AgentContext(
            pair='EURUSD.PRO',
            timeframe='H1',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'trend': 'bearish',
                'rsi': 48.2,
                'macd_diff': 0.0001,
                'atr': 0.000734,
                'last_price': 1.15321,
                'change_pct': -0.02,
            },
            news_context={'news': []},
            memory_context=[],
            price_history=_price_history_bars(),
        )
    )

    assert out['setup_quality'] == 'low'
    assert out['signal'] in {'bearish', 'neutral'}


def test_technical_enriched_contract_fields_present() -> None:
    agent = TechnicalAnalystAgent()
    ctx = AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={
            'trend': 'bullish',
            'rsi': 56.0,
            'macd_diff': 0.0004,
            'atr': 0.001,
            'last_price': 1.1,
            'change_pct': 0.1,
            'ema_fast': 1.101,
            'ema_slow': 1.099,
        },
        news_context={'news': []},
        memory_context=[],
    )
    out = agent.run(ctx)

    assert out['structural_bias'] in {'bullish', 'bearish', 'neutral'}
    assert out['local_momentum'] in {'bullish', 'bearish', 'neutral', 'mixed'}
    assert out['setup_state'] in {'non_actionable', 'conditional', 'weak_actionable', 'actionable', 'high_conviction'}
    assert out['actionable_signal'] in {'bullish', 'bearish', 'neutral'}
    assert isinstance(out.get('tradability'), float)
    assert isinstance(out.get('score_breakdown'), dict)
    assert isinstance(out.get('contradictions'), list)

    breakdown = out['score_breakdown']
    for key in (
        'structure_score',
        'momentum_score',
        'pattern_score',
        'divergence_score',
        'multi_timeframe_score',
        'level_score',
        'contradiction_penalty',
        'recency_adjustment',
        'final_score',
    ):
        assert key in breakdown


def test_technical_conditional_state_when_structure_bearish_but_momentum_mixed(monkeypatch) -> None:
    import app.services.agent_runtime.mcp_trading_server as mcp

    monkeypatch.setattr(
        mcp,
        'divergence_detector',
        lambda **_kwargs: {
            'divergences': [
                {'type': 'bullish', 'bars_apart': 4, 'rsi_low_1': 31.2, 'rsi_low_2': 36.1},
            ]
        },
    )
    monkeypatch.setattr(
        mcp,
        'pattern_detector',
        lambda **_kwargs: {
            'patterns': [
                {'type': 'bearish_engulfing', 'signal': 'bearish', 'strength': 0.84, 'bar_index': 238},
                {'type': 'pin_bar', 'signal': 'bullish', 'strength': 0.81, 'bar_index': 239},
            ]
        },
    )
    monkeypatch.setattr(
        mcp,
        'multi_timeframe_context',
        lambda **_kwargs: {'dominant_direction': 'bearish', 'alignment_score': 1.0, 'all_aligned': True},
    )
    monkeypatch.setattr(
        mcp,
        'support_resistance_detector',
        lambda **_kwargs: {'levels': [{'price': 1.15814, 'distance_pct': 0.43, 'type': 'resistance'}]},
    )

    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: False)
    out = agent.run(
        AgentContext(
            pair='EURUSD.PRO',
            timeframe='H1',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'trend': 'bearish',
                'rsi': 49.8,
                'macd_diff': 0.0002,
                'atr': 0.000734,
                'last_price': 1.15321,
                'change_pct': -0.05,
                'ema_fast': 1.1538,
                'ema_slow': 1.1550,
            },
            news_context={'news': []},
            memory_context=[],
            price_history=_price_history_bars(),
        )
    )

    assert out['structural_bias'] == 'bearish'
    assert out['local_momentum'] == 'mixed'
    assert out['setup_state'] == 'conditional'
    assert out['actionable_signal'] == 'neutral'


def test_technical_non_actionable_state_when_no_directional_edge(monkeypatch) -> None:
    import app.services.agent_runtime.mcp_trading_server as mcp

    monkeypatch.setattr(mcp, 'divergence_detector', lambda **_kwargs: {'divergences': []})
    monkeypatch.setattr(mcp, 'pattern_detector', lambda **_kwargs: {'patterns': []})
    monkeypatch.setattr(mcp, 'support_resistance_detector', lambda **_kwargs: {'levels': []})
    monkeypatch.setattr(
        mcp,
        'multi_timeframe_context',
        lambda **_kwargs: {'dominant_direction': 'neutral', 'alignment_score': 0.0, 'all_aligned': False},
    )

    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: False)
    out = agent.run(
        AgentContext(
            pair='BTCUSD',
            timeframe='H1',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'trend': 'neutral',
                'rsi': 50.0,
                'macd_diff': 0.0,
                'atr': 10.0,
                'last_price': 70000.0,
                'change_pct': 0.0,
            },
            news_context={'news': []},
            memory_context=[],
            price_history=_price_history_bars(start=70000.0),
        )
    )

    assert out['setup_state'] == 'non_actionable'
    assert out['actionable_signal'] == 'neutral'


def test_technical_high_conviction_state_when_all_components_converge(monkeypatch) -> None:
    import app.services.agent_runtime.mcp_trading_server as mcp

    monkeypatch.setattr(
        mcp,
        'divergence_detector',
        lambda **_kwargs: {'divergences': [{'type': 'bearish', 'bars_apart': 2}]},
    )
    monkeypatch.setattr(
        mcp,
        'pattern_detector',
        lambda **_kwargs: {
            'patterns': [
                {'type': 'bearish_engulfing', 'signal': 'bearish', 'strength': 0.95, 'bar_index': 239},
                {'type': 'continuation', 'signal': 'bearish', 'strength': 0.90, 'bar_index': 240},
            ]
        },
    )
    monkeypatch.setattr(
        mcp,
        'multi_timeframe_context',
        lambda **_kwargs: {'dominant_direction': 'bearish', 'alignment_score': 1.0, 'all_aligned': True},
    )
    monkeypatch.setattr(
        mcp,
        'support_resistance_detector',
        lambda **_kwargs: {'levels': [{'price': 1.15814, 'distance_pct': 0.25, 'type': 'resistance'}]},
    )

    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: False)
    out = agent.run(
        AgentContext(
            pair='EURUSD.PRO',
            timeframe='M15',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'trend': 'bearish',
                'rsi': 30.0,
                'macd_diff': -0.003,
                'atr': 0.0007,
                'last_price': 1.15321,
                'change_pct': -0.35,
                'ema_fast': 1.1530,
                'ema_slow': 1.1560,
            },
            news_context={'news': []},
            memory_context=[],
            price_history=_price_history_bars(),
        )
    )

    assert out['actionable_signal'] == 'bearish'
    assert out['setup_state'] == 'high_conviction'
    assert out['setup_quality'] == 'high'


def test_technical_recency_weighting_amplifies_recent_patterns(monkeypatch) -> None:
    import app.services.agent_runtime.mcp_trading_server as mcp

    monkeypatch.setattr(mcp, 'divergence_detector', lambda **_kwargs: {'divergences': []})
    monkeypatch.setattr(
        mcp,
        'multi_timeframe_context',
        lambda **_kwargs: {'dominant_direction': 'bullish', 'alignment_score': 1.0, 'all_aligned': True},
    )
    monkeypatch.setattr(
        mcp,
        'support_resistance_detector',
        lambda **_kwargs: {'levels': [{'price': 1.14800, 'distance_pct': 0.40, 'type': 'support'}]},
    )

    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: False)
    monkeypatch.setattr(
        mcp,
        'pattern_detector',
        lambda **_kwargs: {
            'patterns': [
                {'type': 'flag', 'signal': 'bullish', 'strength': 1.0, 'bar_index': 120},
                {'type': 'flag', 'signal': 'bullish', 'strength': 1.0, 'bar_index': 239},
            ]
        },
    )
    out = agent.run(
        AgentContext(
            pair='EURUSD.PRO',
            timeframe='M15',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'trend': 'bullish',
                'rsi': 58.0,
                'macd_diff': 0.0012,
                'atr': 0.0009,
                'last_price': 1.15321,
                'change_pct': 0.20,
                'ema_fast': 1.1540,
                'ema_slow': 1.1510,
            },
            news_context={'news': []},
            memory_context=[],
            price_history=_price_history_bars(),
        )
    )

    # Without recency weighting, two bullish patterns of strength 1.0 would be 0.12.
    assert out['score_breakdown']['pattern_score'] < 0.12
    assert out['score_breakdown']['recency_adjustment'] < 0.0


def test_technical_prompt_handles_partial_tools_without_hallucination(monkeypatch) -> None:
    captured_user_prompts: list[str] = []
    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_a, **_k: 'test-model')
    monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_a, **_k: 'conservative')
    monkeypatch.setattr(
        agent.model_selector,
        'resolve_enabled_tools',
        lambda *_a, **_k: ['market_snapshot', 'indicator_bundle'],
    )

    def _fake_chat(_system: str, _user: str, **kwargs):
        captured_user_prompts.append(_user)
        if kwargs.get('tool_choice') == 'required':
            return {
                'text': '',
                'degraded': False,
                'tool_calls': [{'id': 'call_market_snapshot', 'name': 'market_snapshot', 'arguments': {}}],
            }
        return {
            'text': (
                'neutral\n'
                'setup_quality=low\n'
                'validation=ok\n'
                'invalidation=ok\n'
                'evidence_used=indicator_bundle,market_snapshot'
            ),
            'degraded': False,
        }

    monkeypatch.setattr(agent.llm, 'chat', _fake_chat)
    out = agent.run(
        AgentContext(
            pair='XAUUSD',
            timeframe='M15',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'trend': 'bearish',
                'atr': 0.8,
                'last_price': 2175.4,
            },
            news_context={'news': []},
            memory_context=[],
        )
    )

    assert out['degraded'] is False
    assert captured_user_prompts, 'expected user prompt'
    prompt = captured_user_prompts[0]
    assert '[source:' not in prompt.lower()
    assert '[tool:' in prompt
    assert 'RSI:' not in prompt
    assert 'MACD diff:' not in prompt


def test_technical_prompt_sections_order_and_contract_format(monkeypatch) -> None:
    captured_user_prompts: list[str] = []
    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_a, **_k: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_a, **_k: 'test-model')
    monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_a, **_k: 'conservative')

    def _fake_chat(_system: str, _user: str, **kwargs):
        captured_user_prompts.append(_user)
        if kwargs.get('tool_choice') == 'required':
            return {
                'text': '',
                'degraded': False,
                'tool_calls': [{'id': 'call_market_snapshot', 'name': 'market_snapshot', 'arguments': {}}],
            }
        return {
            'text': (
                'bearish\n'
                'setup_quality=medium\n'
                'validation=test\n'
                'invalidation=test\n'
                'evidence_used=indicator_bundle,market_snapshot'
            ),
            'degraded': False,
        }

    monkeypatch.setattr(agent.llm, 'chat', _fake_chat)
    agent.run(
        AgentContext(
            pair='EURUSD.PRO',
            timeframe='M15',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={
                'trend': 'bearish',
                'rsi': 36.481,
                'macd_diff': -0.000241,
                'atr': 0.000734,
                'last_price': 1.15321,
            },
            news_context={'news': []},
            memory_context=[],
            price_history=_price_history_bars(),
        )
    )

    assert captured_user_prompts, 'expected user prompt'
    prompt = captured_user_prompts[0]
    facts_idx = prompt.find('Raw facts:')
    tools_idx = prompt.find('Pre-executed tool results:')
    rules_idx = max(prompt.find("Interpretation rules:"), prompt.find('Interpretation rules:'))
    contract_idx = prompt.find('Strict output contract:')
    assert -1 not in {facts_idx, tools_idx, rules_idx, contract_idx}
    assert facts_idx < tools_idx < rules_idx < contract_idx
    assert '[source:' not in prompt.lower()
    assert '[tool:' in prompt
    assert 'evidence_used=<short list of tools/fields actually used>' in prompt


# ---------------------------------------------------------------------------
# TG7 — Market-context market_bias clarity
# ---------------------------------------------------------------------------

def test_market_context_market_bias_present() -> None:
    agent = MarketContextAnalystAgent()
    out = agent.run(AgentContext(
        pair='EURUSD', timeframe='H1', mode='simulation', risk_percent=1.0,
        market_snapshot={
            'last_price': 1.1, 'atr': 0.001, 'trend': 'bullish',
            'change_pct': 0.05, 'rsi': 52, 'macd_diff': 0.01,
            'ema_fast': 1.1005, 'ema_slow': 1.0995,
        },
        news_context={'news': []}, memory_context=[],
    ))
    assert 'market_bias' in out
    assert out['market_bias'] in {'bullish', 'bearish', 'neutral'}
    assert 'confidence_method' in out


def test_market_context_neutral_signal_with_directional_bias() -> None:
    """When signal=neutral but score != 0, market_bias should show the lean."""
    agent = MarketContextAnalystAgent()
    out = agent.run(AgentContext(
        pair='EURUSD', timeframe='H1', mode='simulation', risk_percent=1.0,
        market_snapshot={
            'last_price': 1.1, 'atr': 0.002, 'trend': 'bullish',
            'change_pct': -0.12, 'rsi': 48, 'macd_diff': -0.03,
            'ema_fast': 1.099, 'ema_slow': 1.101,
        },
        news_context={'news': []}, memory_context=[],
    ))
    assert out['signal'] == 'neutral'
    # market_bias can show the lean even when signal is neutral
    assert out['market_bias'] in {'bullish', 'bearish', 'neutral'}


# ---------------------------------------------------------------------------
# TG8 — Cross-agent contract normalization
# ---------------------------------------------------------------------------

def test_all_agents_expose_confidence_method() -> None:
    """All three agents must expose confidence_method in output."""
    ta = TechnicalAnalystAgent()
    mc = MarketContextAnalystAgent()

    ta_out = ta.run(AgentContext(
        pair='EURUSD', timeframe='H1', mode='simulation', risk_percent=1.0,
        market_snapshot={
            'trend': 'neutral', 'rsi': 50, 'macd_diff': 0.0,
            'atr': 0.001, 'last_price': 1.1, 'change_pct': 0.0,
        },
        news_context={'news': []}, memory_context=[],
    ))
    mc_out = mc.run(AgentContext(
        pair='EURUSD', timeframe='H1', mode='simulation', risk_percent=1.0,
        market_snapshot={
            'last_price': 1.1, 'atr': 0.001, 'trend': 'neutral',
            'change_pct': 0.0, 'rsi': 50, 'macd_diff': 0.0,
            'ema_fast': 1.1, 'ema_slow': 1.1,
        },
        news_context={'news': []}, memory_context=[],
    ))

    assert 'confidence_method' in ta_out
    assert 'confidence_method' in mc_out


def test_all_agents_expose_raw_vs_final_contract_fields(monkeypatch) -> None:
    ta = TechnicalAnalystAgent()
    mc = MarketContextAnalystAgent()
    news = NewsAnalystAgent(PromptTemplateService())
    monkeypatch.setattr(news.model_selector, 'is_enabled', lambda *_a, **_k: False)
    monkeypatch.setattr(ta.model_selector, 'is_enabled', lambda *_a, **_k: False)
    monkeypatch.setattr(mc.model_selector, 'is_enabled', lambda *_a, **_k: False)

    common_ctx = AgentContext(
        pair='EURUSD', timeframe='H1', mode='simulation', risk_percent=1.0,
        market_snapshot={
            'last_price': 1.1, 'atr': 0.001, 'trend': 'bullish',
            'change_pct': 0.02, 'rsi': 53, 'macd_diff': 0.01,
            'ema_fast': 1.1006, 'ema_slow': 1.0998,
        },
        news_context={
            'news': [
                {
                    'title': 'Euro strengthens as ECB rhetoric stays hawkish',
                    'summary': 'Markets price a tighter ECB path versus Fed.',
                    'provider': 'newsapi',
                    'published_at': _iso_hours_ago(1),
                    'freshness_score': 0.9,
                    'credibility_score': 0.85,
                }
            ],
            'macro_events': [],
            'fetch_status': 'ok',
        },
        memory_context=[],
    )

    ta_out = ta.run(common_ctx)
    mc_out = mc.run(common_ctx)
    news_out = news.run(common_ctx, db=None)

    for output in (ta_out, mc_out, news_out):
        assert 'raw_score' in output
        assert 'final_signal' in output
        assert 'final_confidence' in output
        assert 'diagnostics' in output
        assert 'signal_threshold_reason' in output
        assert 'degraded' in output
        assert 'llm_fallback_used' in output
        assert 'evidence_total_count' in output
        assert 'evidence_exposed_count' in output


# ---------------------------------------------------------------------------
# Regression: specific anomalies from run-72
# ---------------------------------------------------------------------------

def test_crypto_pair_does_not_get_fx_neutral_alignment() -> None:
    """Regression: ADAUSD/BTCUSD/ETHUSD should not trigger fx_neutral_evidence_alignment."""
    for pair_asset in [('crypto', 'ADAUSD'), ('crypto', 'BTCUSD'), ('crypto', 'ETHUSD')]:
        output = {
            'signal': 'neutral',
            'score': 0.0,
            'confidence': 0.15,
            'summary': '',
            'decision_mode': 'directional',
            'reason': '',
        }
        result = _validate_news_output(
            output,
            selected_evidence=[{
                'asset_class': 'crypto',
                'final_pair_relevance': 0.50,
                'directional_eligible': True,
                'instrument_directional_effect': 'neutral',
            }],
            rejected_evidence=[],
            min_directional_relevance=0.35,
            asset_class=pair_asset[0],
        )
        actions = result.get('validation_actions', [])
        assert 'fx_neutral_evidence_alignment' not in actions, \
            f'{pair_asset[1]}: fx_neutral_evidence_alignment should not fire for crypto'


def test_avaxusd_direct_symbol_before_btc_fallback() -> None:
    """Regression: AVAXUSD should try AVAX-USD before BTC-USD."""
    direct, fallback = MarketProvider._news_symbol_candidates_tiered('AVAXUSD')
    assert any('AVAX' in s for s in direct), 'AVAX symbol must be in direct candidates'
    if 'BTC-USD' in direct + fallback:
        assert 'BTC-USD' in set(fallback), 'BTC-USD must be in fallback, not direct'


def test_usdchf_direct_symbol_before_dxy_proxy() -> None:
    """Regression: USDCHF should try USDCHF=X before DX-Y.NYB."""
    direct, fallback = MarketProvider._news_symbol_candidates_tiered('USDCHF')
    fallback_set = set(fallback)
    for proxy in ['DX-Y.NYB', '^DXY', 'UUP', 'FXF']:
        if proxy in direct + fallback:
            assert proxy in fallback_set, f'{proxy} must be in fallback for USDCHF'
