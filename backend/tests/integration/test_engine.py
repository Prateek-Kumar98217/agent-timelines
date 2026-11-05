from collections.abc import Callable
from datetime import timedelta
from itertools import pairwise
from uuid import UUID

import pytest
from sqlalchemy import select

from app.adapters.memory import ScriptedLLM
from app.db.models import BranchStatus, NextAction, TreeEventRow
from app.db.session import SessionFactory
from app.domain.engine import (
    EngineDeps,
    StepOutcome,
    _claim,
    cancel_branch,
    run_step,
    start_task,
)
from app.domain.sweeper import sweep
from app.ports import (
    LLMRequest,
    LLMResponse,
    ProviderRateLimitedError,
    StepMessage,
    Usage,
)
from tests.helpers import CONFIG, Harness, text_reply, tool_reply


def start(harness: Harness, turn_cap: int = 12, budget: int = 20_000) -> UUID:
    config = CONFIG.model_copy(update={"turn_cap": turn_cap, "branch_token_budget": budget})
    _, branch_id = start_task(harness.deps, harness.user.id, "the task", config)
    return branch_id


def test_run_records_every_node_in_order_and_completes(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply("first", text="looking"), text_reply("the answer")])
    branch_id = start(harness)
    assert harness.branch(branch_id).status == BranchStatus.QUEUED

    outcomes = harness.drain()

    assert outcomes == [StepOutcome.EXECUTED] * 3
    assert harness.types(branch_id) == [
        "root",
        "user_msg",
        "llm_output",
        "tool_call",
        "tool_result",
        "llm_output",
    ]
    nodes = harness.nodes(branch_id)
    assert [node.turn_index for node in nodes] == [0, 0, 1, 1, 1, 2]
    assert all(child.parent_id == parent.id for parent, child in pairwise(nodes))
    branch = harness.branch(branch_id)
    assert branch.status == BranchStatus.COMPLETED
    assert branch.turns_used == 2
    assert (branch.input_tokens, branch.output_tokens) == (20, 20)
    assert branch.finished_at is not None
    assert harness.state(branch_id).next_action == NextAction.NONE


def test_llm_receives_the_tool_result_on_the_next_turn(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply("first"), text_reply()])
    start(harness)
    harness.drain()
    messages = harness.llm.requests[1].messages
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "tool"]
    assert messages[3]["content"].startswith("result of search")


def test_each_message_runs_one_step_and_nothing_stays_running(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply(), text_reply()])
    branch_id = start(harness)
    message = harness.queue.messages.popleft()
    assert run_step(harness.deps, message) == StepOutcome.EXECUTED
    assert harness.types(branch_id)[-1] == "tool_call"
    branch = harness.branch(branch_id)
    assert branch.status == BranchStatus.QUEUED
    assert branch.lease_until is None
    assert harness.queue.depth() == 1


def test_last_allowed_turn_forces_a_final_answer(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply(), tool_reply(), text_reply("forced")])
    branch_id = start(harness, turn_cap=3)
    harness.drain()
    assert [request.force_final for request in harness.llm.requests] == [False, False, True]
    assert harness.llm.requests[2].tools == []
    branch = harness.branch(branch_id)
    assert branch.status == BranchStatus.COMPLETED
    assert branch.turns_used == 3


def test_model_ignoring_force_final_ends_capped_with_no_extra_turn(
    clean_db: SessionFactory,
) -> None:
    harness = Harness(clean_db, [tool_reply(), tool_reply()])
    branch_id = start(harness, turn_cap=2)
    harness.drain()
    branch = harness.branch(branch_id)
    assert branch.status == BranchStatus.CAPPED
    assert branch.turns_used == 2
    assert harness.types(branch_id)[-1] == "llm_output"
    last = harness.nodes(branch_id)[-1]
    assert last.payload["tool_call"] is None
    assert last.payload["dropped_tool_calls"] == 1


def test_token_budget_stops_before_calling_the_llm(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply(tokens=30)])
    branch_id = start(harness, budget=50)
    outcomes = harness.drain()
    assert outcomes == [StepOutcome.EXECUTED, StepOutcome.EXECUTED, StepOutcome.STOPPED]
    assert len(harness.llm.requests) == 1
    assert harness.branch(branch_id).status == BranchStatus.BUDGET_EXHAUSTED


def test_empty_model_response_fails_the_branch(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [LLMResponse(usage=Usage(1, 1))])
    branch_id = start(harness)
    assert harness.drain() == [StepOutcome.FAILED]
    assert harness.branch(branch_id).status == BranchStatus.FAILED


def test_llm_error_fails_the_branch_with_a_reason(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [ValueError("boom")])
    branch_id = start(harness)
    assert harness.drain() == [StepOutcome.FAILED]
    branch = harness.branch(branch_id)
    assert branch.status == BranchStatus.FAILED
    assert branch.status_reason == "ValueError: boom"
    assert branch.lease_until is None


def test_rate_limit_waits_with_no_lease_and_retries_same_step(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [ProviderRateLimitedError(30), text_reply()])
    branch_id = start(harness)
    message = harness.queue.messages.popleft()

    assert run_step(harness.deps, message) == StepOutcome.WAITING

    branch = harness.branch(branch_id)
    assert branch.status == BranchStatus.WAITING
    assert branch.lease_until is None
    assert harness.types(branch_id) == ["root", "user_msg"]
    assert harness.queue.depth() == 0

    assert sweep(harness.deps).dispatched == 0
    assert harness.queue.depth() == 0

    harness.clock.value += timedelta(seconds=30)
    assert sweep(harness.deps).dispatched == 1
    retry = harness.queue.messages.popleft()
    assert retry.seq == message.seq
    assert run_step(harness.deps, retry) == StepOutcome.EXECUTED
    assert harness.branch(branch_id).status == BranchStatus.COMPLETED


