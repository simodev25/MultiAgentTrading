from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.prompt_template import PromptTemplate
from app.services.orchestrator.agents import AgentContext, NewsAnalystAgent
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


def test_news_agent_detects_french_bearish_sentiment(monkeypatch) -> None:
    service = PromptTemplateService()
    agent = NewsAnalystAgent(service)
    captured: dict[str, str | None] = {'model': None}

    def fake_chat(_system: str, _user: str, model: str | None = None) -> dict[str, str]:
        captured['model'] = model
        return {'text': 'Sentiment: baissier. Le dollar reste dominant.'}

    monkeypatch.setattr(agent.llm, 'chat', fake_chat)

    ctx = AgentContext(
        pair='EURUSD',
        timeframe='H1',
        mode='simulation',
        risk_percent=1.0,
        market_snapshot={'trend': 'bearish'},
        news_context={'news': [{'title': 'Dollar strength persists'}]},
        memory_context=[],
    )

    out = agent.run(ctx, db=None)
    assert out['signal'] == 'bearish'
    assert out['score'] == -0.2
    assert isinstance(captured['model'], str)
    assert bool(captured['model'])
