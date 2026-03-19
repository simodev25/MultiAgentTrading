import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Role, require_roles
from app.db.models.connector_config import ConnectorConfig
from app.db.session import get_db
from app.schemas.connector import ConnectorConfigOut, ConnectorConfigUpdate, MarketSymbolsOut, MarketSymbolsUpdate
from app.services.llm.model_selector import (
    AgentModelSelector,
    DEFAULT_DECISION_MODE,
    SUPPORTED_DECISION_MODES,
    normalize_decision_mode,
)
from app.services.llm.provider_client import LlmClient
from app.services.market.symbols import get_market_symbols_config, save_market_symbols_config
from app.services.market.yfinance_provider import YFinanceMarketProvider
from app.services.memory.vector_memory import VectorMemoryService
from app.services.trading.metaapi_client import MetaApiClient

router = APIRouter(prefix='/connectors', tags=['connectors'])

SUPPORTED_CONNECTORS = ['ollama', 'metaapi', 'yfinance', 'qdrant']


def _normalize_agent_skills(raw_skills: object) -> dict[str, list[str]]:
    if not isinstance(raw_skills, dict):
        return {}

    normalized: dict[str, list[str]] = {}
    for raw_agent_name, raw_value in raw_skills.items():
        agent_name = str(raw_agent_name or '').strip()
        if not agent_name:
            continue

        raw_items: list[str]
        if isinstance(raw_value, str):
            text = raw_value.strip()
            if not text:
                continue
            if text.startswith('['):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        raw_items = [str(item).strip() for item in parsed]
                    else:
                        raw_items = [text]
                except json.JSONDecodeError:
                    raw_items = [item.strip() for item in text.splitlines()]
            elif '\n' in text:
                raw_items = [item.strip() for item in text.splitlines()]
            elif '||' in text:
                raw_items = [item.strip() for item in text.split('||')]
            elif ';' in text:
                raw_items = [item.strip() for item in text.split(';')]
            else:
                raw_items = [text]
        elif isinstance(raw_value, (list, tuple, set)):
            raw_items = [str(item).strip() for item in raw_value]
        else:
            continue

        deduped: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            cleaned = item.strip()
            if not cleaned:
                continue
            if len(cleaned) > 500:
                cleaned = cleaned[:500].rstrip()
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(cleaned)
            if len(deduped) >= 12:
                break

        if deduped:
            normalized[agent_name] = deduped

    return normalized


def _normalize_bool_setting(value: object, *, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'1', 'true', 'yes', 'on'}:
            return True
        if normalized in {'0', 'false', 'no', 'off'}:
            return False
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    return fallback


def _sanitize_ollama_settings(raw_settings: dict) -> dict:
    settings = dict(raw_settings or {})
    raw_enabled = settings.get('agent_llm_enabled')
    enabled = dict(raw_enabled) if isinstance(raw_enabled, dict) else {}
    settings['agent_llm_enabled'] = enabled
    settings['agent_skills'] = _normalize_agent_skills(settings.get('agent_skills'))
    settings['decision_mode'] = normalize_decision_mode(
        settings.get('decision_mode'),
        fallback=DEFAULT_DECISION_MODE,
    )
    settings['memory_context_enabled'] = _normalize_bool_setting(
        settings.get('memory_context_enabled'),
        fallback=False,
    )
    return settings


def _validate_decision_mode_value(raw_settings: dict) -> None:
    if not isinstance(raw_settings, dict):
        return
    if 'decision_mode' not in raw_settings:
        return
    value = str(raw_settings.get('decision_mode', '') or '').strip().lower()
    if value not in SUPPORTED_DECISION_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid decision_mode '{raw_settings.get('decision_mode')}'. Allowed: {', '.join(sorted(SUPPORTED_DECISION_MODES))}.",
        )