def test_duplicate_delivery_creates_no_duplicate_nodes_or_calls(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply(), text_reply()])
    branch_id = start(harness)
    message = harness.queue.messages.popleft()
    assert run_step(harness.deps, message) == StepOutcome.EXECUTED
    before = harness.types(branch_id)

    assert run_step(harness.deps, message) == StepOutcome.SKIPPED_DUPLICATE

    assert harness.types(branch_id) == before
    assert len(harness.llm.requests) == 1


def test_message_for_finished_branch_is_skipped(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()])
    branch_id = start(harness)
    harness.drain()
    assert run_step(harness.deps, StepMessage(branch_id, 1)) == StepOutcome.SKIPPED_FINISHED


class WorkerDied(BaseException):
    """Simulates a killed process: no exception handler in the engine can catch it."""


class ControlledLLM:
    """Runs a callback in the middle of the LLM call, while no transaction is open."""

    def __init__(self, reply: LLMResponse) -> None:
        self.reply = reply
        self.during: Callable[[], None] = lambda: None
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        self.during()
        return self.reply


def _die() -> None:
    raise WorkerDied


def test_crashed_worker_step_is_blocked_by_lease_then_resumes(clean_db: SessionFactory) -> None:
    llm = ControlledLLM(text_reply("recovered"))
    llm.during = _die
    harness = Harness(clean_db, llm)
    branch_id = start(harness)
    message = harness.queue.messages.popleft()

    with pytest.raises(WorkerDied):
        run_step(harness.deps, message)
    assert harness.branch(branch_id).status == BranchStatus.RUNNING

    llm.during = lambda: None
    assert run_step(harness.deps, message) == StepOutcome.SKIPPED_LEASED

    harness.clock.value += timedelta(seconds=harness.deps.lease_seconds + 1)
    assert run_step(harness.deps, message) == StepOutcome.EXECUTED
    assert harness.branch(branch_id).status == BranchStatus.COMPLETED
    assert harness.types(branch_id) == ["root", "user_msg", "llm_output"]


def test_result_is_discarded_if_branch_was_cancelled_mid_step(clean_db: SessionFactory) -> None:
    llm = ControlledLLM(tool_reply())
    harness = Harness(clean_db, llm)
    branch_id = start(harness)
    message = harness.queue.messages.popleft()

    def cancel() -> None:
        cancel_branch(harness.deps, branch_id)

    llm.during = cancel

    assert run_step(harness.deps, message) == StepOutcome.DISCARDED_STALE

    assert harness.branch(branch_id).status == BranchStatus.CANCELLED
    assert harness.types(branch_id) == ["root", "user_msg"]
    assert harness.queue.depth() == 0


def test_result_is_discarded_if_lease_expired_and_another_worker_took_over(
    clean_db: SessionFactory,
) -> None:
    llm = ControlledLLM(tool_reply())
    harness = Harness(clean_db, llm)
    second_worker = EngineDeps(
        session_factory=clean_db,
        queue=harness.queue,
        llm=ScriptedLLM([text_reply("second worker wins")]),
        tools=harness.tools,
        live_events=harness.events,
        now=harness.clock,
    )
    branch_id = start(harness)
    message = harness.queue.messages.popleft()
    second: list[StepOutcome] = []

    def lease_expires_and_second_worker_runs() -> None:
        harness.clock.value += timedelta(seconds=harness.deps.lease_seconds + 1)
        second.append(run_step(second_worker, message))

    llm.during = lease_expires_and_second_worker_runs

    assert run_step(harness.deps, message) == StepOutcome.DISCARDED_STALE

    assert second == [StepOutcome.EXECUTED]
    nodes = harness.nodes(branch_id)
    assert [node.type for node in nodes] == ["root", "user_msg", "llm_output"]
    assert nodes[-1].payload["text"] == "second worker wins"


def test_expired_worker_cannot_commit_over_the_worker_now_holding_the_lease(
    clean_db: SessionFactory,
) -> None:
    llm = ControlledLLM(tool_reply())
    harness = Harness(clean_db, llm)
    branch_id = start(harness)
    message = harness.queue.messages.popleft()
    claims: list[object] = []

    def lease_expires_and_second_worker_claims() -> None:
        harness.clock.value += timedelta(seconds=harness.deps.lease_seconds + 1)
        claims.append(_claim(harness.deps, message))

    llm.during = lease_expires_and_second_worker_claims

    assert run_step(harness.deps, message) == StepOutcome.DISCARDED_STALE

    assert harness.types(branch_id) == ["root", "user_msg"]
    branch = harness.branch(branch_id)
    assert branch.status == BranchStatus.RUNNING
    assert branch.lease_owner is not None
    assert branch.lease_owner == claims[0].lease_owner  # type: ignore[attr-defined]


def test_cancel_only_affects_non_completed_branches(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()])
    branch_id = start(harness)
    harness.drain()
    assert cancel_branch(harness.deps, branch_id) is False
    assert harness.branch(branch_id).status == BranchStatus.COMPLETED


def test_events_are_numbered_without_gaps_and_match_what_was_published(
    clean_db: SessionFactory,
) -> None:
    harness = Harness(clean_db, [tool_reply(), text_reply()])
    start(harness)
    harness.drain()
    with clean_db() as session:
        rows = list(session.execute(select(TreeEventRow).order_by(TreeEventRow.seq)).scalars())
    assert [row.seq for row in rows] == list(range(1, len(rows) + 1))
    assert [event.seq for event in harness.events.published] == [row.seq for row in rows]
    assert rows[-1].type == "branch_status"
    assert rows[-1].payload["status"] == "completed"
