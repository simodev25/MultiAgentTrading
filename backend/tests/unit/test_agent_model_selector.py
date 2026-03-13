from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.connector_config import ConnectorConfig
from app.services.llm.model_selector import AgentModelSelector


def test_agent_model_selector_prefers_agent_specific_and_default() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'default_model': 'gpt-oss:20b',
                    'agent_models': {
                        'news-analyst': 'gpt-oss:120b',
                    },
                },
            )
        )
        db.commit()

        selector = AgentModelSelector()
        selector.settings.ollama_model = 'llama3.1'

        assert selector.resolve(db, 'news-analyst') == 'gpt-oss:120b'
        assert selector.resolve(db, 'bearish-researcher') == 'gpt-oss:20b'
        assert selector.is_enabled(db, 'news-analyst') is True


def test_agent_model_selector_falls_back_to_env_default() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    selector = AgentModelSelector()
    selector.settings.ollama_model = 'llama3.1'

    assert selector.resolve(None, 'news-analyst') == 'llama3.1'
    assert selector.is_enabled(None, 'news-analyst') is True
    assert selector.is_enabled(None, 'macro-analyst') is False

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={},
            )
        )
        db.commit()
        assert selector.resolve(db, 'news-analyst') == 'llama3.1'
        assert selector.is_enabled(db, 'news-analyst') is True


def test_agent_model_selector_reads_enabled_overrides() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_llm_enabled': {
                        'news-analyst': False,
                        'macro-analyst': True,
                    },
                },
            )
        )
        db.commit()

        selector = AgentModelSelector()
        assert selector.is_enabled(db, 'news-analyst') is False
        assert selector.is_enabled(db, 'macro-analyst') is True
