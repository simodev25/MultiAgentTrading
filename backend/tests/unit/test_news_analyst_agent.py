import json
from pathlib import Path

import pytest

from app.services.market.news_provider import MarketProvider
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
                    'title': "Coinbase CEO offers bold fix after billionaire Ray Dalio's dollar warning",
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


def test_news_analyst_prefers_explicit_llm_score_and_confidence_over_deterministic_blend(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _news_context()
    _configure_llm(
        agent,
        monkeypatch,
        (
            'bearish\n'
            'case=directional_signal\n'
            'score=-0.34\n'
            'confidence=0.27\n'
            'horizon=swing\n'
            'impact=medium\n'
            'Dollar repricing dominates the retained EUR headlines.'
        ),
        enabled=True,
    )

    out = agent.run(ctx, db=None)

    assert out['signal'] == 'bearish'
    assert out['score'] == -0.34
    assert out['confidence'] == 0.27
    assert out['confidence_method'] == 'llm_direct'


def test_news_analyst_hides_deterministic_directional_diagnostics_from_public_output(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _news_context()
    _configure_llm(
        agent,
        monkeypatch,
        (
            'bearish\n'
            'case=directional_signal\n'
            'score=-0.31\n'
            'confidence=0.41\n'
            'horizon=intraday\n'
            'impact=medium\n'
            'ETF outflows and defensive BTC positioning dominate.'
        ),
        enabled=True,
    )

    out = agent.run(ctx, db=None)

    assert 'raw_score' not in out
    assert 'directional_evidence_count' not in out
    assert out['evidence']
    first = out['evidence'][0]
    for forbidden_key in (
        'sentiment_hint',
        'directional_eligible',
        'signal_case',
        'instrument_directional_effect',
        'instrument_bias_score',
        'impact_on_base',
        'impact_on_quote',
        'pair_directional_effect',
    ):
        assert forbidden_key not in first


def test_news_analyst_never_claims_macro_contribution_when_macro_counts_are_zero(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _news_context()
    _configure_llm(agent, monkeypatch, 'neutral\ncase=no_signal\nLLM disabled for deterministic test.', enabled=False)

    out = agent.run(ctx, db=None)

    assert out['signal'] == 'bullish'
    assert out['macro_event_count'] == 0
    assert out['retained_macro_event_count'] == 0
    combined_text = f"{out.get('reason', '')} {out.get('summary', '')}".lower()
    assert 'news and macro evidence produced' not in combined_text
    assert 'macro evidence produced' not in combined_text
    assert out.get('macro_integration_status') in {'disabled', 'enabled_no_events', 'unavailable'}


def test_news_analyst_marks_macro_integration_disabled_when_provider_disabled(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _news_context()
    ctx.news_context['provider_status_compact'] = {'newsapi': 'ok', 'tradingeconomics': 'disabled'}
    _configure_llm(agent, monkeypatch, 'neutral\ncase=no_signal\nLLM disabled for deterministic test.', enabled=False)

    out = agent.run(ctx, db=None)

    assert out['macro_event_count'] == 0
    assert out['retained_macro_event_count'] == 0
    assert out.get('macro_integration_status') == 'disabled'


def test_news_analyst_keeps_infra_429_text_out_of_primary_summary(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = _news_context()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_args, **_kwargs: 'dummy-model')
    monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_args, **_kwargs: 'conservative')
    monkeypatch.setattr(
        agent.llm,
        'chat',
        lambda *_args, **_kwargs: {
            'text': 'OpenAI 429 Too Many Requests after retries',
            'degraded': True,
            'provider': 'openai',
        },
    )

    out = agent.run(ctx, db=None)

    assert out['llm_fallback_used'] is True
    assert out['degraded'] is True
    assert '429' not in str(out.get('summary', '')).lower()
    diagnostics = out.get('diagnostics')
    assert isinstance(diagnostics, dict)
    assert '429' in str(diagnostics).lower()


def test_news_analyst_degraded_llm_fallback_uses_deterministic_confidence_method(monkeypatch) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    ctx = AgentContext(
        pair='BTCUSD',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'trend': 'neutral', 'last_price': 70000.0, 'atr': 500.0},
        news_context={
            'news': [
                {
                    'title': 'Bitcoin ETF inflows rebound strongly',
                    'summary': 'BTC demand from US spot ETFs accelerates.',
                    'provider': 'yahoo_finance',
                    'sentiment_hint': 'bullish',
                    'pair_relevance': 0.82,
                    'base_currency_relevance': 0.82,
                    'quote_currency_relevance': 0.05,
                    'freshness_score': 0.90,
                    'credibility_score': 0.85,
                    'published_at': '2026-03-26T10:00:00+00:00',
                    'source_symbol': 'BTC-USD',
                },
                {
                    'title': 'Bitcoin miners increase selling pressure',
                    'summary': 'Large miner outflows to exchanges weigh on BTC price action.',
                    'provider': 'yahoo_finance',
                    'sentiment_hint': 'bearish',
                    'pair_relevance': 0.81,
                    'base_currency_relevance': 0.81,
                    'quote_currency_relevance': 0.05,
                    'freshness_score': 0.88,
                    'credibility_score': 0.84,
                    'published_at': '2026-03-26T10:05:00+00:00',
                    'source_symbol': 'BTC-USD',
                },
            ],
            'macro_events': [],
            'fetch_status': 'ok',
            'provider_status_compact': {'yahoo_finance': 'ok'},
            'symbol': 'BTC-USD',
            'selected_news_symbol': 'BTC-USD',
        },
        memory_context=[],
    )

    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_args, **_kwargs: 'dummy-model')
    monkeypatch.setattr(agent.model_selector, 'resolve_decision_mode', lambda *_args, **_kwargs: 'conservative')
    monkeypatch.setattr(
        agent.llm,
        'chat',
        lambda *_args, **_kwargs: {
            'text': "Ollama call failed after retries: Client error '400 Bad Request' for url 'https://ollama.com/api/chat'",
            'degraded': True,
            'provider': 'fallback',
        },
    )

    out = agent.run(ctx, db=None)

    assert out['signal'] == 'neutral'
    assert out['llm_call_attempted'] is True
    assert out['llm_semantic_mode'] is True
    assert out['llm_fallback_used'] is True
    assert out['degraded'] is True
    assert out['confidence_method'] == 'deterministic_fallback'
    assert out['confidence'] <= 0.35


@pytest.mark.parametrize(
    ('pair', 'provider_symbol', 'selected_symbol', 'forbidden_primary'),
    [
        ('USDCHF.PRO', 'USDCHF=X', 'DX-Y.NYB', ('DX-Y.NYB', '^GSPC', 'BTC-USD', 'BNZL', 'USDCHF')),
        ('USDCAD.PRO', 'USDCAD=X', 'DX-Y.NYB', ('DX-Y.NYB', '^GSPC', 'BTC-USD', 'BNZL', 'USDCAD')),
        ('NZDUSD.PRO', 'NZDUSD=X', 'BNZL', ('DX-Y.NYB', '^GSPC', 'BTC-USD', 'BNZL', 'NZDUSD')),
        ('EURJPY.PRO', 'EURJPY=X', '^GSPC', ('DX-Y.NYB', '^GSPC', 'BTC-USD', 'BNZL', 'EURJPY')),
        ('GBPJPY.PRO', 'GBPJPY=X', '^GSPC', ('DX-Y.NYB', '^GSPC', 'BTC-USD', 'BNZL', 'GBPJPY')),
        ('EURGBP.PRO', 'EURGBP=X', '^GSPC', ('DX-Y.NYB', '^GSPC', 'BTC-USD', 'BNZL', 'EURGBP')),
        ('AVAXUSD', 'AVAX-USD', 'BTC-USD', ('DX-Y.NYB', '^GSPC', 'BTC-USD', 'BNZL', 'AVAX', 'AVAXUSD')),
        ('BCHUSD', 'BCH-USD', 'BTC-USD', ('DX-Y.NYB', '^GSPC', 'BTC-USD', 'BNZL', 'BCH', 'BCHUSD')),
        ('DOTUSD', 'DOT-USD', 'BTC-USD', ('DX-Y.NYB', '^GSPC', 'BTC-USD', 'BNZL', 'DOT', 'DOTUSD')),
        ('LTCUSD', 'LTC-USD', 'BTC-USD', ('DX-Y.NYB', '^GSPC', 'BTC-USD', 'BNZL', 'LTC', 'LTCUSD')),
        ('MATICUSD', 'MATIC-USD', 'BTC-USD', ('DX-Y.NYB', '^GSPC', 'BTC-USD', 'BNZL', 'MATIC', 'MATICUSD')),
        ('UNIUSD', 'UNI-USD', 'BTC-USD', ('DX-Y.NYB', '^GSPC', 'BTC-USD', 'BNZL', 'UNI', 'UNIUSD')),
    ],
)
def test_news_analyst_keeps_exact_provider_symbol_in_output_contract(
    monkeypatch,
    pair: str,
    provider_symbol: str,
    selected_symbol: str,
    forbidden_primary: tuple[str, ...],
) -> None:
    agent = NewsAnalystAgent(PromptTemplateService())
    _configure_llm(agent, monkeypatch, 'neutral\ncase=no_signal\nLLM disabled for deterministic test.', enabled=False)

    ctx = AgentContext(
        pair=pair,
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'trend': 'neutral', 'last_price': 1.0, 'atr': 0.001},
        news_context={
            'news': [
                {
                    'title': 'Proxy headline used as secondary context',
                    'summary': 'Secondary correlated symbol headline.',
                    'provider': 'yahoo_finance',
                    'source_symbol': selected_symbol,
                    'published_at': '2026-03-22T08:00:00+00:00',
                    'freshness_score': 0.8,
                    'credibility_score': 0.8,
                }
            ],
            'macro_events': [],
            'fetch_status': 'ok',
            'symbol': provider_symbol,
            'selected_news_symbol': selected_symbol,
            'provider_status_compact': {'yahoo_finance': 'ok'},
        },
        memory_context=[],
    )

    out = agent.run(ctx, db=None)

    assert out['provider_symbol'] == provider_symbol
    assert out['provider_symbol'] != selected_symbol
    assert out['provider_symbol'] not in set(forbidden_primary)


