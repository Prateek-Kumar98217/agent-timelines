"""Celery entry point: `celery -A app.worker.celery_app worker`."""

from app.adapters.celery_queue import make_celery
from app.settings import get_settings

app = make_celery(get_settings())
