import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
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
    assert selector.is_enabled(None, 'market-context-analyst') is False
    assert selector.is_enabled(None, 'schedule-planner-agent') is True

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
                        'market-context-analyst': True,
                    },
                },
            )
        )
        db.commit()

        selector = AgentModelSelector()
        assert selector.is_enabled(db, 'news-analyst') is False
        assert selector.is_enabled(db, 'market-context-analyst') is True


def test_agent_model_selector_allows_risk_and_execution_overrides() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_llm_enabled': {
                        'risk-manager': True,
                        'execution-manager': True,
                    },
                },
            )
        )
        db.commit()

        selector = AgentModelSelector()
        assert selector.is_enabled(db, 'risk-manager') is True
        assert selector.is_enabled(db, 'execution-manager') is True


def test_agent_model_selector_supports_provider_override_and_provider_default_model() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'provider': 'openai',
                },
            )
        )
        db.commit()

        selector = AgentModelSelector()
        selector.settings.openai_model = 'gpt-4o-mini'
        selector.settings.llm_provider = 'ollama'

        assert selector.resolve_provider(db) == 'openai'
        assert selector.resolve(db, 'news-analyst') == 'gpt-4o-mini'


def test_agent_model_selector_resolves_agent_skills() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_skills': {
                        'news-analyst': ['Prioriser sources fiables', 'Citer incertitude', 'Citer incertitude'],
                        'trader-agent': 'Executable decision\nRisk compliance',
                        'risk-manager': 'Validate risk, without splitting the sentence',
                    },
                },
            )
        )
        db.commit()

        selector = AgentModelSelector()
        assert selector.resolve_skills(db, 'news-analyst') == ['Prioriser sources fiables', 'Citer incertitude']
        assert selector.resolve_skills(db, 'trader-agent') == ['Executable decision', 'Risk compliance']
        assert selector.resolve_skills(db, 'risk-manager') == ['Validate risk, without splitting the sentence']
        assert selector.resolve_skills(db, 'market-context-analyst') == []


def test_agent_model_selector_synthesizes_bootstrap_skills_when_connector_missing(tmp_path, monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    bootstrap_file = tmp_path / 'skills.json'
    bootstrap_file.write_text(
        json.dumps(
            {
                'agent_skills': {
                    'news-analyst': ['Interpret retained catalysts first'],
                }
            }
        ),
        encoding='utf-8',
    )

    monkeypatch.setenv('AGENT_SKILLS_BOOTSTRAP_FILE', str(bootstrap_file))
    monkeypatch.setenv('AGENT_SKILLS_BOOTSTRAP_MODE', 'merge')
    monkeypatch.setenv('AGENT_SKILLS_BOOTSTRAP_APPLY_ONCE', 'true')
    get_settings.cache_clear()
    AgentModelSelector.clear_cache()

    try:
        selector = AgentModelSelector()
        with Session(engine) as db:
            assert selector.resolve_skills(db, 'news-analyst') == ['Interpret retained catalysts first']
    finally:
        get_settings.cache_clear()
        AgentModelSelector.clear_cache()


def test_agent_model_selector_resolves_decision_mode_with_fallback() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    selector = AgentModelSelector()
    previous = selector.settings.decision_mode
    try:
        selector.settings.decision_mode = 'conservative'
        assert selector.resolve_decision_mode(None) == 'conservative'

        with Session(engine) as db:
            db.add(
                ConnectorConfig(
                    connector_name='ollama',
                    enabled=True,
                    settings={'decision_mode': 'permissive'},
                )
            )
            db.commit()

            assert selector.resolve_decision_mode(db) == 'permissive'

            row = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == 'ollama').first()
            assert row is not None
            row.settings = {'decision_mode': 'unknown-mode'}
            db.commit()
            AgentModelSelector.clear_cache()
            assert selector.resolve_decision_mode(db) == 'conservative'
    finally:
        selector.settings.decision_mode = previous


def test_agent_model_selector_resolves_memory_context_enabled_with_fallback() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    selector = AgentModelSelector()
    assert selector.resolve_memory_context_enabled(None) is False

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={'memory_context_enabled': 'true'},
            )
        )
        db.commit()

        assert selector.resolve_memory_context_enabled(db) is True

        row = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == 'ollama').first()
        assert row is not None
        row.settings = {'memory_context_enabled': 'off'}
        db.commit()
        AgentModelSelector.clear_cache()
        assert selector.resolve_memory_context_enabled(db) is False


def test_agent_model_selector_resolves_agent_tools_with_default_enabled() -> None:
    selector = AgentModelSelector()
    enabled_tools = selector.resolve_enabled_tools(None, 'news-analyst')

    assert 'news_search' in enabled_tools
    assert 'macro_calendar_or_event_feed' in enabled_tools


def test_agent_model_selector_resolves_agent_tools_overrides() -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='ollama',
                enabled=True,
                settings={
                    'agent_tools': {
                        'news-analyst': {
                            'news_search': False,
                            'macro_calendar_or_event_feed': True,
                        }
                    }
                },
            )
        )
        db.commit()

        selector = AgentModelSelector()
        enabled_tools = selector.resolve_enabled_tools(db, 'news-analyst')
        assert 'news_search' not in enabled_tools
        assert 'macro_calendar_or_event_feed' in enabled_tools
