"""Creates a branch from an edit.

An edit never changes an existing node. It writes a new node beside the edited one, in a new branch
that gets its own copy of the state before that point. The copy is rebuilt from immutable nodes on
the path, so whatever the source branch does before or after can't leak in.
"""

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.db.models import (
    Branch,
    BranchState,
    BranchStatus,
    NextAction,
    Node,
    NodeType,
    ScratchpadChunk,
)
from app.domain.config import TreeConfig
from app.domain.deps import EngineDeps
from app.domain.events import append_event, publish_all
from app.domain.limits import admit_fork
from app.domain.messages import Message, messages_from_path, required_message
from app.domain.outbox import add_step, dispatch_soon
from app.domain.queries import load_config, path_to
from app.domain.scratchpad import add_page, embed_page
from app.domain.task_gate import task_problem
from app.domain.tools import tool_call_problem
from app.ports import TreeEvent


class ForkError(Exception):
    """A fork that was refused."""


class NodeNotFoundError(ForkError):
    pass


class NotEditableError(ForkError):
    pass


class InvalidEditError(ForkError):
    pass


class NoOpEditError(ForkError):
    pass


class NoTurnsLeftError(ForkError):
    pass


@dataclass(frozen=True)
class _Plan:
    """What a validated edit will create."""

    node_payload: dict[str, Any]
    next_action: NextAction
    bypass_cache: bool = False
    tool_call: dict[str, Any] | None = None


def fork_from_edit(
    deps: EngineDeps,
    owner_id: UUID,
    node_id: UUID,
    edit: dict[str, Any],
    *,
    allow_noop: bool = False,
    expires_at: datetime | None = None,
) -> UUID:
    """Create a branch with `edit` replacing the node. `allow_noop` is for evaluation runs."""
    started = time.perf_counter()
    with deps.telemetry.span("fork.create", {"node.id": str(node_id)}):
        page = _embed_edited_page(deps, node_id, edit)
        branch_id, node_type = _fork(deps, owner_id, node_id, edit, allow_noop, expires_at, page)
    deps.telemetry.record("fork.duration", time.perf_counter() - started, {"node_type": node_type})
    deps.telemetry.record("admission.accepted", 1, {"kind": "fork"})
    return branch_id


def _embed_edited_page(
    deps: EngineDeps, node_id: UUID, edit: dict[str, Any]
) -> list[tuple[str, list[float]]]:
    """An edited page replaces the original in this branch's store, so it needs its own vectors."""
    with deps.session_factory() as session:
        source = session.get(Node, node_id)
        if source is None or source.type != NodeType.TOOL_RESULT:
            return []
        name = source.payload["name"]
    content = edit.get("content")
    if not isinstance(content, str):
        return []
    return embed_page(deps.embedder, name, content, edit.get("is_error") is True)


def _fork(
    deps: EngineDeps,
    owner_id: UUID,
    node_id: UUID,
    edit: dict[str, Any],
    allow_noop: bool,
    expires_at: datetime | None,
    page: list[tuple[str, list[float]]],
) -> tuple[UUID, str]:
    with deps.session_factory() as session:
        source = session.get(Node, node_id)
        if source is None:
            raise NodeNotFoundError(f"node {node_id} not found")
        if source.parent_id is None:
            raise NotEditableError("the root node can't be edited")
        config = load_config(session, source.tree_id)
        plan = _plan_edit(source, edit, config)
        if not allow_noop and _same_content(source, plan.node_payload):
            raise NoOpEditError("the edit doesn't change the node")

        admit_fork(
            session,
            deps.limits,
            deps.rate_limiter,
            owner_id,
            will_run=plan.next_action is not NextAction.NONE,
        )
        before = path_to(session, source.parent_id)
        branch, state, events = _create_branch(session, source, plan, before, owner_id, expires_at)
        finished = plan.next_action is NextAction.NONE
        if finished:
            branch.status = BranchStatus.COMPLETED.value
            branch.finished_at = deps.now()
            events.append(
                append_event(
                    session,
                    branch.tree_id,
                    "branch_status",
                    branch_id=str(branch.id),
                    status=branch.status,
                    reason=None,
                )
            )
        session.add(state)
        _copy_scratchpad(session, source.branch_id, branch.id, [node.id for node in before])
        add_page(session, branch.id, state.head_node_id, page)
        outbox_id = (
            None
            if finished
            else add_step(session, branch.id, 0, deps.now(), deps.telemetry.trace_context())
        )
        session.commit()
        branch_id = branch.id
        node_type = source.type
    publish_all(deps.live_events, events, deps.telemetry)
    if outbox_id is not None:
        dispatch_soon(deps, outbox_id)
    return branch_id, node_type


