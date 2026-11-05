"""Celery tasks. All logic lives in the engine, so the deployed Lambda runs the same code."""

from celery import shared_task

from app.adapters.celery_queue import RUN_STEP_TASK, SWEEP_TASK, step_message
from app.bootstrap import get_runtime
from app.domain.engine import run_step
from app.domain.sweeper import sweep


@shared_task(name=RUN_STEP_TASK)  # type: ignore[untyped-decorator]
def run_step_task(branch_id: str, seq: int, trace_context: dict[str, str] | None = None) -> str:
    return run_step(get_runtime().deps, step_message(branch_id, seq, trace_context)).value


@shared_task(name=SWEEP_TASK)  # type: ignore[untyped-decorator]
def sweep_task() -> None:
    sweep(get_runtime().deps)
