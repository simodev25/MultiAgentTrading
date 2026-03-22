import json
from pathlib import Path

import pytest

from app.services.orchestrator.agents import AgentContext, NewsAnalystAgent, _validate_news_output
from app.services.prompts.registry import PromptTemplateService


DEBUG_TRACE_DIR = Path(__file__).resolve().parents[2] / 'debug-traces'


def _news_context() -> AgentContext:
    news_items = [
        {
            'title': 'EUR rises after hawkish ECB remarks',
            'summary': 'Markets price higher euro rates path.',
            'provider': 'newsapi',
            'sentiment_hint': 'bullish',
            'pair_relevance': 0.95,
            'base_currency_relevance': 0.95,
            'quote_currency_relevance': 0.05,
            'freshness_score': 0.9,
            'credibility_score': 0.9,
            'published_at': '2026-03-21T10:00:00+00:00',
        },
        {
            'title': 'ECB official signals restrictive stance',
            'summary': 'Inflation persistence keeps policy tight.',
            'provider': 'newsapi',
            'sentiment_hint': 'bullish',
            'pair_relevance': 0.9,
            'base_currency_relevance': 0.9,
            'quote_currency_relevance': 0.1,
            'freshness_score': 0.88,
            'credibility_score': 0.85,
            'published_at': '2026-03-21T10:05:00+00:00',
        },
        {
            'title': 'Euro demand strengthens on policy divergence',
            'summary': 'Relative central-bank tone supports EUR over USD.',
            'provider': 'newsapi',
            'sentiment_hint': 'bullish',
            'pair_relevance': 0.92,
            'base_currency_relevance': 0.9,
            'quote_currency_relevance': 0.1,
            'freshness_score': 0.9,
            'credibility_score': 0.86,
            'published_at': '2026-03-21T10:10:00+00:00',
        },
    ]

    return AgentContext(
        pair='EURUSD',
        timeframe='M5',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'trend': 'bullish', 'last_price': 1.1, 'atr': 0.001},
        news_context={
            'news': news_items,
            'macro_events': [],
            'fetch_status': 'ok',
            'provider_status_compact': {'newsapi': 'ok'},
        },
        memory_context=[],
    )


def _crypto_noise_context(pair: str) -> AgentContext:
    return AgentContext(
        pair=pair,
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'trend': 'neutral', 'last_price': 100.0, 'atr': 1.0},
        news_context={
            'news': [
                {
                    'title': 'Why is oil priced in dollars?',
                    'summary': 'The long-standing hegemony of the U.S. dollar in energy markets is facing reassessment.',
                    'provider': 'yahoo_finance',
                    'pair_relevance': 0.44,
                    'quote_currency_relevance': 0.55,
                    'macro_relevance': 0.7,
                    'freshness_score': 0.8,
                    'credibility_score': 0.7,
                    'sentiment_hint': 'unknown',
                    'source_symbol': 'DX-Y.NYB',
                },
                {
                    'title': 'Coinbase CEO offers bold fix after billionaire Ray Dalio’s dollar warning',
                    'summary': 'Macro investors warn about the dollar while crypto executives discuss the broader market.',
                    'provider': 'yahoo_finance',
                    'pair_relevance': 0.44,
                    'quote_currency_relevance': 0.55,
                    'macro_relevance': 0.7,
                    'freshness_score': 0.8,
                    'credibility_score': 0.7,
                    'sentiment_hint': 'unknown',
                    'source_symbol': 'DX-Y.NYB',
                },
            ],
            'macro_events': [],
            'fetch_status': 'ok',
            'provider_status_compact': {'yahoo_finance': 'ok'},
            'symbol': 'DX-Y.NYB',
        },
        memory_context=[],
    )


def _fx_pair_context(pair: str, title: str, summary: str) -> AgentContext:
    return AgentContext(
        pair=pair,
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'trend': 'neutral', 'last_price': 1.0, 'atr': 0.001},
        news_context={
            'news': [
                {
                    'title': title,
                    'summary': summary,
                    'provider': 'newsapi',
                    'published_at': '2026-03-22T08:00:00+00:00',
                    'freshness_score': 0.9,
                    'credibility_score': 0.82,
                }
            ],
            'macro_events': [],
            'fetch_status': 'ok',
            'provider_status_compact': {'newsapi': 'ok'},
        },
        memory_context=[],
    )


