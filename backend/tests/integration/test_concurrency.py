"""Real threads against real Postgres. Row locks, not test ordering, must keep this correct."""

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from sqlalchemy import select

from app.adapters.mock import MockLLM
from app.db.models import BranchStatus, Node, TreeEventRow
from app.db.session import SessionFactory
from app.domain.engine import StepOutcome, run_step, start_task
from app.domain.forking import fork_from_edit
from app.domain.outbox import add_step
from app.domain.sweeper import sweep
from app.ports import LLMRequest, LLMResponse
from tests.helpers import CONFIG, Harness, text_reply

WORKERS = 8


def run_all(harness: Harness, branch_ids: list[UUID]) -> None:
    """Run the queue with a pool of workers until every branch has finished."""
    stop = threading.Event()
    errors: list[BaseException] = []

    def worker() -> None:
        while not stop.is_set():
            try:
                message = harness.queue.messages.popleft()
            except IndexError:
                stop.wait(0.005)
                continue
            try:
                run_step(harness.deps, message)
            except BaseException as exc:
                errors.append(exc)
                stop.set()

    with ThreadPoolExecutor(WORKERS) as pool:
        for _ in range(WORKERS):
            pool.submit(worker)
        done = threading.Event()
        for _ in range(4000):
            statuses = [harness.branch(branch_id).status for branch_id in branch_ids]
            if all(status == BranchStatus.COMPLETED for status in statuses) or stop.is_set():
                done.set()
                break
            done.wait(0.01)
        stop.set()
    assert errors == []
    assert all(harness.branch(b).status == BranchStatus.COMPLETED for b in branch_ids)


def assert_events_gapless(session_factory: SessionFactory, tree_id: UUID) -> None:
    with session_factory() as session:
        seqs = list(
            session.execute(
                select(TreeEventRow.seq)
                .where(TreeEventRow.tree_id == tree_id)
                .order_by(TreeEventRow.seq)
            ).scalars()
        )
    assert seqs == list(range(1, len(seqs) + 1))


def test_one_message_delivered_to_many_workers_runs_once(clean_db: SessionFactory) -> None:
    class Slow:
        def __init__(self) -> None:
            self.calls = 0
            self.lock = threading.Lock()

        def complete(self, request: LLMRequest) -> LLMResponse:
            with self.lock:
                self.calls += 1
            threading.Event().wait(0.2)
            return text_reply()

    llm = Slow()
    harness = Harness(clean_db, llm)
    _, branch_id = start_task(harness.deps, harness.user.id, "t", CONFIG)
    message = harness.queue.messages.popleft()
    barrier = threading.Barrier(WORKERS)

    def deliver() -> StepOutcome:
        barrier.wait()
        return run_step(harness.deps, message)

    with ThreadPoolExecutor(WORKERS) as pool:
        outcomes = Counter(pool.map(lambda _: deliver(), range(WORKERS)))

    assert llm.calls == 1
    assert outcomes[StepOutcome.EXECUTED] == 1
    assert (
        outcomes[StepOutcome.EXECUTED]
        + sum(
            outcomes[o]
            for o in (
                StepOutcome.SKIPPED_LEASED,
                StepOutcome.SKIPPED_DUPLICATE,
                StepOutcome.SKIPPED_FINISHED,
            )
        )
        == WORKERS
    )
    assert harness.types(branch_id) == ["root", "user_msg", "llm_output"]


def test_many_forks_run_in_parallel_without_interfering(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, MockLLM(latency_seconds=0.005, tool_turns=2))
    tree_id, main = start_task(harness.deps, harness.user.id, "main task", CONFIG)
    run_all(harness, [main])
    nodes = harness.nodes(main)
    user, result = nodes[1], next(n for n in nodes if n.type == "tool_result")

    forks = [
        fork_from_edit(harness.deps, harness.user.id, user.id, {"text": f"task {i}"})
        for i in range(10)
    ] + [
        fork_from_edit(harness.deps, harness.user.id, result.id, {"content": f"edit {i}"})
        for i in range(10)
    ]
    main_before = [(n.id, n.payload) for n in harness.nodes(main)]

    run_all(harness, forks)

    for i, branch_id in enumerate(forks[:10]):
        messages = harness.state(branch_id).messages
        assert messages[0]["content"] == f"task {i}"
        assert f"task {i}" in messages[-1]["content"]
        assert harness.types(branch_id)[-1] == "llm_output"
    for i, branch_id in enumerate(forks[10:]):
        assert harness.state(branch_id).messages[2]["content"] == f"edit {i}"
    assert [(n.id, n.payload) for n in harness.nodes(main)] == main_before
    assert_events_gapless(clean_db, tree_id)


def test_forks_made_concurrently_while_the_source_runs(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, MockLLM(latency_seconds=0.01, tool_turns=3))
    tree_id, main = start_task(harness.deps, harness.user.id, "main task", CONFIG)
    user = harness.nodes(main)[1]
    barrier = threading.Barrier(WORKERS)

    def fork(i: int) -> UUID:
        barrier.wait()
        return fork_from_edit(harness.deps, harness.user.id, user.id, {"text": f"task {i}"})

    with ThreadPoolExecutor(WORKERS) as pool:
        forks = list(pool.map(fork, range(WORKERS)))

    run_all(harness, [main, *forks])

    assert len(set(forks)) == WORKERS
    assert harness.state(main).messages[0]["content"] == "main task"
    assert_events_gapless(clean_db, tree_id)


def test_concurrent_sweeps_send_each_row_once(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()])
    _, branch_id = start_task(harness.deps, harness.user.id, "t", CONFIG)
    harness.queue.messages.clear()
    with clean_db() as session:
        add_step(session, branch_id, 0, harness.clock())
        session.commit()
    barrier = threading.Barrier(WORKERS)

    def concurrent_sweep(_: int) -> int:
        barrier.wait()
        return sweep(harness.deps).dispatched

    with ThreadPoolExecutor(WORKERS) as pool:
        total = sum(pool.map(concurrent_sweep, range(WORKERS)))

    assert total == 1
    assert harness.queue.depth() == 1


@pytest.mark.parametrize("round_number", range(3))
def test_node_sequence_stays_unique_per_branch_under_load(
    clean_db: SessionFactory, round_number: int
) -> None:
    harness = Harness(clean_db, MockLLM(tool_turns=2))
    _, main = start_task(harness.deps, harness.user.id, f"task {round_number}", CONFIG)
    for _ in range(4):
        harness.queue.enqueue(harness.queue.messages[0])
    run_all(harness, [main])
    with clean_db() as session:
        seqs = list(session.execute(select(Node.seq).where(Node.branch_id == main)).scalars())
    assert sorted(seqs) == list(range(len(seqs)))
    assert harness.types(main)[-1] == "llm_output"
