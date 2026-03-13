import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Role, require_roles
from app.db.models.connector_config import ConnectorConfig
from app.db.session import get_db
from app.schemas.connector import ConnectorConfigOut, ConnectorConfigUpdate
from app.services.llm.ollama_client import OllamaCloudClient
from app.services.market.yfinance_provider import YFinanceMarketProvider
from app.services.memory.vector_memory import VectorMemoryService
from app.services.trading.metaapi_client import MetaApiClient

router = APIRouter(prefix='/connectors', tags=['connectors'])

SUPPORTED_CONNECTORS = ['ollama', 'metaapi', 'yfinance', 'qdrant']


@router.get('', response_model=list[ConnectorConfigOut])
def list_connectors(
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)),
) -> list[ConnectorConfigOut]:
    connectors = db.query(ConnectorConfig).all()
    existing = {conn.connector_name for conn in connectors}
    for connector_name in SUPPORTED_CONNECTORS:
        if connector_name not in existing:
            conn = ConnectorConfig(connector_name=connector_name, enabled=True, settings={})
            db.add(conn)
    db.commit()
    connectors = db.query(ConnectorConfig).all()
    return [ConnectorConfigOut.model_validate(conn) for conn in connectors]


@router.get('/ollama/models')
def list_ollama_models(
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)),
) -> dict:
    settings = get_settings()
    base_url = (settings.ollama_base_url or '').strip().rstrip('/')
    api_key = (settings.ollama_api_key or '').strip().strip('"').strip("'")
    timeout = max(min(int(settings.ollama_timeout_seconds), 30), 5)

    candidate_urls = []
    if base_url:
        candidate_urls.append(f'{base_url}/api/tags')
    candidate_urls.append('https://ollama.com/api/tags')

    # Deduplicate while preserving order.
    unique_urls = list(dict.fromkeys(candidate_urls))
    headers = {'Accept': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    errors: list[str] = []
    for url in unique_urls:
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                payload = response.json() if response.content else {}
            models = payload.get('models', [])
            names: list[str] = []
            if isinstance(models, list):
                for item in models:
                    if not isinstance(item, dict):
                        continue
                    name = item.get('name') or item.get('model')
                    if isinstance(name, str) and name.strip():
                        names.append(name.strip())
            return {'models': sorted(set(names)), 'source': url}
        except Exception as exc:  # pragma: no cover - network failures are expected in local/offline runs
            errors.append(f'{url}: {exc}')

    return {'models': [], 'source': None, 'error': '; '.join(errors[:2])}


@router.put('/{connector_name}', response_model=ConnectorConfigOut)
def update_connector(
    connector_name: str,
    payload: ConnectorConfigUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)),
) -> ConnectorConfigOut:
    connector_name = connector_name.lower()
    if connector_name not in SUPPORTED_CONNECTORS:
        raise HTTPException(status_code=404, detail='Unsupported connector')

    conn = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == connector_name).first()
    if not conn:
        conn = ConnectorConfig(connector_name=connector_name)
        db.add(conn)

    conn.enabled = payload.enabled
    conn.settings = payload.settings
    db.commit()
    db.refresh(conn)
    return ConnectorConfigOut.model_validate(conn)


@router.post('/{connector_name}/test')
async def test_connector(
    connector_name: str,
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)),
) -> dict:
    connector_name = connector_name.lower()
    if connector_name == 'ollama':
        client = OllamaCloudClient()
        return client.chat('You are a health-check bot.', 'Reply with OK in one word.')
    if connector_name == 'metaapi':
        client = MetaApiClient()
        return await client.get_account_information()
    if connector_name == 'yfinance':
        provider = YFinanceMarketProvider()
        return {
            'market': provider.get_market_snapshot('EURUSD', 'H1'),
            'news': provider.get_news_context('EURUSD'),
        }
    if connector_name == 'qdrant':
        service = VectorMemoryService()
        return {
            'configured': bool(service._qdrant),
            'collection': service.collection,
            'vector_size': service.vector_size,
        }

    raise HTTPException(status_code=404, detail='Unsupported connector')
