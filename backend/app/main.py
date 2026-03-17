import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import Role, get_password_hash
from app.db.base import Base
from app.db.models.connector_config import ConnectorConfig
from app.db.models.execution_order import ExecutionOrder
from app.db.models.metaapi_account import MetaApiAccount
from app.db.models.run import AnalysisRun
from app.db.models.user import User
from app.db.session import SessionLocal, engine, get_db
from app.services.prompts.registry import PromptTemplateService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    if settings.enable_pgvector and engine.dialect.name == 'postgresql':
        try:
            with engine.begin() as conn:
                conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        except Exception as exc:
            logger.error(
                'ENABLE_PGVECTOR=true but pgvector extension is not available. '
                'Use a pgvector-enabled Postgres image or set ENABLE_PGVECTOR=false. error=%s',
                exc,
            )
            raise
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            admin = User(
                email='admin@local.dev',
                hashed_password=get_password_hash('admin1234'),
                role=Role.SUPER_ADMIN,
                is_active=True,
            )
            db.add(admin)

        for name in ['ollama', 'metaapi', 'yfinance', 'qdrant', 'order-guardian']:
            exists = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == name).first()
            if not exists:
                enabled = name != 'order-guardian'
                db.add(ConnectorConfig(connector_name=name, enabled=enabled, settings={}))

        if settings.metaapi_account_id and not db.query(MetaApiAccount).count():
            db.add(
                MetaApiAccount(
                    label='Default MetaApi Account',
                    account_id=settings.metaapi_account_id,
                    region=settings.metaapi_region,
                    enabled=True,
                    is_default=True,
                )
            )

        db.commit()

        PromptTemplateService().seed_defaults(db)
    finally:
        db.close()

    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version='0.1.0', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(api_router, prefix=settings.api_prefix)

if settings.open_telemetry_enabled:
    FastAPIInstrumentor.instrument_app(app)


@app.get('/metrics')
def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode('utf-8'), media_type=CONTENT_TYPE_LATEST)


@app.websocket('/ws/runs/{run_id}')
async def run_updates_socket(websocket: WebSocket, run_id: int) -> None:
    await websocket.accept()
    try:
        while True:
            db: Session = SessionLocal()
            try:
                run = db.get(AnalysisRun, run_id)
                if not run:
                    await websocket.send_json({'error': 'Run not found'})
                    await websocket.close(code=1008)
                    return
                await websocket.send_json(
                    {
                        'id': run.id,
                        'status': run.status,
                        'decision': run.decision,
                        'updated_at': run.updated_at.isoformat(),
                    }
                )
                if run.status in {'completed', 'failed'}:
                    await websocket.close(code=1000)
                    return
            finally:
                db.close()

            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return


@app.websocket('/ws/trading/orders')
async def trading_orders_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    last_order_id: int | None = None
    try:
        while True:
            db: Session = SessionLocal()
            try:
                order = db.query(ExecutionOrder).order_by(ExecutionOrder.id.desc()).first()
                if order and order.id != last_order_id:
                    event_type = 'snapshot' if last_order_id is None else 'execution-order'
                    await websocket.send_json(
                        {
                            'type': event_type,
                            'order': {
                                'id': order.id,
                                'run_id': order.run_id,
                                'mode': order.mode,
                                'status': order.status,
                                'symbol': order.symbol,
                                'created_at': order.created_at.isoformat(),
                            },
                        }
                    )
                    last_order_id = order.id
            finally:
                db.close()

            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return


@app.get('/')
def root() -> dict[str, str]:
    return {'message': settings.app_name, 'version': '0.1.0'}
