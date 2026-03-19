from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.connector_config import ConnectorConfig
from app.services.orchestrator.agents import AgentContext, MacroAnalystAgent, NewsAnalystAgent, TraderAgent


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


def test_macro_agent_applies_deterministic_skill_guardrails() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_llm_enabled': {'macro-analyst': False},
                    'agent_skills': {
                        'macro-analyst': [
                            "Ne présente une lecture directionnelle que si plusieurs éléments convergent; sinon parle d'incertitude."
                        ]
                    },
                },
            )
        )
        db.commit()

        agent = MacroAnalystAgent()
        result = agent.run(_context(), db=db)

        assert result['signal'] == 'neutral'
        assert result['reason'] == 'Skill guardrails applied (deterministic mode)'
        assert result['prompt_meta']['skills_count'] == 1


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

        assert result['signal'] == 'bearish'
        assert result['score'] < 0.0
        assert 'Deterministic skill-aware fallback' in result['summary']
        assert result['prompt_meta']['skills_count'] == 1


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

        assert result['combined_score'] == 0.24
        assert result['rationale']['decision_buy_threshold'] == 0.3
        assert result['decision'] == 'HOLD'
        assert result['prompt_meta']['skills_count'] == 2


def agent_prompt_service():
    from app.services.prompts.registry import PromptTemplateService

    return PromptTemplateService()
