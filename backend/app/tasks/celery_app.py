import os

from celery import Celery
from celery.signals import worker_process_shutdown, worker_ready

from app.core.config import get_settings
from app.observability.prometheus import mark_worker_process_dead, start_worker_metrics_server

settings = get_settings()

backend_url = 'cache+memory://' if settings.celery_ignore_result else settings.celery_result_backend
if settings.celery_ignore_result:
    os.environ.pop('CELERY_RESULT_BACKEND', None)

celery_app = Celery(
    'forex_platform',
    broker=settings.celery_broker_url,
    backend=backend_url,
    include=['app.tasks.run_analysis_task', 'app.tasks.backtest_task'],
)
celery_app.conf.task_routes = {
    'app.tasks.run_analysis_task.*': {'queue': settings.celery_analysis_queue},
    'app.tasks.backtest_task.*': {'queue': settings.celery_backtest_queue},
}
celery_app.conf.task_default_queue = settings.celery_analysis_queue
celery_app.conf.result_backend = backend_url
celery_app.conf.task_ignore_result = settings.celery_ignore_result
celery_app.conf.task_store_errors_even_if_ignored = False
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.task_acks_late = settings.celery_task_acks_late
celery_app.conf.task_reject_on_worker_lost = settings.celery_task_reject_on_worker_lost
celery_app.conf.task_track_started = settings.celery_task_track_started

# Ensure task module is imported when worker boots with "-A ...celery_app".
import app.tasks.run_analysis_task  # noqa: E402,F401
import app.tasks.backtest_task  # noqa: E402,F401


@worker_ready.connect(weak=False)
def _start_prometheus_worker_metrics_server(**_: object) -> None:
    if not settings.prometheus_enabled:
        return
    start_worker_metrics_server(settings.prometheus_worker_port)


@worker_process_shutdown.connect(weak=False)
def _mark_prometheus_worker_process_dead(pid: int | None = None, **_: object) -> None:
    mark_worker_process_dead(pid)
