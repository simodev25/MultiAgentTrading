from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.connector_config import ConnectorConfig
from app.services.orchestrator.agents import AgentContext, MarketContextAnalystAgent, NewsAnalystAgent, TechnicalAnalystAgent, TraderAgent


def _context() -> AgentContext:
    return AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'last_price': 1.1, 'atr': 0.001, 'trend': 'bullish', 'change_pct': 0.15, 'rsi': 50, 'macd_diff': 0.1},
        news_context={'news': [{'title': 'Dollar falls as recession fears rise'}]},
        memory_context=[],
    )


def test_market_context_agent_applies_deterministic_skill_guardrails() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_llm_enabled': {'market-context-analyst': False},
                    'agent_skills': {
                        'market-context-analyst': [
                            "Ne présente une lecture directionnelle que si plusieurs éléments convergent; sinon parle d'incertitude."
                        ]
                    },
                },
            )
        )
        db.commit()

        agent = MarketContextAnalystAgent()
        result = agent.run(_context(), db=db)

        assert result['signal'] in {'bullish', 'neutral'}
        assert result['regime'] in {'trending', 'ranging', 'unstable', 'calm', 'volatile'}
        assert result['momentum_bias'] in {'bullish', 'bearish', 'neutral'}
        assert result['volatility_context'] in {'supportive', 'unsupportive', 'neutral'}
        assert result['llm_summary'].startswith(result['signal'])
        assert result['prompt_meta']['skills_count'] == 1
        assert result['tooling']['llm_tool_calls']
        assert result['tooling']['llm_tool_calls'][0]['source'] == 'runtime_preload'


def test_technical_agent_injects_tools_into_llm_and_executes_tool_calls(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_llm_enabled': {'technical-analyst': True},
                },
            )
        )
        db.commit()

        agent = TechnicalAnalystAgent()
        seen_payloads: list[dict[str, object]] = []

        def fake_chat(_system: str, _user: str, **kwargs):
            seen_payloads.append(dict(kwargs))
            if len(seen_payloads) == 1:
                return {
                    'text': '',
                    'degraded': False,
                    'tool_calls': [
                        {
                            'id': 'call_market_snapshot',
                            'name': 'market_snapshot',
                            'arguments': {},
                        }
                    ],
                }
            return {
                'text': (
                    'bullish\n'
                    'setup_quality=medium\n'
                    'validation=ok\n'
                    'invalidation=ko\n'
                    'evidence_used=indicator_bundle,market_snapshot'
                ),
                'degraded': False,
            }

        monkeypatch.setattr(agent.llm, 'chat', fake_chat)
        result = agent.run(_context(), db=db)

        assert seen_payloads
        assert isinstance(seen_payloads[0].get('tools'), list)
        assert seen_payloads[0].get('tool_choice') == 'required'
        assert result['tooling']['llm_tool_calls']
        assert result['tooling']['llm_tool_calls'][0]['name'] == 'market_snapshot'
        invocations = result['tooling']['invocations']
        assert invocations['market_snapshot']['llm_invocations'][0]['status'] == 'ok'


def test_news_agent_uses_skill_aware_fallback_when_llm_disabled() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_llm_enabled': {'news-analyst': False},
                    'agent_skills': {
                        'news-analyst': [
                            'Réduis le poids des titres vagues et privilégie les signaux crédibles.',
                        ]
                    },
                },
            )
        )
        db.commit()

        agent = NewsAnalystAgent(prompt_service=agent_prompt_service())
        result = agent.run(_context(), db=db)

        assert result['signal'] == 'neutral'
        assert abs(result['score']) <= 0.05
        assert result['summary'].startswith('neutral')
        assert result['decision_mode'] == 'neutral_from_low_relevance'
        assert result['prompt_meta']['skills_count'] == 1
        assert result['tooling']['llm_tool_calls']
        assert result['tooling']['llm_tool_calls'][0]['source'] == 'runtime_preload'


def test_news_agent_respects_disabled_news_search_tool() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_llm_enabled': {'news-analyst': False},
                    'agent_tools': {
                        'news-analyst': {
                            'news_search': False,
                            'macro_calendar_or_event_feed': True,
                            'symbol_relevance_filter': True,
                            'sentiment_or_event_impact_parser': True,
                        }
                    },
                },
            )
        )
        db.commit()

        agent = NewsAnalystAgent(prompt_service=agent_prompt_service())
        result = agent.run(_context(), db=db)

        assert result['news_count'] == 0
        assert result['signal'] == 'neutral'
        invocations = (result.get('tooling') or {}).get('invocations') or {}
        assert invocations.get('news_search', {}).get('status') == 'disabled'


