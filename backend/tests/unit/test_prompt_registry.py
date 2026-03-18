from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.connector_config import ConnectorConfig
from app.db.models.prompt_template import PromptTemplate
from app.db.models.user import User  # noqa: F401
from app.services.prompts.registry import PromptTemplateService


def test_prompt_registry_version_activation() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = PromptTemplateService()
    with Session(engine) as db:
        service.seed_defaults(db)
        schedule_prompt = service.get_active(db, 'schedule-planner-agent')
        assert schedule_prompt is not None
        assert schedule_prompt.version >= 1

        created = service.create_version(
            db=db,
            agent_name='bullish-researcher',
            system_prompt='system v2',
            user_prompt_template='user {pair}',
            notes='test',
            created_by_id=None,
        )
        assert created.version >= 2

        activated = service.activate(db, created.id)
        assert activated is not None
        assert activated.is_active is True

        active = service.get_active(db, 'bullish-researcher')
        assert active is not None
        assert active.id == created.id

        rows = db.query(PromptTemplate).filter(PromptTemplate.agent_name == 'bullish-researcher').all()
        assert sum(1 for row in rows if row.is_active) == 1


def test_prompt_registry_render_appends_agent_skills() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    service = PromptTemplateService()
    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_skills': {
                        'news-analyst': ['Prioriser impact Forex', 'Citer les risques'],
                    },
                },
            )
        )
        db.commit()

        rendered = service.render(
            db=db,
            agent_name='news-analyst',
            fallback_system='You are a forex news analyst.',
            fallback_user='Pair: {pair}',
            variables={'pair': 'EURUSD'},
        )

        assert 'Skills agent à appliquer:' in rendered['system_prompt']
        assert '- Prioriser impact Forex' in rendered['system_prompt']
        assert rendered['skills'] == ['Prioriser impact Forex', 'Citer les risques']
