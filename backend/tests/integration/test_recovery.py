"""Lost messages, failed sends and repeatedly dying workers."""

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.adapters.memory import InlineQueue
from app.db.models import BranchStatus, StepOutbox
from app.db.session import SessionFactory
from app.domain.engine import StepOutcome, run_step, start_task
from app.domain.forking import fork_from_edit
from app.domain.sweeper import QUEUED_RESEND_SECONDS, sweep
from app.ports import StepMessage
from tests.helpers import CONFIG, Harness, text_reply, tool_reply
from tests.integration.test_engine import ControlledLLM, WorkerDied, _die


class BrokenQueue(InlineQueue):
    """A queue that can refuse sends, like a broker that's down."""

    def __init__(self) -> None:
        super().__init__()
        self.down = False

    def enqueue(self, message: StepMessage) -> None:
        if self.down:
            raise ConnectionError("broker down")
        super().enqueue(message)


def harness_with_broken_queue(clean_db: SessionFactory, replies: list[object]) -> Harness:
    harness = Harness(clean_db, replies)
    harness.queue = BrokenQueue()
    harness.deps.queue = harness.queue
    return harness


def outbox_rows(session_factory: SessionFactory) -> int:
    with session_factory() as session:
        return session.execute(select(func.count()).select_from(StepOutbox)).scalar_one()


def start(harness: Harness) -> UUID:
    return start_task(harness.deps, harness.user.id, "the task", CONFIG)[1]


def test_a_sent_step_leaves_no_outbox_row(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()])
    start(harness)
    assert harness.queue.depth() == 1
    assert outbox_rows(clean_db) == 0


def test_broker_outage_at_start_loses_nothing(clean_db: SessionFactory) -> None:
    harness = harness_with_broken_queue(clean_db, [text_reply()])
    harness.queue.down = True  # type: ignore[attr-defined]

    branch_id = start(harness)

    assert outbox_rows(clean_db) == 1
    assert harness.queue.depth() == 0
    harness.queue.down = False  # type: ignore[attr-defined]
    assert sweep(harness.deps).dispatched == 1
    assert outbox_rows(clean_db) == 0
    harness.drain()
    assert harness.branch(branch_id).status == BranchStatus.COMPLETED


def test_broker_outage_between_steps_resumes_after_sweep(clean_db: SessionFactory) -> None:
    harness = harness_with_broken_queue(clean_db, [tool_reply(), text_reply()])
    branch_id = start(harness)
    first = harness.queue.messages.popleft()
    harness.queue.down = True  # type: ignore[attr-defined]

    assert run_step(harness.deps, first) == StepOutcome.EXECUTED

    assert harness.types(branch_id)[-1] == "tool_call"
    assert harness.queue.depth() == 0
    harness.queue.down = False  # type: ignore[attr-defined]
    sweep(harness.deps)
    harness.drain()
    assert harness.branch(branch_id).status == BranchStatus.COMPLETED


def test_broker_outage_at_fork_loses_nothing(clean_db: SessionFactory) -> None:
    harness = harness_with_broken_queue(clean_db, [tool_reply(), text_reply(), text_reply("fork")])
    branch_id = start(harness)
    harness.drain()
    user = harness.nodes(branch_id)[1]
    harness.queue.down = True  # type: ignore[attr-defined]

    child = fork_from_edit(harness.deps, harness.user.id, user.id, {"text": "other"})

    assert harness.branch(child).status == BranchStatus.QUEUED
    harness.queue.down = False  # type: ignore[attr-defined]
    sweep(harness.deps)
    harness.drain()
    assert harness.branch(child).status == BranchStatus.COMPLETED


def test_a_message_lost_by_the_broker_is_resent(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()])
    branch_id = start(harness)
    harness.queue.messages.clear()

    assert sweep(harness.deps).requeued == 0

    harness.clock.value += timedelta(seconds=QUEUED_RESEND_SECONDS + 1)
    result = sweep(harness.deps)
    assert (result.requeued, result.dispatched) == (1, 1)
    harness.drain()
    assert harness.branch(branch_id).status == BranchStatus.COMPLETED


def test_dead_worker_branch_resumes_through_the_sweeper(clean_db: SessionFactory) -> None:
    llm = ControlledLLM(text_reply("recovered"))
    llm.during = _die
    harness = Harness(clean_db, llm)
    branch_id = start(harness)
    message = harness.queue.messages.popleft()
    with pytest.raises(WorkerDied):
        run_step(harness.deps, message)
    llm.during = lambda: None

    assert sweep(harness.deps).requeued == 0

    harness.clock.value += timedelta(seconds=harness.deps.lease_seconds + 1)
    assert sweep(harness.deps).requeued == 1
    harness.drain()
    assert harness.branch(branch_id).status == BranchStatus.COMPLETED


def test_step_that_keeps_killing_workers_fails_instead_of_looping(clean_db: SessionFactory) -> None:
    llm = ControlledLLM(text_reply())
    llm.during = _die
    harness = Harness(clean_db, llm)
    branch_id = start(harness)
    message = harness.queue.messages.popleft()

    for _ in range(harness.deps.max_step_attempts):
        with pytest.raises(WorkerDied):
            run_step(harness.deps, message)
        harness.clock.value += timedelta(seconds=harness.deps.lease_seconds + 1)

    assert run_step(harness.deps, message) == StepOutcome.FAILED
    branch = harness.branch(branch_id)
    assert branch.status == BranchStatus.FAILED
    assert "attempts" in (branch.status_reason or "")
    assert branch.lease_until is None


def test_attempts_reset_when_a_step_succeeds(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply()])
    branch_id = start(harness)
    run_step(harness.deps, harness.queue.messages.popleft())
    assert harness.branch(branch_id).step_attempts == 0


def test_sweeping_finished_branches_does_nothing(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()])
    start(harness)
    harness.drain()
    harness.clock.value += timedelta(days=1)
    result = sweep(harness.deps)
    assert (result.requeued, result.dispatched) == (0, 0)
