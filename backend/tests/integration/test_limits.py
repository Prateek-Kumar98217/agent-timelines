import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.models import BranchStatus, User
from app.db.session import SessionFactory
from app.domain.engine import StepOutcome, cancel_branch, start_task
from app.domain.forking import fork_from_edit
from app.domain.limits import AdmissionRejectedError, Limits, UserLimits
from tests.helpers import CONFIG, GENEROUS_LIMITS, Harness, text_reply, tool_reply
from tests.integration.test_api import Api


def limits(**overrides: Any) -> Limits:
    queued = overrides.pop("max_queued_branches", 10_000)
    base = UserLimits(1_000, 1_000, 1_000_000)
    return Limits({"local": replace(base, **overrides)}, queued)


def rejected_reason(call: Any) -> str:
    with pytest.raises(AdmissionRejectedError) as error:
        call()
    return error.value.reason


def test_the_active_branch_cap_counts_queued_running_and_waiting(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()], limits(active_branches=2))
    start_task(harness.deps, harness.user.id, "one", CONFIG)
    _, second = start_task(harness.deps, harness.user.id, "two", CONFIG)
    assert (
        rejected_reason(lambda: start_task(harness.deps, harness.user.id, "three", CONFIG))
        == "active_branches"
    )
    cancel_branch(harness.deps, second)
    start_task(harness.deps, harness.user.id, "three", CONFIG)
    harness.queue.messages.clear()


def test_finishing_a_branch_frees_a_slot(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply(), text_reply()], limits(active_branches=1))
    start_task(harness.deps, harness.user.id, "one", CONFIG)
    assert harness.drain() == [StepOutcome.EXECUTED]
    start_task(harness.deps, harness.user.id, "two", CONFIG)
    harness.queue.messages.clear()


def test_forks_count_toward_the_cap_and_a_refused_one_creates_nothing(
    clean_db: SessionFactory,
) -> None:
    harness = Harness(clean_db, [text_reply()], limits(active_branches=1))
    _, main = start_task(harness.deps, harness.user.id, "t", CONFIG)
    harness.drain()
    user = harness.nodes(main)[1]
    first = fork_from_edit(harness.deps, harness.user.id, user.id, {"text": "a"})
    assert harness.branch(first).status == BranchStatus.QUEUED
    depth = harness.queue.depth()
    assert (
        rejected_reason(
            lambda: fork_from_edit(harness.deps, harness.user.id, user.id, {"text": "b"})
        )
        == "active_branches"
    )
    assert harness.queue.depth() == depth
    harness.queue.messages.clear()


def test_a_fork_that_closes_at_once_needs_no_slot(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()], limits(active_branches=1))
    _, main = start_task(harness.deps, harness.user.id, "t", CONFIG)
    harness.drain()
    llm_output = harness.nodes(main)[2]
    start_task(harness.deps, harness.user.id, "uses the only slot", CONFIG)
    fork = fork_from_edit(harness.deps, harness.user.id, llm_output.id, {"text": "typed answer"})
    assert harness.branch(fork).status == BranchStatus.COMPLETED
    harness.queue.messages.clear()


def test_concurrent_forks_cant_exceed_the_cap(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()], limits(active_branches=3))
    _, main = start_task(harness.deps, harness.user.id, "t", CONFIG)
    harness.drain()
    user = harness.nodes(main)[1]
    barrier = threading.Barrier(12)

    def attempt(i: int) -> UUID | str:
        barrier.wait()
        try:
            return fork_from_edit(harness.deps, harness.user.id, user.id, {"text": f"task {i}"})
        except AdmissionRejectedError as error:
            return error.reason

    with ThreadPoolExecutor(12) as pool:
        results = list(pool.map(attempt, range(12)))

    assert sum(isinstance(result, UUID) for result in results) == 3
    assert results.count("active_branches") == 9
    harness.queue.messages.clear()


def test_the_tree_limit(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()], limits(trees=1))
    start_task(harness.deps, harness.user.id, "one", CONFIG)
    assert (
        rejected_reason(lambda: start_task(harness.deps, harness.user.id, "two", CONFIG)) == "trees"
    )
    harness.queue.messages.clear()


