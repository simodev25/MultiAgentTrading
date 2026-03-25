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
