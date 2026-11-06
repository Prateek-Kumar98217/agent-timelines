"""A finished branch can continue with another message, gaining turns only if it has none left."""

from uuid import UUID

import pytest

from app.db.models import BranchStatus, NodeType
from app.db.session import SessionFactory
from app.domain.config import EXTRA_TURNS_PER_MESSAGE, TreeConfig
from app.domain.engine import ContinueRefusedError, cancel_branch, continue_branch, start_task
from app.domain.forking import fork_from_edit
from app.domain.limits import AdmissionRejectedError, Limits, UserLimits
from tests.helpers import CONFIG, Harness, text_reply, tool_reply

SHORT = TreeConfig(system_prompt="sys", tools=CONFIG.tools, turn_cap=2)


def capped(clean_db: SessionFactory, *later: object, **kwargs: object) -> tuple[Harness, UUID]:
    """Run a branch to its limit of 2 turns, leaving `later` replies for afterwards."""
    harness = Harness(clean_db, [tool_reply(), tool_reply(), *later], **kwargs)  # type: ignore[arg-type]
    _, branch = start_task(harness.deps, harness.user.id, "the task", SHORT)
    harness.drain()
    assert harness.branch(branch).status == BranchStatus.CAPPED
    return harness, branch


def test_continuing_adds_a_message_and_runs_the_model(clean_db: SessionFactory) -> None:
    harness, branch = capped(clean_db, text_reply("a follow-up answer"))
    before = {node.id: node.payload for node in harness.nodes(branch)}

    continue_branch(harness.deps, harness.user.id, branch, "Now check the other source")

    assert harness.branch(branch).status == BranchStatus.QUEUED
    assert harness.branch(branch).finished_at is None
    harness.drain()
    nodes = harness.nodes(branch)
    assert harness.branch(branch).status == BranchStatus.COMPLETED
    assert [node.type for node in nodes[-2:]] == [NodeType.USER_MSG, NodeType.LLM_OUTPUT]
    assert nodes[-1].payload["text"] == "a follow-up answer"
    assert harness.llm.requests[-1].messages[-1] == {
        "role": "user",
        "content": "Now check the other source",
    }
    # What already ran is exactly as it was.
    assert {node.id: node.payload for node in nodes if node.id in before} == before


def test_each_message_adds_four_turns_and_the_limit_still_holds(clean_db: SessionFactory) -> None:
    replies = [tool_reply() for _ in range(EXTRA_TURNS_PER_MESSAGE)]
    harness, branch = capped(clean_db, *replies)

    continue_branch(harness.deps, harness.user.id, branch, "Keep going")
    harness.drain()

    row = harness.branch(branch)
    assert row.status == BranchStatus.CAPPED
    assert row.extra_turns == EXTRA_TURNS_PER_MESSAGE
    assert row.turns_used == SHORT.turn_cap + EXTRA_TURNS_PER_MESSAGE


def test_a_capped_branch_can_continue_again(clean_db: SessionFactory) -> None:
    replies = [tool_reply() for _ in range(EXTRA_TURNS_PER_MESSAGE)]
    harness, branch = capped(clean_db, *replies, text_reply("finally"))

    continue_branch(harness.deps, harness.user.id, branch, "Keep going")
    harness.drain()
    continue_branch(harness.deps, harness.user.id, branch, "One more")
    harness.drain()

    assert harness.branch(branch).status == BranchStatus.COMPLETED
    assert harness.branch(branch).extra_turns == 2 * EXTRA_TURNS_PER_MESSAGE


def test_a_branch_that_answered_on_its_last_turn_can_continue(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply(), text_reply("my answer"), text_reply("more")])
    _, branch = start_task(harness.deps, harness.user.id, "the task", SHORT)
    harness.drain()
    assert harness.branch(branch).status == BranchStatus.COMPLETED
    assert harness.branch(branch).turns_used == SHORT.turn_cap

    continue_branch(harness.deps, harness.user.id, branch, "Check that again")
    harness.drain()

    assert harness.nodes(branch)[-1].payload["text"] == "more"


def test_a_branch_with_turns_left_continues_without_gaining_more(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply("first"), text_reply("second")])
    _, branch = start_task(harness.deps, harness.user.id, "the task", CONFIG)
    harness.drain()
    assert harness.branch(branch).turns_used == 1

    continue_branch(harness.deps, harness.user.id, branch, "Tell me more")
    harness.drain()

    row = harness.branch(branch)
    assert row.status == BranchStatus.COMPLETED
    assert row.turns_used == 2
    assert row.extra_turns == 0
    assert harness.nodes(branch)[-1].payload["text"] == "second"