def test_the_queue_limit_is_global_and_ignores_waiting_branches(clean_db: SessionFactory) -> None:
    table = limits(max_queued_branches=1)
    harness = Harness(clean_db, [text_reply()], table)
    other = User(id=uuid4(), kind="local")
    with clean_db() as session:
        session.add(other)
        session.commit()
    start_task(harness.deps, harness.user.id, "queued", CONFIG)
    assert (
        rejected_reason(lambda: start_task(harness.deps, other.id, "refused", CONFIG))
        == "queue_full"
    )
    harness.drain()
    start_task(harness.deps, other.id, "now accepted", CONFIG)
    harness.queue.messages.clear()


def test_daily_tokens_stop_a_running_branch_before_its_next_call(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply(tokens=30), text_reply()], limits(tokens_per_day=50))
    _, branch = start_task(harness.deps, harness.user.id, "t", CONFIG)

    outcomes = harness.drain()

    assert outcomes == [StepOutcome.EXECUTED, StepOutcome.EXECUTED, StepOutcome.STOPPED]
    assert len(harness.llm.requests) == 1
    state = harness.branch(branch)
    assert state.status == BranchStatus.BUDGET_EXHAUSTED
    assert "daily token limit" in (state.status_reason or "")


def test_no_new_work_starts_once_daily_tokens_are_gone(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply(tokens=30)], limits(tokens_per_day=50))
    start_task(harness.deps, harness.user.id, "t", CONFIG)
    harness.drain()
    assert (
        rejected_reason(lambda: start_task(harness.deps, harness.user.id, "more", CONFIG))
        == "tokens_per_day"
    )


def test_a_failed_token_charge_doesnt_lose_the_step(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()], GENEROUS_LIMITS)
    _, branch = start_task(harness.deps, harness.user.id, "t", CONFIG)

    def broken(key: str, amount: int, window_seconds: int) -> None:
        raise ConnectionError("redis down")

    harness.rate_limiter.charge = broken  # type: ignore[method-assign]
    harness.drain()
    assert harness.branch(branch).status == BranchStatus.COMPLETED


def api_with(session_factory: SessionFactory, table: Limits) -> Api:
    api = Api(session_factory)
    api.harness.deps.limits = table
    return api


def test_api_returns_429_with_a_reason_and_retry_after(clean_db: SessionFactory) -> None:
    api = api_with(clean_db, limits(active_branches=1))
    api.start("one")
    response = api.client.post("/api/trees", json={"task": "two"})
    assert response.status_code == 429
    assert response.json()["reason"] == "active_branches"
    assert "cancel" in response.json()["detail"]
    assert int(response.headers["Retry-After"]) > 0
    api.harness.queue.messages.clear()


def test_api_writes_are_rate_limited_per_user(clean_db: SessionFactory) -> None:
    api = api_with(clean_db, limits(writes_per_minute=2))
    client: TestClient = api.client
    api.start("one")
    api.start("two")
    response = client.post("/api/trees", json={"task": "three"})
    assert response.status_code == 429
    assert response.json()["reason"] == "rate_limited"
    assert client.get("/api/trees").status_code == 200
    api.harness.queue.messages.clear()


def test_malformed_writes_count_toward_the_rate_limit(clean_db: SessionFactory) -> None:
    api = api_with(clean_db, limits(writes_per_minute=1))
    assert api.client.post("/api/trees", json={"task": ""}).status_code == 422
    assert api.client.post("/api/trees", json={"task": "real"}).status_code == 429


def test_rejections_are_recorded_by_reason(clean_db: SessionFactory) -> None:
    api = api_with(clean_db, limits(active_branches=1))
    recorded: list[tuple[str, float, Any]] = []

    def record(metric: str, value: float, attributes: Any = None) -> None:
        recorded.append((metric, value, attributes))

    api.adapters.telemetry.record = record  # type: ignore[method-assign]
    api.start("one")
    api.client.post("/api/trees", json={"task": "two"})
    assert recorded == [("admission.rejected", 1, {"reason": "active_branches"})]
    api.harness.queue.messages.clear()


def test_default_limits() -> None:
    limits_for_local = Limits().for_kind("local")
    assert limits_for_local.active_branches == 5
    assert limits_for_local.tokens_per_day == 200_000
