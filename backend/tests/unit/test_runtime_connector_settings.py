from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models.connector_config import ConnectorConfig
from app.services.connectors.runtime_settings import RuntimeConnectorSettings


def test_runtime_connector_settings_reads_and_invalidates_cache(monkeypatch) -> None:
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr('app.services.connectors.runtime_settings.SessionLocal', local_session)
    RuntimeConnectorSettings.clear_cache()

    with Session(engine) as db:
        db.add(
            ConnectorConfig(
                connector_name='yfinance',
                enabled=True,
                settings={'NEWSAPI_API_KEY': 'first-key'},
            )
        )
        db.commit()

    first = RuntimeConnectorSettings.get_string('yfinance', ('NEWSAPI_API_KEY',), default='')
    assert first == 'first-key'

    with Session(engine) as db:
        row = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == 'yfinance').first()
        assert row is not None
        row.settings = {'NEWSAPI_API_KEY': 'second-key'}
        db.commit()

    cached = RuntimeConnectorSettings.get_string('yfinance', ('NEWSAPI_API_KEY',), default='')
    assert cached == 'first-key'

    RuntimeConnectorSettings.clear_cache('yfinance')
    refreshed = RuntimeConnectorSettings.get_string('yfinance', ('NEWSAPI_API_KEY',), default='')
    assert refreshed == 'second-key'
