from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from app.adapters.memory import HashEmbedder
from app.db.models import BranchStatus, NextAction, Node, NodeType
from app.db.session import SessionFactory
from app.domain.engine import StepOutcome, run_step, start_task
from app.domain.forking import (
    InvalidEditError,
    NodeNotFoundError,
    NoOpEditError,
    NotEditableError,
    NoTurnsLeftError,
    fork_from_edit,
)
from app.domain.queries import path_to
from app.domain.scratchpad import search_notes, write_note
from app.ports import StepMessage, ToolContext, ToolOutcome
from tests.helpers import CONFIG, Harness, text_reply, tool_reply


def completed_run(
    session_factory: SessionFactory, *extra_replies: Any, turn_cap: int = 12
) -> tuple[Harness, UUID, dict[str, Node]]:
    """Run tool call -> result -> final answer, leaving replies queued for forks to use."""
    harness = Harness(
        session_factory,
        [tool_reply("original", text="thinking"), text_reply("original answer"), *extra_replies],
    )
    config = CONFIG.model_copy(update={"turn_cap": turn_cap})
    _, branch_id = start_task(harness.deps, harness.user.id, "the task", config)
    harness.drain()
    nodes = harness.nodes(branch_id)
    by_name = {
        "root": nodes[0],
        "user": nodes[1],
        "llm_tool": nodes[2],
        "call": nodes[3],
        "result": nodes[4],
        "final": nodes[5],
    }
    return harness, branch_id, by_name


def snapshot(harness: Harness) -> list[tuple[Any, ...]]:
    with harness.session_factory() as session:
        rows = session.execute(select(Node).order_by(Node.created_at, Node.id)).scalars()
        return [
            (n.id, n.branch_id, n.parent_id, n.type, n.payload, n.seq, n.turn_index) for n in rows
        ]


# The edit table


def test_editing_user_msg_runs_the_llm_live_with_the_new_text(clean_db: SessionFactory) -> None:
    harness, _, nodes = completed_run(clean_db, text_reply("new answer"))
    calls_before = len(harness.llm.requests)

    branch = fork_from_edit(harness.deps, harness.user.id, nodes["user"].id, {"text": "new task"})

    assert harness.branch(branch).status == BranchStatus.QUEUED
    assert harness.drain() == [StepOutcome.EXECUTED]
    request = harness.llm.requests[calls_before]
    assert request.messages[1:] == [{"role": "user", "content": "new task"}]
    assert harness.branch(branch).status == BranchStatus.COMPLETED
    assert harness.types(branch) == ["user_msg", "llm_output"]


def test_editing_llm_output_to_text_closes_the_branch_without_any_call(
    clean_db: SessionFactory,
) -> None:
    harness, _, nodes = completed_run(clean_db)
    calls = len(harness.llm.requests)

    branch = fork_from_edit(
        harness.deps, harness.user.id, nodes["llm_tool"].id, {"text": "my final answer"}
    )

    assert harness.queue.depth() == 0
    assert len(harness.llm.requests) == calls
    state = harness.branch(branch)
    assert state.status == BranchStatus.COMPLETED
    assert state.finished_at is not None
    assert harness.state(branch).next_action == NextAction.NONE
    assert harness.nodes(branch)[0].payload["text"] == "my final answer"


def test_editing_llm_output_to_a_tool_call_executes_that_tool_live(
    clean_db: SessionFactory,
) -> None:
    harness, _, nodes = completed_run(clean_db, text_reply("after new tool"))
    harness.tools.calls.clear()
    calls = len(harness.llm.requests)

    branch = fork_from_edit(
        harness.deps,
        harness.user.id,
        nodes["final"].id,
        {"tool_call": {"name": "search", "arguments": {"query": "chosen"}}},
    )

    assert harness.types(branch) == ["llm_output", "tool_call"]
    assert harness.drain() == [StepOutcome.EXECUTED, StepOutcome.EXECUTED]
    context, name, arguments = harness.tools.calls[0]
    assert (name, arguments) == ("search", {"query": "chosen"})
    assert context.bypass_cache is True
    assert context.branch_id == branch
    assert len(harness.llm.requests) == calls + 1
    assert harness.branch(branch).status == BranchStatus.COMPLETED


def test_editing_tool_call_reruns_the_tool_live_with_new_params(
    clean_db: SessionFactory,
) -> None:
    harness, _, nodes = completed_run(clean_db, text_reply("new answer"))
    harness.tools.calls.clear()
    calls = len(harness.llm.requests)

    branch = fork_from_edit(
        harness.deps, harness.user.id, nodes["call"].id, {"arguments": {"query": "edited"}}
    )

    assert harness.drain() == [StepOutcome.EXECUTED, StepOutcome.EXECUTED]
    context, name, arguments = harness.tools.calls[0]
    assert (name, arguments) == ("search", {"query": "edited"})
    assert context.bypass_cache is True
    assert harness.types(branch) == ["tool_call", "tool_result", "llm_output"]
    seen = harness.llm.requests[calls].messages
    assert seen[2]["tool_call"]["arguments"] == {"query": "edited"}
    assert seen[2]["content"] == "thinking"
    assert "edited" in seen[3]["content"]


