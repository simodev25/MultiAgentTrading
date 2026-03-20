from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.prompt_template import PromptTemplate
from app.services.orchestrator.agents import AgentContext, NewsAnalystAgent, TechnicalAnalystAgent
from app.services.prompts.registry import PromptTemplateService


def test_prompt_render_enforces_french_directive() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = PromptTemplateService()
    with Session(engine) as db:
        db.add(
            PromptTemplate(
                agent_name='news-analyst',
                version=1,
                is_active=True,
                system_prompt='You are a forex news analyst.',
                user_prompt_template='Pair: {pair}',
                notes='test',
            )
        )
        db.commit()

        rendered = service.render(
            db=db,
            agent_name='news-analyst',
            fallback_system='fallback',
            fallback_user='Pair: {pair}',
            variables={'pair': 'EURUSD'},
        )
        assert 'Réponds en français' in rendered['system_prompt']
        assert 'forex' not in rendered['system_prompt'].lower()
        assert 'multi-actifs' in rendered['system_prompt'].lower()


def test_news_agent_detects_french_bearish_sentiment(monkeypatch) -> None:
    service = PromptTemplateService()
    agent = NewsAnalystAgent(service)
    captured: dict[str, str | None] = {'model': None}

    def fake_chat(_system: str, _user: str, model: str | None = None, **_kwargs: object) -> dict[str, str]:
        captured['model'] = model
        return {'text': 'Sentiment: baissier. Le dollar reste dominant.'}

    monkeypatch.setattr(agent.llm, 'chat', fake_chat)

    ctx = AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'trend': 'bearish'},
        news_context={
            'news': [
                {'title': 'Dollar strength persists as Fed remains hawkish'},
                {'title': 'Greenback gains on risk-off flows'},
                {'title': 'US yields rise after inflation surprise'},
            ]
        },
        memory_context=[],
    )

    out = agent.run(ctx, db=None)
    assert out['signal'] == 'bearish'
    assert out['score'] < 0.0
    assert out['llm_fallback_used'] is False
    assert isinstance(captured['model'], str)
    assert bool(captured['model'])


def test_news_agent_ignores_empty_titles() -> None:
    service = PromptTemplateService()
    agent = NewsAnalystAgent(service)
    ctx = AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'trend': 'neutral'},
        news_context={'news': [{'title': ''}, {'title': '   '} ]},
        memory_context=[],
    )

    out = agent.run(ctx, db=None)
    assert out['signal'] == 'neutral'
    assert out['score'] == 0.0
    assert out['reason'] == 'No recent relevant news or macro events were available from enabled providers'
    assert out['coverage'] == 'none'
    assert out['decision_mode'] == 'no_evidence'
    assert out['llm_call_attempted'] is False
    assert out['llm_fallback_used'] is False
    assert out['llm_skipped_reason'] == 'coverage_none'
    assert out['llm_summary'] == 'LLM not called (coverage_none)'


def test_news_agent_uses_deterministic_fallback_when_llm_is_degraded(monkeypatch) -> None:
    service = PromptTemplateService()
    agent = NewsAnalystAgent(service)
    monkeypatch.setattr(
        agent.llm,
        'chat',
        lambda *_args, **_kwargs: {'text': '', 'degraded': True},
    )

    ctx = AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='live',
        risk_percent=1.0,
        market_snapshot={'trend': 'neutral'},
        news_context={
            'news': [
                {'title': 'Dollar strength persists as Fed remains hawkish'},
                {'title': 'Greenback gains on risk-off flows'},
                {'title': 'US yields rise after inflation surprise'},
            ],
            'symbol': 'EURUSD=X',
        },
        memory_context=[],
    )

    out = agent.run(ctx, db=None)
    assert out['degraded'] is False
    assert out['llm_call_attempted'] is True
    assert out['llm_fallback_used'] is True
    assert out['summary'] == 'LLM degraded for news-analyst. Deterministic skill-aware fallback used.'
    assert out['provider_symbol'] == 'EURUSD=X'
    assert out['score'] < 0.0
    assert out['signal'] == 'bearish'
    assert out['coverage'] in {'medium', 'high'}


def test_news_agent_pair_aware_fallback_for_fx_headlines(monkeypatch) -> None:
    service = PromptTemplateService()
    agent = NewsAnalystAgent(service)
    monkeypatch.setattr(
        agent.llm,
        'chat',
        lambda *_args, **_kwargs: {'text': '', 'degraded': True},
    )

    headlines = [
        {'title': 'Dollar Falls and Gold Plunges on Hawkish Global Central Banks'},
        {'title': 'Dollar holds losses as risk appetite flickers ahead of central bank meetings'},
        {'title': '7 Key Central Banks Meetings to Watch Next Week'},
    ]
    ctx = AgentContext(
        pair='EURUSD.PRO',
        timeframe='H4',
        mode='live',
        risk_percent=1.0,
        market_snapshot={'trend': 'neutral'},
        news_context={'news': headlines, 'symbol': 'EURUSD=X'},
        memory_context=[],
    )

    out = agent.run(ctx, db=None)
    assert out['llm_fallback_used'] is True
    assert out['signal'] == 'bullish'
    assert out['score'] > 0.0


