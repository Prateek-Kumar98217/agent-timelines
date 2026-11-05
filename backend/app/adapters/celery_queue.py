"""Step queue on Celery and RabbitMQ, used by the local build."""

from uuid import UUID

from celery import Celery

from app.ports import StepMessage
from app.settings import Settings

STEP_QUEUE = "steps"
RUN_STEP_TASK = "agent_timelines.run_step"
SWEEP_TASK = "agent_timelines.sweep"


def make_celery(settings: Settings) -> Celery:
    app = Celery("agent_timelines", broker=settings.rabbitmq_url, include=["app.worker.tasks"])
    app.conf.update(
        task_default_queue=STEP_QUEUE,
        task_serializer="json",
        accept_content=["json"],
        task_ignore_result=True,
        # A step is redelivered if its worker dies. The engine's lease and sequence checks make
        # that safe, and one message at a time keeps queued work out of worker memory.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        # RabbitMQ 4.3 refuses Celery's default transient non-exclusive control queues.
        control_queue_exclusive=True,
        event_queue_exclusive=True,
        broker_connection_timeout=3,
        broker_connection_retry_on_startup=True,
        beat_schedule={
            "sweep": {
                "task": SWEEP_TASK,
                "schedule": settings.sweep_interval_seconds,
                "options": {"expires": settings.sweep_interval_seconds * 5},
            }
        },
    )
    return app


class CeleryStepQueue:
    def __init__(self, app: Celery) -> None:
        self._app = app

    def enqueue(self, message: StepMessage) -> None:
        # No retries here: on failure the outbox row stays, and the sweeper sends it later.
        self._app.send_task(
            RUN_STEP_TASK,
            args=[str(message.branch_id), message.seq],
            kwargs={"trace_context": message.trace_context},
            retry=False,
        )

    def depth(self) -> int:
        with self._app.connection_for_read() as connection:
            declared = connection.default_channel.queue_declare(queue=STEP_QUEUE, passive=True)
            return int(declared.message_count)


def step_message(branch_id: str, seq: int, trace_context: dict[str, str] | None) -> StepMessage:
    return StepMessage(UUID(branch_id), seq, trace_context or {})
