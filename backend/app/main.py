import asyncio
import fcntl
import logging
import os
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
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
from app.observability.metrics import backend_http_request_duration_seconds, backend_http_requests_total
from app.services.prompts.registry import PromptTemplateService

logger = logging.getLogger(__name__)


def _is_pgvector_extension_race(exc: Exception) -> bool:
    """Return True when concurrent startup attempted to create the same extension."""
    if isinstance(exc, IntegrityError):
        pgcode = getattr(getattr(exc, 'orig', None), 'pgcode', None)
        if pgcode in {'23505', '42710'}:
            return True
    message = str(exc).lower()
    return 'pg_extension_name_index' in message and 'vector' in message


def _acquire_startup_lock() -> tuple[int, bool]:
    """
    Acquire an inter-process startup lock.
    Returns (fd, already_initialized) where already_initialized means another
    worker has already finished bootstrap in this container lifecycle.
    """
    lock_path = '/tmp/forex_startup.lock'
    done_path = '/tmp/forex_startup.done'
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd, os.path.exists(done_path)


def _release_startup_lock(fd: int, mark_done: bool) -> None:
    if mark_done:
        done_path = '/tmp/forex_startup.done'
        with open(done_path, 'w', encoding='utf-8') as marker:
            marker.write('ok\n')
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    lock_fd, already_initialized = _acquire_startup_lock()
    try:
        if not already_initialized:
            if settings.enable_pgvector and engine.dialect.name == 'postgresql':
                try:
                    with engine.begin() as conn:
                        conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
                except Exception as exc:
                    if _is_pgvector_extension_race(exc):
                        logger.warning(
                            'Concurrent pgvector extension initialization detected; continuing startup. error=%s',
                            exc,
                        )
                    else:
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
        _release_startup_lock(lock_fd, mark_done=True)
    except Exception:
        _release_startup_lock(lock_fd, mark_done=False)
        raise

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


def _request_route_template(request: Request) -> str:
    route = request.scope.get('route')
    path_template = getattr(route, 'path', None)
    if isinstance(path_template, str) and path_template:
        return path_template
    return request.url.path or 'unknown'


@app.middleware('http')
async def prometheus_request_metrics(request: Request, call_next):
    started = perf_counter()
    method = (request.method or 'UNKNOWN').upper()
    status = '500'
    try:
        response = await call_next(request)
        status = str(getattr(response, 'status_code', 500))
        return response
    finally:
        duration = max(perf_counter() - started, 0.0)
        route = _request_route_template(request)
        backend_http_requests_total.labels(method=method, route=route, status=status).inc()
        backend_http_request_duration_seconds.labels(method=method, route=route).observe(duration)


@app.get('/metrics')
def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode('utf-8'), media_type=CONTENT_TYPE_LATEST)


@app.websocket('/ws/runs/{run_id}')
async def run_updates_socket(websocket: WebSocket, run_id: int) -> None:
    await websocket.accept()
    poll_interval = max(float(settings.ws_run_poll_seconds), 0.5)
    last_signature: tuple[str, str] | None = None
    try:
        while True:
            db: Session = SessionLocal()
            try:
                row = (
                    db.query(
                        AnalysisRun.id,
                        AnalysisRun.status,
                        AnalysisRun.decision,
                        AnalysisRun.updated_at,
                    )
                    .filter(AnalysisRun.id == run_id)
                    .first()
                )
                if not row:
                    await websocket.send_json({'error': 'Run not found'})
                    await websocket.close(code=1008)
                    return
                decision = row.decision
                if isinstance(decision, dict):
                    decision = decision.get('decision') or decision
                updated_at = row.updated_at.isoformat()
                signature = (str(row.status), updated_at)
                if signature != last_signature:
                    await websocket.send_json(
                        {
                            'id': row.id,
                            'status': row.status,
                            'decision': decision,
                            'updated_at': updated_at,
                        }
                    )
                    last_signature = signature
                if row.status in {'completed', 'failed'}:
                    await websocket.close(code=1000)
                    return
            finally:
                db.close()

            await asyncio.sleep(poll_interval)
    except WebSocketDisconnect:
        return


@app.websocket('/ws/trading/orders')
async def trading_orders_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    poll_interval = max(float(settings.ws_trading_orders_poll_seconds), 0.5)
    last_order_id: int | None = None
    try:
        while True:
            db: Session = SessionLocal()
            try:
                order = (
                    db.query(
                        ExecutionOrder.id,
                        ExecutionOrder.run_id,
                        ExecutionOrder.mode,
                        ExecutionOrder.status,
                        ExecutionOrder.symbol,
                        ExecutionOrder.created_at,
                    )
                    .order_by(ExecutionOrder.id.desc())
                    .first()
                )
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

            await asyncio.sleep(poll_interval)
    except WebSocketDisconnect:
        return


@app.get('/')
def root() -> dict[str, str]:
    return {'message': settings.app_name, 'version': '0.1.0'}
