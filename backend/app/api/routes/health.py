from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.health import HealthResponse
from app.services.llm.model_selector import normalize_llm_provider

router = APIRouter(prefix='/health', tags=['health'])


@router.get('', response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    settings = get_settings()
    services = {'api': 'ok'}

    try:
        db.execute(text('SELECT 1'))
        services['postgres'] = 'ok'
    except Exception:
        services['postgres'] = 'degraded'

    llm_provider = normalize_llm_provider(settings.llm_provider, fallback='ollama')
    llm_configured = False
    if llm_provider == 'openai':
        llm_configured = bool((settings.openai_api_key or '').strip())
    elif llm_provider == 'mistral':
        llm_configured = bool((settings.mistral_api_key or '').strip())
    else:
        llm_configured = bool((settings.ollama_api_key or '').strip())
    services['llm'] = 'configured' if llm_configured else 'degraded'
    services['llm_provider'] = llm_provider
    # Backward-compatible key for existing dashboards/checklists.
    services['ollama'] = 'configured' if llm_provider == 'ollama' and llm_configured else 'degraded'
    services['metaapi'] = 'configured' if bool(settings.metaapi_token and settings.metaapi_account_id) else 'degraded'

    status = 'ok' if services.get('postgres') == 'ok' else 'degraded'
    return HealthResponse(status=status, services=services)