def test_editing_tool_result_runs_no_tool_and_the_llm_sees_the_edit(
    clean_db: SessionFactory,
) -> None:
    harness, _, nodes = completed_run(clean_db, text_reply("new answer"))
    harness.tools.calls.clear()
    calls = len(harness.llm.requests)

    branch = fork_from_edit(
        harness.deps, harness.user.id, nodes["result"].id, {"content": "edited evidence"}
    )

    assert harness.drain() == [StepOutcome.EXECUTED]
    assert harness.tools.calls == []
    seen = harness.llm.requests[calls].messages
    assert seen[-1]["role"] == "tool"
    assert seen[-1]["content"] == "edited evidence"
    assert seen[-1]["tool_call_id"] == str(nodes["call"].id)
    assert harness.types(branch) == ["tool_result", "llm_output"]


def test_cache_bypass_only_applies_to_the_edited_call(clean_db: SessionFactory) -> None:
    harness, _, nodes = completed_run(clean_db, tool_reply("later"), text_reply("done"))
    harness.tools.calls.clear()

    fork_from_edit(
        harness.deps, harness.user.id, nodes["call"].id, {"arguments": {"query": "edited"}}
    )
    harness.drain()

    assert [context.bypass_cache for context, _, _ in harness.tools.calls] == [True, False]


# History never changes


@pytest.mark.parametrize(
    ("name", "edit"),
    [
        ("user", {"text": "changed"}),
        ("llm_tool", {"text": "changed"}),
        ("call", {"arguments": {"query": "changed"}}),
        ("result", {"content": "changed"}),
    ],
)
def test_edit_leaves_every_existing_node_byte_identical(
    clean_db: SessionFactory, name: str, edit: dict[str, Any]
) -> None:
    harness, source_branch, nodes = completed_run(clean_db)
    before = snapshot(harness)
    source_before = harness.branch(source_branch)

    fork_from_edit(harness.deps, harness.user.id, nodes[name].id, edit)

    after = {row[0]: row for row in snapshot(harness)}
    assert all(after[row[0]] == row for row in before)
    assert len(after) > len(before)
    source_after = harness.branch(source_branch)
    assert source_after.status == source_before.status
    assert source_after.turns_used == source_before.turns_used
    assert source_after.step_seq == source_before.step_seq


def test_fork_of_a_running_branch_leaves_it_running_unchanged(clean_db: SessionFactory) -> None:
    harness = Harness(
        clean_db, [tool_reply("src"), text_reply("child answer"), text_reply("source answer")]
    )
    _, source = start_task(harness.deps, harness.user.id, "the task", CONFIG)
    first, _ = harness.queue.messages.popleft()
    assert run_step(harness.deps, first) == StepOutcome.EXECUTED
    source_messages = list(harness.state(source).messages)
    source_seq = harness.branch(source).step_seq
    assert harness.branch(source).status == BranchStatus.QUEUED
    user = harness.nodes(source)[1]

    child = fork_from_edit(harness.deps, harness.user.id, user.id, {"text": "other"})

    assert harness.state(source).messages == source_messages
    assert harness.branch(source).step_seq == source_seq
    assert harness.types(source) == ["root", "user_msg", "llm_output", "tool_call"]

    harness.drain()
    assert harness.branch(source).status == BranchStatus.COMPLETED
    assert harness.branch(child).status == BranchStatus.COMPLETED
    assert harness.types(source)[:4] == ["root", "user_msg", "llm_output", "tool_call"]
    assert all(node.branch_id == child for node in harness.nodes(child))


def test_source_activity_after_the_fork_never_reaches_the_child(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply("first"), tool_reply("child"), text_reply("c")])
    _, source = start_task(harness.deps, harness.user.id, "the task", CONFIG)
    message, _ = harness.queue.messages.popleft()
    run_step(harness.deps, message)
    call_step, _ = harness.queue.messages.popleft()
    run_step(harness.deps, call_step)
    source_nodes = harness.nodes(source)
    result = source_nodes[-1]
    assert result.type == NodeType.TOOL_RESULT

    child = fork_from_edit(harness.deps, harness.user.id, result.id, {"content": "edited"})
    child_messages = list(harness.state(child).messages)

    harness.queue.messages.clear()
    assert run_step(harness.deps, StepMessage(source, harness.branch(source).step_seq))
    assert harness.state(child).messages == child_messages