def test_news_agent_uses_compact_prompt_and_generation_limits(monkeypatch) -> None:
    service = PromptTemplateService()
    agent = NewsAnalystAgent(service)
    captured: dict[str, object] = {}

    def fake_chat(_system: str, _user: str, model: str | None = None, **kwargs: object) -> dict[str, str]:
        captured['system'] = _system
        captured['user'] = _user
        captured['model'] = model
        captured['kwargs'] = kwargs
        return {'text': 'bullish', 'degraded': False}

    monkeypatch.setattr(agent.llm, 'chat', fake_chat)

    ctx = AgentContext(
        pair='EURUSD.PRO',
        timeframe='M15',
        mode='live',
        risk_percent=1.0,
        market_snapshot={'trend': 'bullish'},
        news_context={
            'symbol': 'EURUSD=X',
            'news': [
                {'title': 'Dollar Falls and Gold Plunges on Hawkish Global Central Banks', 'summary': 'Summary 1'},
                {'title': 'Sterling Rises After Bank of England Votes Unanimously to Hold Rates', 'summary': 'Summary 2'},
                {'title': 'Dollar holds losses as risk appetite flickers ahead of central bank meetings', 'summary': 'Summary 3'},
                {'title': 'BCA Research warns of sticky inflation, downgrades stocks to underweight', 'summary': 'Summary 4'},
                {'title': '7 Key Central Banks Meetings to Watch Next Week', 'summary': 'Summary 5'},
            ],
        },
        memory_context=[],
    )

    out = agent.run(ctx, db=None)
    assert out['signal'] == 'bullish'
    assert out['score'] > 0.0
    kwargs = captured.get('kwargs')
    assert isinstance(kwargs, dict)
    assert kwargs.get('max_tokens') == 96
    assert kwargs.get('temperature') == 0.1
    assert kwargs.get('request_timeout_seconds') == 45.0
    system = str(captured.get('system', ''))
    user = str(captured.get('user', ''))
    assert 'Format de sortie strict' in system
    titles_section = user.split('Titres:\n', 1)[1] if 'Titres:\n' in user else ''
    assert titles_section.count('\n- ') <= 4


def test_news_agent_retries_when_llm_returns_empty_length_response(monkeypatch) -> None:
    service = PromptTemplateService()
    agent = NewsAnalystAgent(service)
    calls: list[dict[str, object]] = []

    def fake_chat(_system: str, _user: str, model: str | None = None, **kwargs: object) -> dict[str, object]:
        calls.append({'model': model, **kwargs})
        if len(calls) == 1:
            return {
                'provider': 'ollama-cloud',
                'text': '',
                'degraded': False,
                'completion_tokens': 96,
                'raw': {
                    'done_reason': 'length',
                    'message': {'content': '', 'thinking': 'internal reasoning trace'},
                },
            }
        return {
            'provider': 'ollama-cloud',
            'text': 'bullish momentum valide',
            'degraded': False,
            'completion_tokens': 180,
            'raw': {
                'done_reason': 'stop',
                'message': {'content': 'bullish momentum valide'},
            },
        }

    monkeypatch.setattr(agent.llm, 'chat', fake_chat)

    ctx = AgentContext(
        pair='EURUSD.PRO',
        timeframe='M15',
        mode='live',
        risk_percent=1.0,
        market_snapshot={'trend': 'bullish'},
        news_context={
            'symbol': 'EURUSD=X',
            'news': [
                {'title': 'Dollar Falls on softer US yields', 'summary': 'USD weakens against majors.'},
                {'title': 'ECB officials keep hawkish tone', 'summary': 'Euro support remains firm.'},
                {'title': 'Central bank meetings keep FX volatility elevated', 'summary': 'Macro catalysts stay active.'},
            ],
        },
        memory_context=[],
    )

    out = agent.run(ctx, db=None)
    assert len(calls) == 2
    assert calls[0].get('max_tokens') == 96
    assert calls[1].get('max_tokens') == 384
    assert out['llm_retry_used'] is True
    assert out['llm_fallback_used'] is False
    assert out['llm_summary'] == 'bullish momentum valide'
    assert out['signal'] == 'bullish'


def test_news_agent_empty_llm_summary_contains_diagnostics_after_retry(monkeypatch) -> None:
    service = PromptTemplateService()
    agent = NewsAnalystAgent(service)
    call_count = {'value': 0}

    def fake_chat(_system: str, _user: str, **_kwargs: object) -> dict[str, object]:
        call_count['value'] += 1
        return {
            'provider': 'ollama-cloud',
            'text': '',
            'degraded': False,
            'completion_tokens': 96,
            'raw': {
                'done_reason': 'length',
                'message': {'content': '', 'thinking': 'internal reasoning trace'},
            },
        }

    monkeypatch.setattr(agent.llm, 'chat', fake_chat)

    ctx = AgentContext(
        pair='EURUSD.PRO',
        timeframe='M15',
        mode='live',
        risk_percent=1.0,
        market_snapshot={'trend': 'neutral'},
        news_context={
            'symbol': 'EURUSD=X',
            'news': [
                {'title': 'Dollar Falls and Gold Plunges on Hawkish Global Central Banks', 'summary': 'USD broadly weaker.'},
                {'title': 'Sterling Rises After BoE Holds Rates', 'summary': 'European currencies supported.'},
                {'title': 'Dollar holds losses before central bank meetings', 'summary': 'No clear follow-through.'},
            ],
        },
        memory_context=[],
    )

    out = agent.run(ctx, db=None)
    assert call_count['value'] == 2
    assert out['llm_retry_used'] is True
    assert out['llm_fallback_used'] is True
    assert 'empty response after retry' in out['llm_summary'].lower()
    assert 'stop_reason=length' in out['llm_summary']
    assert 'reasoning_chars=' in out['llm_summary']