@pytest.mark.parametrize(
    ('pair', 'expected_primary_symbol', 'fallback_symbol'),
    [
        ('USDCHF.PRO', 'USDCHF=X', 'DX-Y.NYB'),
        ('USDCAD.PRO', 'USDCAD=X', 'DX-Y.NYB'),
        ('NZDUSD.PRO', 'NZDUSD=X', 'BNZL'),
        ('EURJPY.PRO', 'EURJPY=X', '^GSPC'),
        ('GBPJPY.PRO', 'GBPJPY=X', '^GSPC'),
        ('EURGBP.PRO', 'EURGBP=X', '^GSPC'),
        ('AVAXUSD', 'AVAX-USD', 'BTC-USD'),
        ('BCHUSD', 'BCH-USD', 'BTC-USD'),
        ('DOTUSD', 'DOT-USD', 'BTC-USD'),
        ('LTCUSD', 'LTC-USD', 'BTC-USD'),
        ('MATICUSD', 'MATIC-USD', 'BTC-USD'),
        ('UNIUSD', 'UNI-USD', 'BTC-USD'),
    ],
)
def test_news_analyst_serializes_exact_provider_symbol_from_provider_context(
    monkeypatch,
    pair: str,
    expected_primary_symbol: str,
    fallback_symbol: str,
) -> None:
    provider = MarketProvider()
    provider.settings.yfinance_cache_enabled = False
    provider._redis = None
    provider.settings.news_providers = {
        'yahoo_finance': {'enabled': True, 'priority': 100},
        'newsapi': {'enabled': False},
        'tradingeconomics': {'enabled': False},
        'finnhub': {'enabled': False},
        'alphavantage': {'enabled': False},
        'llm_search': {'enabled': False},
    }

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def news(self):
            if self.symbol == fallback_symbol:
                return [
                    {
                        'title': 'Proxy headline',
                        'publisher': 'unit',
                        'link': 'https://example.com/proxy',
                        'providerPublishTime': _epoch_hours_ago(1),
                    }
                ]
            return []

    monkeypatch.setattr('app.services.market.news_provider.yf.Ticker', _FakeTicker)

    agent = NewsAnalystAgent(PromptTemplateService())
    _configure_llm(agent, monkeypatch, 'neutral\ncase=no_signal\nLLM disabled for deterministic test.', enabled=False)
    out = agent.run(
        AgentContext(
            pair=pair,
            timeframe='H1',
            mode='simulation',
            risk_percent=1.0,
            market_snapshot={'trend': 'neutral', 'last_price': 1.0, 'atr': 0.001},
            news_context=provider.get_news_context(pair, limit=5),
            memory_context=[],
        ),
        db=None,
    )

    assert out['provider_symbol'] == expected_primary_symbol
    assert out['provider_symbol'] != fallback_symbol


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
    # With high-relevance evidence (0.82), score is compressed but not zeroed
    assert abs(output['score']) <= 0.05
    # Confidence scales with relevance quality instead of hard cap at 0.18
    assert output['confidence'] <= 0.50
    assert 'directional_evidence_count' not in output
    # High-relevance fx evidence (>=0.60) is no longer force-classified as fx_neutral_only
    assert output['decision_mode'] in ('neutral_from_low_relevance', 'neutral_from_mixed_news')
