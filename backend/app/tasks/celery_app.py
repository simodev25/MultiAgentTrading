import logging
import os

from celery import Celery
from celery.signals import after_setup_logger, worker_process_init, worker_process_shutdown, worker_ready

from app.core.config import get_settings
from app.observability.prometheus import mark_worker_process_dead, start_worker_metrics_server
from app.tasks.worker_tracing import init_agentscope_tracing_for_current_process

settings = get_settings()

backend_url = 'cache+memory://' if settings.celery_ignore_result else settings.celery_result_backend
if settings.celery_ignore_result:
    os.environ.pop('CELERY_RESULT_BACKEND', None)

celery_app = Celery(
    'trading_platform',
    broker=settings.celery_broker_url,
    backend=backend_url,
    include=['app.tasks.run_analysis_task', 'app.tasks.backtest_task', 'app.tasks.strategy_backtest_task', 'app.tasks.strategy_monitor_task', 'app.tasks.portfolio_tasks'],
)
celery_app.conf.task_routes = {
    'app.tasks.run_analysis_task.*': {'queue': settings.celery_analysis_queue},
    'app.tasks.backtest_task.*': {'queue': settings.celery_backtest_queue},
    'app.tasks.strategy_backtest_task.*': {'queue': settings.celery_backtest_queue},
    'app.tasks.strategy_monitor_task.*': {'queue': settings.celery_analysis_queue},
    'app.tasks.portfolio_tasks.*': {'queue': settings.celery_analysis_queue},
}
celery_app.conf.task_default_queue = settings.celery_analysis_queue
celery_app.conf.result_backend = backend_url
celery_app.conf.task_ignore_result = settings.celery_ignore_result
celery_app.conf.task_store_errors_even_if_ignored = False
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.task_acks_late = settings.celery_task_acks_late
celery_app.conf.task_reject_on_worker_lost = settings.celery_task_reject_on_worker_lost
celery_app.conf.task_track_started = settings.celery_task_track_started
celery_app.conf.worker_hijack_root_logger = True
celery_app.conf.worker_redirect_stdouts_level = 'INFO'


# Ensure task module is imported when worker boots with "-A ...celery_app".
import app.tasks.run_analysis_task  # noqa: E402,F401
import app.tasks.backtest_task  # noqa: E402,F401
import app.tasks.strategy_backtest_task  # noqa: E402,F401
import app.tasks.strategy_monitor_task  # noqa: E402,F401
import app.tasks.portfolio_tasks  # noqa: E402,F401

# Beat schedule: periodic strategy monitoring (every 30 seconds)
celery_app.conf.beat_schedule = {
    'strategy-monitor-check': {
        'task': 'app.tasks.strategy_monitor_task.check_all',
        'schedule': 30.0,
    },
    'portfolio-snapshot': {
        'task': 'app.tasks.portfolio_tasks.snapshot_portfolio',
        'schedule': 900.0,  # 15 minutes
    },
    'correlation-matrix-refresh': {
        'task': 'app.tasks.portfolio_tasks.refresh_correlation_matrix',
        'schedule': 86400.0,  # 24 hours
    },
}


@after_setup_logger.connect(weak=False)
def _quiet_celery_loggers(**_: object) -> None:
    """Suppress noisy loggers in Celery workers.

    - Celery internals: WARNING (no more per-task received/succeeded)
    - MetaAPI SDK: ERROR only (suppress connection retry spam)
    - App loggers: INFO (our application logs)
    """
    # Celery noise
    logging.getLogger('celery').setLevel(logging.WARNING)
    logging.getLogger('celery.app.trace').setLevel(logging.WARNING)
    logging.getLogger('celery.worker').setLevel(logging.WARNING)
    logging.getLogger('celery.beat').setLevel(logging.WARNING)
    logging.getLogger('celery.redirected').setLevel(logging.WARNING)
    # MetaAPI SDK noise (reconnection loops, PING/PONG, connection errors)
    logging.getLogger('socketio').setLevel(logging.ERROR)
    logging.getLogger('engineio').setLevel(logging.ERROR)
    logging.getLogger('metaapi.cloud').setLevel(logging.ERROR)
    logging.getLogger('metaapi_cloud_sdk').setLevel(logging.ERROR)
    # Keep our app loggers at INFO
    logging.getLogger('app').setLevel(logging.INFO)


@worker_ready.connect(weak=False)
def _start_prometheus_worker_metrics_server(**_: object) -> None:
    if not settings.prometheus_enabled:
        return
    start_worker_metrics_server(settings.prometheus_worker_port)


@worker_process_init.connect(weak=False)
def _init_agentscope_tracing_in_worker_process(**_: object) -> None:
    """Initialize tracing in each forked Celery worker process."""
    init_agentscope_tracing_for_current_process()


@worker_ready.connect(weak=False)
def _init_agentscope_tracing_in_main_process(**_: object) -> None:
    """Initialize tracing in main process for embedded beat / process-local diagnostics."""
    init_agentscope_tracing_for_current_process()


@worker_process_shutdown.connect(weak=False)
def _mark_prometheus_worker_process_dead(pid: int | None = None, **_: object) -> None:
    mark_worker_process_dead(pid)