def test_news_agent_timeout_case_matches_live_payload(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_llm_enabled': {'news-analyst': True},
                    'agent_skills': {
                        'news-analyst': [
                            "Pondère chaque news selon son impact probable sur la devise de base et la devise de cotation du pair, pas selon son importance médiatique générale.",
                            "Donne la priorité aux banques centrales, inflation, emploi, croissance, matières premières et géopolitique lorsqu'ils ont un lien crédible avec le pair analysé.",
                            "Réduis le poids des titres vagues, repris sans confirmation ou trop éloignés du marché FX; une news peu spécifique ne doit pas forcer une direction.",
                            "Accorde plus de valeur aux news récentes et aux événements encore actifs dans le pricing; les anciennes news servent surtout de contexte.",
                            "Utilise la mémoire comme toile de fond pour la continuité narrative, jamais comme preuve supérieure à une news fraîche et pertinente.",
                        ]
                    },
                },
            )
        )
        db.commit()

        agent = NewsAnalystAgent(prompt_service=agent_prompt_service())
        monkeypatch.setattr(
            agent.llm,
            'chat',
            lambda *_args, **_kwargs: {
                'text': 'Ollama call failed after retries: The read operation timed out',
                'degraded': True,
            },
        )

        ctx = AgentContext(
            pair='EURUSD.PRO',
            timeframe='M15',
            mode='live',
            risk_percent=1.0,
            market_snapshot={'trend': 'bullish'},
            news_context={
                'symbol': 'EURUSD=X',
                'symbols_scanned': ['EURUSD=X'],
                'news': [
                    {'title': 'Dollar Falls and Gold Plunges on Hawkish Global Central Banks'},
                    {'title': 'Sterling Rises After Bank of England Votes Unanimously to Hold Rates'},
                    {'title': 'Dollar holds losses as risk appetite flickers ahead of central bank meetings'},
                    {'title': 'BCA Research warns of sticky inflation, downgrades stocks to underweight'},
                    {'title': '7 Key Central Banks Meetings to Watch Next Week'},
                ],
            },
            memory_context=[],
        )

        result = agent.run(ctx, db=db)

        assert result['signal'] == 'neutral'
        assert abs(result['score']) <= 0.05
        assert result['degraded'] is False
        assert result['llm_fallback_used'] is False
        assert result['llm_call_attempted'] is False
        assert 'coverage_low' in result['llm_summary'].lower()
        assert result['summary'].startswith('neutral')
        assert result['provider_symbol'] == 'EURUSD=X'
        assert result['provider_symbols_scanned'] == ['EURUSD=X']
        assert result['coverage'] == 'low'
        assert result['decision_mode'] == 'neutral_from_low_relevance'
        assert result['fetch_status'] == 'ok'
        assert result['prompt_meta']['skills_count'] == 5


def test_trader_agent_uses_skill_hold_guardrail_when_llm_disabled() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_llm_enabled': {'trader-agent': False},
                    'decision_mode': 'conservative',
                    'agent_skills': {
                        'trader-agent': [
                            "Synthétise l'ensemble en BUY, SELL ou HOLD; HOLD est la réponse par défaut quand l'avantage n'est pas net.",
                            'Ne transforme jamais un signal isolé en décision exécutable; exige une convergence raisonnable.',
                        ]
                    },
                },
            )
        )
        db.commit()

        agent = TraderAgent()
        ctx = _context()
        outputs = {
            'technical': {'score': 0.21},
            'news': {'score': 0.02},
            'macro': {'score': 0.01},
        }
        bullish = {'arguments': ['x'], 'confidence': 0.5}
        bearish = {'arguments': ['y'], 'confidence': 0.5}

        result = agent.run(ctx, outputs, bullish, bearish, db=db)

        assert 0.24 <= result['combined_score'] <= 0.33
        assert result['rationale']['decision_buy_threshold'] == 0.32
        assert result['decision'] == 'HOLD'
        assert result['prompt_meta']['skills_count'] == 2


def agent_prompt_service():
    from app.services.prompts.registry import PromptTemplateService

    return PromptTemplateService()