def _context_from_debug_trace(filename: str) -> AgentContext:
    trace_path = DEBUG_TRACE_DIR / filename
    if not trace_path.exists():
        fallback_pairs = {
            'run-9-20260321T212538Z.json': 'LTCUSD',
            'run-10-20260321T212658Z.json': 'DOTUSD',
        }
        fallback_pair = fallback_pairs.get(filename)
        if fallback_pair:
            return _crypto_noise_context(fallback_pair)
        raise FileNotFoundError(trace_path)

    data = json.loads(trace_path.read_text(encoding='utf-8'))
    context = data['context']
    run = data['run']
    return AgentContext(
        pair=run['pair'],
        timeframe=run['timeframe'],
        mode=run['mode'],
        risk_percent=float(run.get('risk_percent', 1.0) or 1.0),
        market_snapshot=context.get('market_snapshot', {}),
        news_context=context.get('news_context', {}),
        memory_context=context.get('memory_context', []),
        memory_signal=context.get('memory_signal', {}) if isinstance(context.get('memory_signal'), dict) else {},
    )


def _configure_llm(agent: NewsAnalystAgent, monkeypatch, text: str, *, enabled: bool = True) -> None:
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_args, **_kwargs: enabled)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_args, **_kwargs: 'dummy-model')
    monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_args, **_kwargs: 'conservative')
    monkeypatch.setattr(
        agent.llm,
        'chat',
        lambda *_args, **_kwargs: {
            'text': text,
            'degraded': False,
            'provider': 'unit-test',
        },
    )


def test_news_analyst_opens_llm_circuit_after_repeated_empty_llm_responses(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _news_context()

    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_args, **_kwargs: 'dummy-model')
    monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_args, **_kwargs: 'conservative')

    def _empty_llm_response(*_args, **_kwargs):
        return {
            'text': '',
            'degraded': False,
            'provider': 'ollama-cloud',
            'stop_reason': 'stop',
            'usage': {'completion_tokens': 96},
        }

    monkeypatch.setattr(agent.llm, 'chat', _empty_llm_response)

    for _ in range(3):
        out = agent.run(ctx, db=None)
        assert out['llm_call_attempted'] is True
        assert out['llm_fallback_used'] is True

    out = agent.run(ctx, db=None)
    assert out['llm_call_attempted'] is False
    assert out['llm_skipped_reason'] == 'llm_circuit_open'
    assert out['llm_circuit_open'] is True


