"""Celery tasks. All logic lives in the engine."""

from celery import shared_task

from app.adapters.celery_queue import RUN_STEP_TASK, SWEEP_TASK, step_message
from app.bootstrap import get_runtime
from app.domain.engine import run_step
from app.domain.sweeper import sweep


@shared_task(name=RUN_STEP_TASK)  # type: ignore[untyped-decorator]
def run_step_task(
    branch_id: str,
    seq: int,
    trace_context: dict[str, str] | None = None,
    enqueued_at: float | None = None,
) -> str:
    message = step_message(branch_id, seq, trace_context, enqueued_at)
    return run_step(get_runtime().deps, message).value


@shared_task(name=SWEEP_TASK)  # type: ignore[untyped-decorator]
def sweep_task() -> None:
    sweep(get_runtime().deps)