@router.get('', response_model=list[ConnectorConfigOut])
def list_connectors(
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)),
) -> list[ConnectorConfigOut]:
    settings = get_settings()
    connectors = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name.in_(SUPPORTED_CONNECTORS)).all()
    existing = {conn.connector_name for conn in connectors}
    for connector_name in SUPPORTED_CONNECTORS:
        if connector_name not in existing:
            connector_settings: dict = {}
            if connector_name == 'ollama':
                connector_settings = {
                    'provider': settings.llm_provider,
                    'decision_mode': normalize_decision_mode(settings.decision_mode),
                    'memory_context_enabled': False,
                }
            conn = ConnectorConfig(connector_name=connector_name, enabled=True, settings=connector_settings)
            db.add(conn)
    for conn in connectors:
        if conn.connector_name != 'ollama':
            continue
        current_settings = conn.settings if isinstance(conn.settings, dict) else {}
        normalized_settings = _sanitize_ollama_settings(
            {
                **current_settings,
                'provider': current_settings.get('provider', settings.llm_provider),
                'decision_mode': current_settings.get('decision_mode', settings.decision_mode),
                'memory_context_enabled': current_settings.get('memory_context_enabled', False),
            }
        )
        if normalized_settings != current_settings:
            conn.settings = normalized_settings
    db.commit()
    connectors = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name.in_(SUPPORTED_CONNECTORS)).all()
    return [ConnectorConfigOut.model_validate(conn) for conn in connectors]


@router.get('/market-symbols', response_model=MarketSymbolsOut)
def get_market_symbols(
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.TRADER_OPERATOR, Role.ANALYST, Role.VIEWER)),
) -> MarketSymbolsOut:
    settings = get_settings()
    payload = get_market_symbols_config(db, settings)
    return MarketSymbolsOut.model_validate(payload)


@router.put('/market-symbols', response_model=MarketSymbolsOut)
def update_market_symbols(
    payload: MarketSymbolsUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)),
) -> MarketSymbolsOut:
    symbol_groups_payload = [group.model_dump() for group in payload.symbol_groups]
    save_market_symbols_config(
        db,
        symbol_groups=symbol_groups_payload,
        forex_pairs=payload.forex_pairs,
        crypto_pairs=payload.crypto_pairs,
    )
    settings = get_settings()
    resolved = get_market_symbols_config(db, settings)
    return MarketSymbolsOut.model_validate(resolved)


@router.get('/ollama/models')
def list_ollama_models(
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)),
) -> dict:
    return LlmClient().list_models(db)


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
    if connector_name == 'ollama':
        _validate_decision_mode_value(payload.settings)
        conn.settings = _sanitize_ollama_settings(payload.settings)
    else:
        conn.settings = payload.settings
    db.commit()
    db.refresh(conn)
    if connector_name == 'ollama':
        AgentModelSelector.clear_cache()
    return ConnectorConfigOut.model_validate(conn)


@router.post('/{connector_name}/test')
async def test_connector(
    connector_name: str,
    db: Session = Depends(get_db),
    _=Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)),
) -> dict:
    connector_name = connector_name.lower()
    if connector_name == 'ollama':
        client = LlmClient()
        return client.chat('You are a health-check bot.', 'Reply with OK in one word.', db=db)
    if connector_name == 'metaapi':
        client = MetaApiClient()
        return await client.get_account_information()
    if connector_name == 'yfinance':
        provider = YFinanceMarketProvider()
        settings = get_settings()
        symbols_config = get_market_symbols_config(db, settings)
        sample_symbol = next(iter(symbols_config.get('tradeable_pairs', [])), 'SPY')
        return {
            'sample_symbol': sample_symbol,
            'market': provider.get_market_snapshot(sample_symbol, 'H1'),
            'news': provider.get_news_context(sample_symbol),
        }
    if connector_name == 'qdrant':
        service = VectorMemoryService()
        return {
            'configured': bool(service._qdrant),
            'collection': service.collection,
            'vector_size': service.vector_size,
        }

    raise HTTPException(status_code=404, detail='Unsupported connector')