def test_news_analyst_blocks_directional_signal_when_only_indirect_crypto_noise(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _crypto_noise_context('LTCUSD')
    _configure_llm(agent, monkeypatch, 'neutral\ncase=no_signal\nAucune news pertinente pour LTCUSD.', enabled=True)

    out = agent.run(ctx, db=None)

    assert out['signal'] == 'neutral'
    assert abs(out['score']) <= 0.05
    assert out['confidence'] <= 0.22
    assert out['signal_contract_case'] in {'no_signal', 'weak_signal'}
    assert out['retained_news_count'] == 0


def test_news_analyst_validator_aligns_neutral_summary_with_structured_output(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _news_context()
    _configure_llm(agent, monkeypatch, 'neutral\ncase=no_signal\nAucune news pertinente pour EURUSD.', enabled=True)

    out = agent.run(ctx, db=None)

    assert out['signal'] == 'neutral'
    assert abs(out['score']) <= 0.05
    assert out['signal_contract_case'] == 'no_signal'
    assert out['summary'].startswith('neutral')


def test_news_analyst_regression_ltcusd_run_no_longer_returns_bullish_noise(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _context_from_debug_trace('run-9-20260321T212538Z.json')
    _configure_llm(agent, monkeypatch, 'neutral\ncase=no_signal\nAucune news pertinente pour LTCUSD.', enabled=True)

    out = agent.run(ctx, db=None)

    assert out['signal'] == 'neutral'
    assert abs(out['score']) <= 0.05
    assert out['retained_news_count'] <= 1
    assert out['summary'].startswith('neutral')


def test_news_analyst_regression_dotusd_run_no_longer_returns_bullish_noise(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _context_from_debug_trace('run-10-20260321T212658Z.json')
    _configure_llm(agent, monkeypatch, 'neutral\ncase=no_signal\nAucune news pertinente pour DOTUSD.', enabled=True)

    out = agent.run(ctx, db=None)

    assert out['signal'] == 'neutral'
    assert abs(out['score']) <= 0.05
    assert out['retained_news_count'] <= 1
    assert out['summary'].startswith('neutral')


def test_news_analyst_keeps_directional_signal_for_clean_fx_catalysts(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _news_context()
    _configure_llm(agent, monkeypatch, 'bullish\ncase=directional_signal\nECB hawkish headlines directly support EUR over USD.', enabled=True)

    out = agent.run(ctx, db=None)

    assert out['signal'] == 'bullish'
    assert out['score'] > 0.10
    assert out['signal_contract_case'] == 'directional_signal'
    assert out['retained_news_count'] >= 2


@pytest.mark.parametrize(
    ('pair', 'expected_signal'),
    [
        ('EURUSD', 'bullish'),
        ('GBPUSD', 'bullish'),
        ('AUDUSD', 'bullish'),
        ('NZDUSD', 'bullish'),
        ('USDJPY', 'bearish'),
        ('USDCHF', 'bearish'),
        ('USDCAD', 'bearish'),
    ],
)
def test_news_analyst_maps_usd_weakness_generically_by_pair_structure(monkeypatch, pair: str, expected_signal: str) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _fx_pair_context(
        pair,
        'Dollar falls after soft US CPI as Fed turns dovish',
        'USD weakens after cooler inflation and lower Treasury yields.',
    )
    _configure_llm(agent, monkeypatch, 'neutral\ncase=no_signal\nLLM disabled for deterministic test.', enabled=False)

    out = agent.run(ctx, db=None)

    assert out['signal'] == expected_signal
    assert out['score'] > 0.10 if expected_signal == 'bullish' else out['score'] < -0.10
    assert out['evidence'][0]['pair_directional_effect'] == expected_signal


@pytest.mark.parametrize(
    ('pair', 'title', 'summary', 'expected_signal'),
    [
        ('EURGBP', 'Sterling rises after hawkish Bank of England remarks', 'GBP strengthens as markets price tighter policy.', 'bearish'),
        ('AUDNZD', 'Aussie rallies after hawkish RBA surprise', 'AUD strengthens on a higher rates path.', 'bullish'),
        ('EURCHF', 'Swiss franc firms as SNB stays restrictive', 'CHF strengthens on safe-haven demand and tighter policy.', 'bearish'),
    ],
)
def test_news_analyst_handles_cross_pairs_without_symbol_hardcode(
    monkeypatch,
    pair: str,
    title: str,
    summary: str,
    expected_signal: str,
) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _fx_pair_context(pair, title, summary)
    _configure_llm(agent, monkeypatch, 'neutral\ncase=no_signal\nLLM disabled for deterministic test.', enabled=False)

    out = agent.run(ctx, db=None)

    assert out['signal'] == expected_signal
    assert out['evidence'][0]['pair_directional_effect'] == expected_signal


def test_news_analyst_downshifts_ambiguous_fx_news_to_neutral(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _fx_pair_context(
        'EURUSD',
        'ECB hawkish while Fed stays hawkish ahead of inflation data',
        'Euro and dollar both draw support from sticky inflation and central-bank caution.',
    )
    _configure_llm(agent, monkeypatch, 'neutral\ncase=weak_signal\nMixed EUR and USD catalysts keep the pair balanced.', enabled=False)

    out = agent.run(ctx, db=None)

    assert out['signal'] == 'neutral'
    assert abs(out['score']) <= 0.10
    assert out['confidence'] <= 0.45
    assert out['evidence'][0]['pair_directional_effect'] == 'neutral'


def test_news_analyst_downweights_vague_fx_macro_noise(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _fx_pair_context(
        'EURUSD',
        'Global geopolitical risks keep investors cautious',
        'The article describes broad market sentiment without explicit currency transmission.',
    )
    _configure_llm(agent, monkeypatch, 'neutral\ncase=no_signal\nAucune news pertinente pour EURUSD.', enabled=False)

    out = agent.run(ctx, db=None)

    assert out['signal'] == 'neutral'
    assert abs(out['score']) <= 0.05
    assert out['retained_news_count'] == 0


def test_validate_news_output_removes_hidden_directional_push_from_neutral_fx_output() -> None:
    selected_evidence = [
        {
            'asset_class': 'fx',
            'directional_eligible': True,
            'final_pair_relevance': 0.82,
            'pair_directional_effect': 'neutral',
            'impact_on_base': 'unknown',
            'impact_on_quote': 'unknown',
            'base_currency_effect': 'unknown',
            'quote_currency_effect': 'unknown',
        }
        for _ in range(5)
    ]

    output = _validate_news_output(
        {
            'signal': 'neutral',
            'score': 0.363,
            'confidence': 0.822,
            'decision_mode': 'directional',
            'information_state': 'clear_directional_bias',
            'reason': 'Relevant news and macro evidence produced a directional edge',
            'summary': 'neutral\nAucun signal macro dominant',
            'llm_summary': '',
        },
        selected_evidence=selected_evidence,
        rejected_evidence=[],
        min_directional_relevance=0.55,
    )

    assert output['signal'] == 'neutral'
    assert output['score'] == 0.0
    assert output['confidence'] <= 0.18
    assert output['directional_evidence_count'] == 0
    assert output['decision_mode'] == 'neutral_from_low_relevance'
    assert output['reason'] == 'Retained FX evidence did not produce any directional pair effect.'
