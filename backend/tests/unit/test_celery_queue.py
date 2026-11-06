from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from app.adapters.celery_queue import (
    CONFIRM_TIMEOUT_SECONDS,
    SOCKET_TIMEOUT_SECONDS,
    CeleryStepQueue,
    make_celery,
    make_publisher,
)
from app.ports import StepMessage
from app.settings import Settings


def test_sends_wait_for_the_broker_to_confirm_them() -> None:
    """The outbox deletes its row after a send, so an unconfirmed send could lose the step."""
    assert make_celery(Settings()).conf.broker_transport_options["confirm_publish"] is True


def test_only_the_sending_connection_has_socket_timeouts() -> None:
    """A consumer sits idle on its socket between messages, so a timeout there would break it."""
    options = make_publisher(Settings()).conf.broker_transport_options
    assert options["read_timeout"] == SOCKET_TIMEOUT_SECONDS
    assert options["confirm_publish"] is True
    assert "read_timeout" not in make_celery(Settings()).conf.broker_transport_options


def test_a_send_gives_up_on_a_silent_broker_instead_of_blocking() -> None:
    app = MagicMock()
    CeleryStepQueue(app).enqueue(StepMessage(uuid4(), 1))
    options: dict[str, Any] = app.send_task.call_args.kwargs
    assert options["confirm_timeout"] == CONFIRM_TIMEOUT_SECONDS
    assert options["retry"] is False