# Copying state


def test_child_state_is_its_own_copy_of_the_path_before_the_edit(
    clean_db: SessionFactory,
) -> None:
    harness, source, nodes = completed_run(clean_db)
    child = fork_from_edit(harness.deps, harness.user.id, nodes["result"].id, {"content": "x"})
    source_messages = harness.state(source).messages
    child_messages = harness.state(child).messages
    assert child_messages[:2] == source_messages[:2]
    assert child_messages[2]["content"] == "x"
    assert harness.state(child).head_node_id != harness.state(source).head_node_id


def test_scratchpad_is_copied_up_to_the_fork_with_no_re_embedding(
    clean_db: SessionFactory,
) -> None:
    class CountingEmbedder(HashEmbedder):
        calls = 0

        def embed(self, texts: Any) -> list[list[float]]:
            CountingEmbedder.calls += 1
            return super().embed(texts)

    embedder = CountingEmbedder()
    harness = Harness(
        clean_db,
        [tool_reply("note one"), tool_reply("note two"), text_reply("done"), text_reply("child")],
    )

    def save(context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
        write_note(clean_db, embedder, context, arguments["query"])
        return ToolOutcome("saved")

    harness.tools.handlers["search"] = save
    _, source = start_task(harness.deps, harness.user.id, "the task", CONFIG)
    harness.drain()
    results = [node for node in harness.nodes(source) if node.type == NodeType.TOOL_RESULT]
    embeds_before = CountingEmbedder.calls

    child = fork_from_edit(
        harness.deps, harness.user.id, results[0].id, {"content": "edited result"}
    )

    assert CountingEmbedder.calls == embeds_before
    harness.drain()
    child_notes = [hit.content for hit in search_notes(clean_db, embedder, child, "note")]
    source_notes = [hit.content for hit in search_notes(clean_db, embedder, source, "note")]
    assert child_notes == ["note one"]
    assert sorted(source_notes) == ["note one", "note two"]


def test_editing_a_scratchpad_tool_call_excludes_its_original_write(
    clean_db: SessionFactory,
) -> None:
    embedder = HashEmbedder()
    harness = Harness(clean_db, [tool_reply("original"), text_reply("a"), text_reply("b")])

    def save(context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
        write_note(clean_db, embedder, context, arguments["query"])
        return ToolOutcome("saved")

    harness.tools.handlers["search"] = save
    _, source = start_task(harness.deps, harness.user.id, "the task", CONFIG)
    harness.drain()
    call = next(node for node in harness.nodes(source) if node.type == NodeType.TOOL_CALL)

    child = fork_from_edit(
        harness.deps, harness.user.id, call.id, {"arguments": {"query": "replacement"}}
    )
    harness.drain()

    assert [hit.content for hit in search_notes(clean_db, embedder, child, "x")] == ["replacement"]
    assert [hit.content for hit in search_notes(clean_db, embedder, source, "x")] == ["original"]


# Caps, nesting and budgets


def test_turns_are_inherited_through_nested_forks(clean_db: SessionFactory) -> None:
    harness, _, nodes = completed_run(clean_db, tool_reply("a"), text_reply("end"), turn_cap=3)
    first = fork_from_edit(harness.deps, harness.user.id, nodes["result"].id, {"content": "one"})
    assert harness.branch(first).turns_used == 1
    harness.drain()
    assert harness.branch(first).turns_used == 3
    assert harness.branch(first).status == BranchStatus.COMPLETED
    assert harness.llm.requests[-1].force_final is True

    result = [node for node in harness.nodes(first) if node.type == NodeType.TOOL_RESULT][-1]
    assert result.turn_index == 2
    harness.llm.add(text_reply("nested"))
    nested = fork_from_edit(harness.deps, harness.user.id, result.id, {"content": "two"})
    assert harness.branch(nested).turns_used == 2
    harness.drain()
    assert harness.branch(nested).turns_used == 3
    assert harness.llm.requests[-1].force_final is True


def test_a_fork_needing_a_turn_with_none_left_is_refused(clean_db: SessionFactory) -> None:
    harness, source, _ = completed_run(clean_db, turn_cap=2)
    final = harness.nodes(source)[-1]
    assert final.turn_index == 2
    with pytest.raises(NoTurnsLeftError):
        fork_from_edit(
            harness.deps,
            harness.user.id,
            final.id,
            {"tool_call": {"name": "search", "arguments": {"query": "more"}}},
        )
    plain = fork_from_edit(harness.deps, harness.user.id, final.id, {"text": "still allowed"})
    assert harness.branch(plain).status == BranchStatus.COMPLETED


def test_token_budget_counts_only_the_branch_own_spending(clean_db: SessionFactory) -> None:
    harness, source, nodes = completed_run(clean_db)
    assert harness.branch(source).input_tokens == 20
    child = fork_from_edit(harness.deps, harness.user.id, nodes["llm_tool"].id, {"text": "done"})
    assert harness.branch(child).input_tokens == 0


def test_nested_fork_path_crosses_branches_back_to_the_root(clean_db: SessionFactory) -> None:
    harness, source, nodes = completed_run(clean_db, tool_reply("x"), text_reply("y"))
    child = fork_from_edit(harness.deps, harness.user.id, nodes["user"].id, {"text": "changed"})
    harness.drain()
    child_result = next(n for n in harness.nodes(child) if n.type == NodeType.TOOL_RESULT)
    harness.llm.add(text_reply("deep"))
    nested = fork_from_edit(harness.deps, harness.user.id, child_result.id, {"content": "c"})
    harness.drain()

    with clean_db() as session:
        path = path_to(session, harness.nodes(nested)[-1].id)
    assert [node.branch_id for node in path] == [source, child, child, child, nested, nested]
    assert [node.type for node in path] == [
        "root",
        "user_msg",
        "llm_output",
        "tool_call",
        "tool_result",
        "llm_output",
    ]
    fork = harness.branch(nested)
    assert fork.parent_branch_id == child
    assert fork.fork_node_id == child_result.id
    assert harness.nodes(nested)[0].edited_from_id == child_result.id


def test_sibling_forks_of_one_node_are_independent(clean_db: SessionFactory) -> None:
    harness, _, nodes = completed_run(clean_db, text_reply("a"), text_reply("b"))
    one = fork_from_edit(harness.deps, harness.user.id, nodes["result"].id, {"content": "1"})
    two = fork_from_edit(harness.deps, harness.user.id, nodes["result"].id, {"content": "2"})
    harness.drain()
    assert harness.state(one).messages[2]["content"] == "1"
    assert harness.state(two).messages[2]["content"] == "2"
    assert harness.nodes(one)[0].parent_id == harness.nodes(two)[0].parent_id == nodes["call"].id


# Refused edits


def test_root_cannot_be_edited(clean_db: SessionFactory) -> None:
    harness, _, nodes = completed_run(clean_db)
    with pytest.raises(NotEditableError):
        fork_from_edit(harness.deps, harness.user.id, nodes["root"].id, {"text": "x"})


def test_unknown_node_is_refused(clean_db: SessionFactory) -> None:
    harness, _, _ = completed_run(clean_db)
    with pytest.raises(NodeNotFoundError):
        fork_from_edit(
            harness.deps,
            harness.user.id,
            UUID("00000000-0000-0000-0000-000000000000"),
            {"text": "x"},
        )


def test_edit_with_identical_content_is_refused_unless_allowed(
    clean_db: SessionFactory,
) -> None:
    harness, _, nodes = completed_run(clean_db, text_reply("repeat"))
    with pytest.raises(NoOpEditError):
        fork_from_edit(harness.deps, harness.user.id, nodes["user"].id, {"text": "the task"})
    with pytest.raises(NoOpEditError):
        fork_from_edit(
            harness.deps, harness.user.id, nodes["call"].id, {"arguments": {"query": "original"}}
        )
    allowed = fork_from_edit(
        harness.deps, harness.user.id, nodes["user"].id, {"text": "the task"}, allow_noop=True
    )
    harness.drain()
    assert harness.branch(allowed).status == BranchStatus.COMPLETED


@pytest.mark.parametrize(
    ("name", "edit", "message"),
    [
        ("user", {"text": "  "}, "non-empty"),
        ("user", {"wrong": "x"}, "non-empty"),
        ("llm_tool", {}, "text or a tool call"),
        ("llm_tool", {"text": 5}, "must be a string"),
        ("llm_tool", {"tool_call": {"name": "search"}}, "'name' and an 'arguments'"),
        ("llm_tool", {"tool_call": {"name": "rm", "arguments": {}}}, "unknown tool"),
        ("call", {"arguments": "x"}, "'arguments' object"),
        ("call", {"arguments": {"query": 1}}, "invalid arguments"),
        ("call", {"arguments": {"query": "q", "extra": 1}}, "invalid arguments"),
        ("result", {"content": 5}, "'content'"),
        ("result", {"content": "x", "is_error": "no"}, "true or false"),
        ("user", {"text": "x" * 5000}, "limited to"),
    ],
)
def test_invalid_edits_are_refused_and_create_nothing(
    clean_db: SessionFactory, name: str, edit: dict[str, Any], message: str
) -> None:
    harness, _, nodes = completed_run(clean_db)
    before = snapshot(harness)
    with pytest.raises(InvalidEditError, match=message):
        fork_from_edit(harness.deps, harness.user.id, nodes[name].id, edit)
    assert snapshot(harness) == before
    assert harness.queue.depth() == 0
