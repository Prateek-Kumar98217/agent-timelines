"""Step queue on Celery and RabbitMQ, used by the local build."""

from uuid import UUID

from celery import Celery

from app.ports import StepMessage
from app.settings import Settings

STEP_QUEUE = "steps"
RUN_STEP_TASK = "agent_timelines.run_step"
SWEEP_TASK = "agent_timelines.sweep"
# How long a send waits for the broker to confirm it, and how long any one socket read or write can
# take. Together they bound how long a request can block on a broker that has stopped answering.
CONFIRM_TIMEOUT_SECONDS = 2
SOCKET_TIMEOUT_SECONDS = 2


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
        # A stuck step is stopped before its lease expires, and the sweeper retries it.
        task_time_limit=settings.lease_seconds,
        # RabbitMQ 4.3 refuses Celery's default transient non-exclusive control queues.
        control_queue_exclusive=True,
        event_queue_exclusive=True,
        broker_connection_timeout=3,
        # The outbox deletes its row after a send, so a send must mean the broker has the message.
        # Without this, one written to a dying connection is lost with no error.
        broker_transport_options={"confirm_publish": True},
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


def make_publisher(settings: Settings) -> Celery:
    """A Celery app only for sending, with socket timeouts.

    Closing a connection to a frozen broker otherwise waits forever for its reply. A consumer can't
    have these timeouts, because it sits idle on its socket between messages.
    """
    app = make_celery(settings)
    app.conf.broker_transport_options = {
        **app.conf.broker_transport_options,
        "read_timeout": SOCKET_TIMEOUT_SECONDS,
        "write_timeout": SOCKET_TIMEOUT_SECONDS,
    }
    return app


class CeleryStepQueue:
    def __init__(self, app: Celery, queue: str = STEP_QUEUE) -> None:
        self._app = app
        self._queue = queue

    def enqueue(self, message: StepMessage) -> None:
        # No retries here: on failure the outbox row stays, and the sweeper sends it later.
        self._app.send_task(
            RUN_STEP_TASK,
            args=[str(message.branch_id), message.seq],
            kwargs={"trace_context": message.trace_context, "enqueued_at": message.enqueued_at},
            queue=self._queue,
            retry=False,
            confirm_timeout=CONFIRM_TIMEOUT_SECONDS,
        )

    def depth(self) -> int:
        with self._app.connection_for_read() as connection:
            declared = connection.default_channel.queue_declare(queue=self._queue, passive=True)
            return int(declared.message_count)


def step_message(
    branch_id: str, seq: int, trace_context: dict[str, str] | None, enqueued_at: float | None
) -> StepMessage:
    return StepMessage(UUID(branch_id), seq, trace_context or {}, enqueued_at)