def test_news_agent_exposes_summary_description_and_source_in_evidence(monkeypatch) -> None:
    service = PromptTemplateService()
    agent = NewsAnalystAgent(service)
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_args, **_kwargs: False)

    ctx = AgentContext(
        pair='EURUSD.PRO',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'trend': 'neutral'},
        news_context={
            'news': [
                {
                    'title': 'Dollar Falls and Gold Plunges on Hawkish Global Central Banks',
                    'summary': 'The dollar index is down after hawkish central bank remarks.',
                    'description': 'Energy prices and war risk kept volatility high across currencies.',
                    'publisher': 'Barchart',
                    'source_name': 'Barchart',
                    'url': 'https://finance.yahoo.com/example',
                    'published_at': '2026-03-19T14:42:49+00:00',
                    'pair_relevance': 0.65,
                    'sentiment_hint': 'bearish',
                }
            ]
        },
        memory_context=[],
    )

    out = agent.run(ctx, db=None)
    evidence = out.get('evidence', [])
    assert len(evidence) >= 1
    first = evidence[0]
    assert first.get('summary') == 'The dollar index is down after hawkish central bank remarks.'
    assert first.get('description') == 'Energy prices and war risk kept volatility high across currencies.'
    assert first.get('publisher') == 'Barchart'
    assert first.get('source_name') == 'Barchart'


def test_news_agent_keeps_score_direction_consistent_with_llm_forced_signal(monkeypatch) -> None:
    service = PromptTemplateService()
    agent = NewsAnalystAgent(service)

    monkeypatch.setattr(
        agent.llm,
        'chat',
        lambda *_args, **_kwargs: {'text': 'bearish dollar momentum dominates', 'degraded': False},
    )

    ctx = AgentContext(
        pair='EURUSD.PRO',
        timeframe='M15',
        mode='live',
        risk_percent=1.0,
        market_snapshot={'trend': 'neutral'},
        news_context={
            'symbol': 'EURUSD=X',
            'news': [
                {'title': 'Dollar falls as risk appetite returns'},
                {'title': 'Euro rises after ECB hawkish remarks'},
                {'title': 'Dollar rises on safe-haven demand'},
                {'title': 'Euro falls after weak growth outlook'},
            ],
        },
        memory_context=[],
    )

    out = agent.run(ctx, db=None)
    assert out['llm_fallback_used'] is False
    assert out['signal'] == 'bearish'
    assert out['score'] < 0.0


def test_technical_agent_respects_explicit_neutral_llm_output(monkeypatch) -> None:
    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_args, **_kwargs: 'llama3.1')
    monkeypatch.setattr(
        agent.llm,
        'chat',
        lambda *_args, **_kwargs: {
            'text': (
                "**Biais: neutral**\n\n"
                "Trend bearish mais RSI en zone de survente sans confirmation. HOLD."
            ),
            'degraded': False,
        },
    )

    ctx = AgentContext(
        pair='EURUSD',
        timeframe='M15',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={
            'degraded': False,
            'pair': 'EURUSD',
            'timeframe': 'M15',
            'last_price': 1.1460,
            'trend': 'bearish',
            'rsi': 28.0,
            'macd_diff': -0.0003,
            'atr': 0.0008,
        },
        news_context={'news': []},
        memory_context=[],
    )

    out = agent.run(ctx, db=None)
    assert out['signal'] == 'neutral'
    assert out['score'] == -0.15


def test_technical_agent_marks_empty_llm_output_as_degraded(monkeypatch) -> None:
    agent = TechnicalAnalystAgent()
    monkeypatch.setattr(agent.model_selector, 'is_enabled', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent.model_selector, 'resolve', lambda *_args, **_kwargs: 'llama3.1')
    monkeypatch.setattr(
        agent.llm,
        'chat',
        lambda *_args, **_kwargs: {'text': '   ', 'degraded': False},
    )

    ctx = AgentContext(
        pair='EURUSD',
        timeframe='M15',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={
            'degraded': False,
            'pair': 'EURUSD',
            'timeframe': 'M15',
            'last_price': 1.1460,
            'trend': 'bullish',
            'rsi': 45.0,
            'macd_diff': 0.0003,
            'atr': 0.0008,
        },
        news_context={'news': []},
        memory_context=[],
    )

    out = agent.run(ctx, db=None)
    assert out['degraded'] is True
