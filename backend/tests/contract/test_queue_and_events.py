"""Behaviour shared by each build's step queue and live events."""

import asyncio
import time
from collections.abc import Iterator
from uuid import uuid4

import pytest
from celery import Celery

from app.adapters.celery_queue import STEP_QUEUE, CeleryStepQueue, make_celery
from app.adapters.memory import InlineQueue, MemoryLiveEvents
from app.adapters.redis_store import RedisLiveEvents
from app.ports import LiveEvents, StepMessage, StepQueue, TreeEvent
from app.settings import Settings


@pytest.fixture
def celery_app() -> Iterator[Celery]:
    app = make_celery(Settings())
    with app.connection_for_write() as connection:
        connection.default_channel.queue_declare(queue=STEP_QUEUE, durable=True, auto_delete=False)
        connection.default_channel.queue_purge(STEP_QUEUE)
    yield app
    with app.connection_for_write() as connection:
        connection.default_channel.queue_purge(STEP_QUEUE)


@pytest.fixture(params=["memory", "celery"])
def queue(request: pytest.FixtureRequest) -> StepQueue:
    if request.param == "memory":
        return InlineQueue()
    return CeleryStepQueue(request.getfixturevalue("celery_app"))


@pytest.fixture(params=["memory", "redis"])
def live_events(request: pytest.FixtureRequest) -> LiveEvents:
    if request.param == "memory":
        return MemoryLiveEvents()
    return RedisLiveEvents(Settings().redis_url, prefix=f"test:{uuid4().hex}:")


def test_enqueue_increases_depth(queue: StepQueue) -> None:
    assert queue.depth() == 0
    queue.enqueue(StepMessage(uuid4(), 0))
    queue.enqueue(StepMessage(uuid4(), 1))
    for _ in range(50):
        if queue.depth() == 2:
            break
        time.sleep(0.05)
    assert queue.depth() == 2


async def test_events_reach_subscribers_of_that_tree_only(live_events: LiveEvents) -> None:
    tree, other = uuid4(), uuid4()
    subscription = live_events.subscribe(tree)
    received = asyncio.ensure_future(anext(subscription))
    await asyncio.sleep(0.1)

    live_events.publish(TreeEvent(other, 1, "ignored", {}))
    live_events.publish(TreeEvent(tree, 2, "seen", {"a": 1}))

    event = await asyncio.wait_for(received, timeout=2)
    assert (event.seq, event.type, event.payload) == (2, "seen", {"a": 1})
    await subscription.aclose()  # type: ignore[attr-defined]