def test_a_branch_still_running_cant_continue(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()])
    _, branch = start_task(harness.deps, harness.user.id, "the task", CONFIG)
    with pytest.raises(ContinueRefusedError, match="finished"):
        continue_branch(harness.deps, harness.user.id, branch, "Too early")


def test_a_cancelled_branch_cant_continue(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply()])
    _, branch = start_task(harness.deps, harness.user.id, "the task", CONFIG)
    cancel_branch(harness.deps, branch)
    with pytest.raises(ContinueRefusedError, match="finished"):
        continue_branch(harness.deps, harness.user.id, branch, "Come back")


@pytest.mark.parametrize(
    ("text", "message"),
    [("   ", "blank"), ("Write a Python script instead", "not coding")],
)
def test_a_bad_message_changes_nothing(clean_db: SessionFactory, text: str, message: str) -> None:
    harness, branch = capped(clean_db)
    count = len(harness.nodes(branch))
    with pytest.raises(ContinueRefusedError, match=message):
        continue_branch(harness.deps, harness.user.id, branch, text)
    assert harness.branch(branch).status == BranchStatus.CAPPED
    assert len(harness.nodes(branch)) == count


def test_a_branch_out_of_tokens_cant_continue(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply(tokens=500), tool_reply(tokens=500)])
    config = SHORT.model_copy(update={"branch_token_budget": 2_000})
    _, branch = start_task(harness.deps, harness.user.id, "the task", config)
    harness.drain()
    assert harness.branch(branch).status == BranchStatus.CAPPED
    with pytest.raises(ContinueRefusedError, match="tokens"):
        continue_branch(harness.deps, harness.user.id, branch, "More please")


def test_continuing_counts_against_the_active_branch_limit(clean_db: SessionFactory) -> None:
    limits = Limits({"local": UserLimits(1, 100, 1_000_000)})
    harness, branch = capped(clean_db, limits=limits)
    start_task(harness.deps, harness.user.id, "another task", SHORT)
    with pytest.raises(AdmissionRejectedError):
        continue_branch(harness.deps, harness.user.id, branch, "Keep going")
    assert harness.branch(branch).status == BranchStatus.CAPPED


def test_a_fork_after_the_message_inherits_the_extra_turns(clean_db: SessionFactory) -> None:
    harness, branch = capped(clean_db, text_reply(), text_reply())
    continue_branch(harness.deps, harness.user.id, branch, "Keep going")
    harness.drain()
    answer = harness.nodes(branch)[-1]

    after = fork_from_edit(
        harness.deps,
        harness.user.id,
        answer.id,
        {"text": None, "tool_call": {"name": "search", "arguments": {"query": "q"}}},
    )
    assert harness.branch(after).extra_turns == EXTRA_TURNS_PER_MESSAGE


def test_a_fork_after_a_message_that_earned_nothing_gains_nothing(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [text_reply("first"), text_reply("second")])
    _, branch = start_task(harness.deps, harness.user.id, "the task", CONFIG)
    harness.drain()
    continue_branch(harness.deps, harness.user.id, branch, "Tell me more")
    harness.drain()

    fork = fork_from_edit(
        harness.deps, harness.user.id, harness.nodes(branch)[-1].id, {"text": "x"}
    )
    assert harness.branch(fork).extra_turns == 0


def test_a_fork_before_the_message_doesnt(clean_db: SessionFactory) -> None:
    harness, branch = capped(clean_db, text_reply())
    continue_branch(harness.deps, harness.user.id, branch, "Keep going")
    harness.drain()
    first_decision = next(
        node for node in harness.nodes(branch) if node.type == NodeType.LLM_OUTPUT
    )

    before = fork_from_edit(harness.deps, harness.user.id, first_decision.id, {"text": "Done"})
    assert harness.branch(before).extra_turns == 0


def test_editing_a_follow_up_message_runs_the_model_on_a_new_branch(
    clean_db: SessionFactory,
) -> None:
    harness, branch = capped(clean_db, text_reply("first"), text_reply("second"))
    continue_branch(harness.deps, harness.user.id, branch, "Keep going")
    harness.drain()
    message = next(
        node
        for node in harness.nodes(branch)
        if node.type == NodeType.USER_MSG and node.payload["text"] == "Keep going"
    )

    fork = fork_from_edit(harness.deps, harness.user.id, message.id, {"text": "Go another way"})
    harness.drain()

    assert harness.nodes(fork)[-1].payload["text"] == "second"
    assert harness.branch(fork).extra_turns == EXTRA_TURNS_PER_MESSAGE
    assert harness.nodes(branch)[-1].payload["text"] == "first"
