from app.services.orchestrator.agents import AgentContext, NewsAnalystAgent
from app.services.prompts.registry import PromptTemplateService


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