def _plan_edit(source: Node, edit: dict[str, Any], config: TreeConfig) -> _Plan:
    if len(json.dumps(edit)) > config.max_edit_chars:
        raise InvalidEditError(f"edits are limited to {config.max_edit_chars} characters")
    match source.type:
        case NodeType.USER_MSG:
            text = _required_text(edit, "text")
            problem = task_problem(text)
            if problem:
                raise InvalidEditError(problem)
            return _Plan({"text": text}, NextAction.LLM)
        case NodeType.LLM_OUTPUT:
            return _plan_llm_output(source, edit, config)
        case NodeType.TOOL_CALL:
            arguments = edit.get("arguments")
            if not isinstance(arguments, dict):
                raise InvalidEditError("a tool call edit needs an 'arguments' object")
            name = source.payload["name"]
            _require_valid_call(config, name, arguments)
            _require_turn_after(source, config)
            payload = {"name": name, "arguments": arguments}
            return _Plan(payload, NextAction.TOOL, bypass_cache=True)
        case NodeType.TOOL_RESULT:
            content = _required_text(edit, "content", allow_empty=True)
            is_error = edit.get("is_error", False)
            if not isinstance(is_error, bool):
                raise InvalidEditError("'is_error' must be true or false")
            _require_turn_after(source, config)
            payload = {"name": source.payload["name"], "content": content, "is_error": is_error}
            return _Plan(payload, NextAction.LLM)
        case _:
            raise NotEditableError(f"{source.type} nodes can't be edited")


def _plan_llm_output(source: Node, edit: dict[str, Any], config: TreeConfig) -> _Plan:
    text = edit.get("text")
    tool_call = edit.get("tool_call")
    if text is not None and not isinstance(text, str):
        raise InvalidEditError("'text' must be a string")
    if tool_call is None:
        if not text or not text.strip():
            raise InvalidEditError("an LLM output edit needs text or a tool call")
        return _Plan({"text": text, "tool_call": None, "dropped_tool_calls": 0}, NextAction.NONE)

    if (
        not isinstance(tool_call, dict)
        or not isinstance(tool_call.get("name"), str)
        or not isinstance(tool_call.get("arguments"), dict)
    ):
        raise InvalidEditError("'tool_call' needs a 'name' and an 'arguments' object")
    _require_valid_call(config, tool_call["name"], tool_call["arguments"])
    _require_turn_after(source, config)
    call = {"name": tool_call["name"], "arguments": tool_call["arguments"]}
    payload = {"text": text or None, "tool_call": call, "dropped_tool_calls": 0}
    return _Plan(payload, NextAction.TOOL, bypass_cache=True, tool_call=call)


