import os

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

backend_url = 'cache+memory://' if settings.celery_ignore_result else settings.celery_result_backend
if settings.celery_ignore_result:
    os.environ.pop('CELERY_RESULT_BACKEND', None)

celery_app = Celery(
    'forex_platform',
    broker=settings.celery_broker_url,
    backend=backend_url,
    include=['app.tasks.run_analysis_task', 'app.tasks.scheduler_task'],
)
celery_app.conf.task_routes = {'app.tasks.run_analysis_task.*': {'queue': 'analysis'}}
celery_app.conf.result_backend = backend_url
celery_app.conf.task_ignore_result = settings.celery_ignore_result
celery_app.conf.task_store_errors_even_if_ignored = False
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.beat_schedule = {
    'dispatch-due-schedules-each-minute': {
        'task': 'app.tasks.scheduler_task.dispatch_due_schedules',
        'schedule': crontab(minute='*'),
        'options': {'queue': 'analysis'},
    }
}

# Ensure task module is imported when worker boots with "-A ...celery_app".
import app.tasks.run_analysis_task  # noqa: E402,F401
import app.tasks.scheduler_task  # noqa: E402,F401