def _required_text(edit: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = edit.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise InvalidEditError(f"the edit needs a non-empty '{key}' string")
    return value


def _require_valid_call(config: TreeConfig, name: str, arguments: dict[str, Any]) -> None:
    problem = tool_call_problem(config, name, arguments)
    if problem is not None:
        raise InvalidEditError(problem)


def _require_turn_after(source: Node, config: TreeConfig) -> None:
    """A tool's result is only useful if the LLM gets another turn to read it."""
    if source.turn_index >= config.turn_cap:
        raise NoTurnsLeftError(f"this path has used all {config.turn_cap} turns")


def _same_content(source: Node, payload: dict[str, Any]) -> bool:
    if source.type == NodeType.LLM_OUTPUT:
        return all(source.payload.get(key) == payload[key] for key in ("text", "tool_call"))
    return bool(source.payload == payload)


def _create_branch(
    session: Session,
    source: Node,
    plan: _Plan,
    before: list[Node],
    owner_id: UUID,
    expires_at: datetime | None,
) -> tuple[Branch, BranchState, list[TreeEvent]]:
    branch = Branch(
        id=uuid4(),
        tree_id=source.tree_id,
        owner_id=owner_id,
        parent_branch_id=source.branch_id,
        fork_node_id=source.id,
        status=BranchStatus.QUEUED.value,
        turns_used=source.turn_index,
        input_tokens=0,
        output_tokens=0,
        step_seq=0,
        next_node_seq=0,
        expires_at=expires_at,
    )
    session.add(branch)
    session.flush()

    edited = _new_node(branch, source.parent_id, source, plan.node_payload, source.type)
    edited.edited_from_id = source.id
    session.add(edited)
    session.flush()
    new_nodes = [edited]

    messages: list[Message] = messages_from_path(before)
    if plan.tool_call is not None:
        call = _new_node(branch, edited.id, source, plan.tool_call, NodeType.TOOL_CALL.value)
        session.add(call)
        session.flush()
        new_nodes.append(call)
        messages.append(required_message(call, plan.node_payload.get("text")))
        head = call
    elif source.type == NodeType.TOOL_CALL:
        parent = before[-1]
        messages.append(required_message(edited, parent.payload.get("text")))
        head = edited
    else:
        messages.append(required_message(edited))
        head = edited

    state = BranchState(
        branch_id=branch.id,
        messages=messages,
        head_node_id=head.id,
        next_action=plan.next_action.value,
        bypass_cache=plan.bypass_cache,
    )
    events = [
        append_event(
            session,
            branch.tree_id,
            "branch_created",
            branch_id=str(branch.id),
            parent_branch_id=str(source.branch_id),
            fork_node_id=str(source.id),
        ),
        *(
            append_event(
                session,
                branch.tree_id,
                "node_created",
                node_id=str(node.id),
                branch_id=str(branch.id),
                type=node.type,
            )
            for node in new_nodes
        ),
    ]
    return branch, state, events


def _new_node(
    branch: Branch,
    parent_id: UUID | None,
    source: Node,
    payload: dict[str, Any],
    node_type: str,
) -> Node:
    node = Node(
        id=uuid4(),
        tree_id=branch.tree_id,
        branch_id=branch.id,
        parent_id=parent_id,
        type=node_type,
        payload=payload,
        seq=branch.next_node_seq,
        turn_index=source.turn_index,
        input_tokens=0,
        output_tokens=0,
    )
    branch.next_node_seq += 1
    return node


def _copy_scratchpad(
    session: Session, source_branch: UUID, new_branch: UUID, path_node_ids: list[UUID]
) -> None:
    """Copy the chunks written on the path before the fork, keeping embeddings as they are."""
    rows = session.execute(
        select(
            ScratchpadChunk.source_node_id,
            ScratchpadChunk.chunk_index,
            ScratchpadChunk.content,
            ScratchpadChunk.embedding,
        ).where(
            ScratchpadChunk.branch_id == source_branch,
            ScratchpadChunk.source_node_id.in_(path_node_ids),
        )
    ).all()
    if rows:
        session.execute(
            insert(ScratchpadChunk),
            [
                {
                    "id": uuid4(),
                    "branch_id": new_branch,
                    "source_node_id": row.source_node_id,
                    "chunk_index": row.chunk_index,
                    "content": row.content,
                    "embedding": row.embedding,
                }
                for row in rows
            ],
        )
