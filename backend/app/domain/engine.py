"""Runs branches one step at a time.

A step has three phases: claim it with a lease in one transaction, do the slow work with no
transaction open, and commit the result in a second transaction. Queues can deliver a message twice
and workers can die, so the commit is discarded unless this worker still holds the lease for the
same step. No process stays alive between steps, so a waiting branch uses no compute.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.agent.graph import PendingTool
from app.db.models import (
    NON_COMPLETED_STATUSES,
    Branch,
    BranchState,
    BranchStatus,
    NextAction,
    Node,
    NodeType,
    Tree,
)
from app.domain.config import TreeConfig
from app.domain.deps import EngineDeps
from app.domain.events import append_event, publish_all
from app.domain.messages import Message, estimate_tokens, required_message
from app.domain.outbox import add_step, dispatch_soon
from app.domain.queries import load_config
from app.ports import (
    LLMResponse,
    ProviderRateLimitedError,
    ProviderRefusedError,
    ProviderTransientError,
    StepMessage,
    ToolContext,
    ToolOutcome,
    TreeEvent,
)

# FOR NO KEY UPDATE. A plain FOR UPDATE conflicts with the key-share locks that foreign key checks
# take, which deadlocks with a fork that holds the tree's event lock and references this branch.
LOCK_BRANCH = {"key_share": True}
TRANSIENT_RETRY_SECONDS = 5

__all__ = ["EngineDeps", "StepOutcome", "cancel_branch", "run_step", "start_task"]


class StepOutcome(StrEnum):
    EXECUTED = "executed"
    STOPPED = "stopped"
    WAITING = "waiting"
    FAILED = "failed"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    SKIPPED_LEASED = "skipped_leased"
    SKIPPED_FINISHED = "skipped_finished"
    DISCARDED_STALE = "discarded_stale"


@dataclass(frozen=True)
class _Claim:
    lease_owner: str
    tree_id: UUID
    owner_id: UUID
    config: TreeConfig
    action: NextAction
    messages: list[Message]
    head_node_id: UUID
    turns_used: int
    pending_tool: PendingTool | None
    attempt: int


def start_task(
    deps: EngineDeps, owner_id: UUID, task: str, config: TreeConfig | None = None
) -> tuple[UUID, UUID]:
    """Create a tree with its main branch and queue the first step. Returns (tree_id, branch_id)."""
    config = config or TreeConfig()
    with deps.session_factory() as session:
        tree = Tree(id=uuid4(), owner_id=owner_id)
        branch = Branch(
            id=uuid4(),
            tree_id=tree.id,
            owner_id=owner_id,
            status=BranchStatus.QUEUED.value,
            next_node_seq=2,
            turns_used=0,
            input_tokens=0,
            output_tokens=0,
            step_seq=0,
        )
        root = Node(
            id=uuid4(),
            tree_id=tree.id,
            branch_id=branch.id,
            type=NodeType.ROOT.value,
            payload={"task": task, "config": config.model_dump()},
            seq=0,
            turn_index=0,
        )
        user_msg = Node(
            id=uuid4(),
            tree_id=tree.id,
            branch_id=branch.id,
            parent_id=root.id,
            type=NodeType.USER_MSG.value,
            payload={"text": task},
            seq=1,
            turn_index=0,
        )
        session.add(tree)
        session.flush()
        session.add(branch)
        session.flush()
        session.add(root)
        session.flush()
        session.add(user_msg)
        session.flush()
        session.add(
            BranchState(
                branch_id=branch.id,
                messages=[required_message(user_msg)],
                head_node_id=user_msg.id,
                next_action=NextAction.LLM.value,
            )
        )
        events = [
            append_event(session, tree.id, "branch_created", branch_id=str(branch.id)),
            *_node_events(session, tree.id, [root, user_msg]),
        ]
        outbox_id = add_step(session, branch.id, 0, deps.now())
        session.commit()
    publish_all(deps.live_events, events)
    dispatch_soon(deps, outbox_id)
    return tree.id, branch.id


def cancel_branch(deps: EngineDeps, branch_id: UUID) -> bool:
    """Cancel a non-completed branch. A step in progress has its result discarded."""
    with deps.session_factory() as session:
        branch = session.get(Branch, branch_id, with_for_update=LOCK_BRANCH)
        if branch is None or BranchStatus(branch.status) not in NON_COMPLETED_STATUSES:
            return False
        events = _finish(session, branch, BranchStatus.CANCELLED, deps.now(), "cancelled by user")
        session.commit()
    publish_all(deps.live_events, events)
    return True


def run_step(deps: EngineDeps, message: StepMessage) -> StepOutcome:
    claim = _claim(deps, message)
    if isinstance(claim, StepOutcome):
        return claim

    state: dict[str, Any] = {
        "action": claim.action.value,
        "config": claim.config,
        "messages": claim.messages,
    }
    if claim.action is NextAction.LLM:
        state["owner_id"] = claim.owner_id
        state["force_final"] = claim.turns_used + 1 >= claim.config.turn_cap
    else:
        state["pending_tool"] = claim.pending_tool

    try:
        result = deps.graph.invoke(state)
    except ProviderRateLimitedError as exc:
        return _release_to_waiting(deps, message, claim, exc.retry_after_seconds)
    except ProviderTransientError:
        # Attempts aren't reset, so a provider that stays down fails the branch at the limit.
        delay = TRANSIENT_RETRY_SECONDS * 2 ** (claim.attempt - 1)
        return _release_to_waiting(deps, message, claim, delay, reset_attempts=False)
    except ProviderRefusedError as exc:
        return _fail(deps, message, claim, str(exc))
    except Exception as exc:
        return _fail(deps, message, claim, f"{type(exc).__name__}: {exc}")

    if claim.action is NextAction.LLM:
        return _commit_llm(deps, message, claim, result["llm_response"], state["force_final"])
    return _commit_tool(deps, message, claim, result["tool_outcome"])


def _claim(deps: EngineDeps, message: StepMessage) -> _Claim | StepOutcome:
    with deps.session_factory() as session:
        branch = session.get(Branch, message.branch_id, with_for_update=LOCK_BRANCH)
        if branch is None or BranchStatus(branch.status) not in NON_COMPLETED_STATUSES:
            return StepOutcome.SKIPPED_FINISHED
        if message.seq != branch.step_seq:
            return StepOutcome.SKIPPED_DUPLICATE
        now = deps.now()
        if branch.lease_until is not None and branch.lease_until > now:
            return StepOutcome.SKIPPED_LEASED

        if branch.step_attempts >= deps.max_step_attempts:
            reason = f"step abandoned after {branch.step_attempts} attempts"
            events = _finish(session, branch, BranchStatus.FAILED, now, reason)
            session.commit()
            publish_all(deps.live_events, events)
            return StepOutcome.FAILED

        state = session.get_one(BranchState, branch.id)
        config = load_config(session, branch.tree_id)
        action = NextAction(state.next_action)

        if action is NextAction.LLM:
            stop = _budget_stop(branch, state, config)
            if stop is not None:
                events = _finish(session, branch, stop[0], now, stop[1])
                session.commit()
                publish_all(deps.live_events, events)
                return StepOutcome.STOPPED

        pending_tool = None
        if action is NextAction.TOOL:
            head = session.get_one(Node, state.head_node_id)
            pending_tool = PendingTool(
                name=head.payload["name"],
                arguments=head.payload["arguments"],
                context=ToolContext(branch.id, head.id, state.bypass_cache),
            )

        lease_owner = uuid4().hex
        branch.lease_owner = lease_owner
        branch.lease_until = now + timedelta(seconds=deps.lease_seconds)
        branch.step_attempts += 1
        branch.status = BranchStatus.RUNNING.value
        events = [_status_event(session, branch)]
        claim = _Claim(
            lease_owner=lease_owner,
            tree_id=branch.tree_id,
            owner_id=branch.owner_id,
            config=config,
            action=action,
            messages=list(state.messages),
            head_node_id=state.head_node_id,
            turns_used=branch.turns_used,
            pending_tool=pending_tool,
            attempt=branch.step_attempts,
        )
        session.commit()
    publish_all(deps.live_events, events)
    return claim


def _budget_stop(
    branch: Branch, state: BranchState, config: TreeConfig
) -> tuple[BranchStatus, str] | None:
    if branch.turns_used >= config.turn_cap:
        return BranchStatus.CAPPED, f"turn cap of {config.turn_cap} reached"
    spent = branch.input_tokens + branch.output_tokens
    if spent + estimate_tokens(state.messages) > config.branch_token_budget:
        return (
            BranchStatus.BUDGET_EXHAUSTED,
            f"token budget of {config.branch_token_budget} reached",
        )
    return None


def _locked_if_current(session: Session, message: StepMessage, claim: _Claim) -> Branch | None:
    """Lock the branch, or return None if this worker's result must be thrown away."""
    branch = session.get(Branch, message.branch_id, with_for_update=LOCK_BRANCH)
    if (
        branch is None
        or branch.status != BranchStatus.RUNNING.value
        or branch.lease_owner != claim.lease_owner
        or branch.step_seq != message.seq
    ):
        return None
    return branch


def _commit_llm(
    deps: EngineDeps,
    message: StepMessage,
    claim: _Claim,
    response: LLMResponse,
    force_final: bool,
) -> StepOutcome:
    text = response.text or ""
    tool_call = response.tool_call
    dropped = response.dropped_tool_calls
    if force_final and tool_call is not None:
        tool_call, dropped = None, dropped + 1

    turn = claim.turns_used + 1
    with deps.session_factory() as session:
        branch = _locked_if_current(session, message, claim)
        if branch is None:
            return StepOutcome.DISCARDED_STALE

        llm_output = _add_node(
            session,
            branch,
            claim.head_node_id,
            NodeType.LLM_OUTPUT,
            {
                "text": text or None,
                "tool_call": (
                    {"name": tool_call.name, "arguments": tool_call.arguments}
                    if tool_call
                    else None
                ),
                "dropped_tool_calls": dropped,
            },
            turn,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        branch.turns_used = turn
        branch.input_tokens += response.usage.input_tokens
        branch.output_tokens += response.usage.output_tokens
        state = session.get_one(BranchState, branch.id)
        new_nodes = [llm_output]

        if tool_call is not None:
            call_node = _add_node(
                session,
                branch,
                llm_output.id,
                NodeType.TOOL_CALL,
                {"name": tool_call.name, "arguments": tool_call.arguments},
                turn,
            )
            new_nodes.append(call_node)
            state.messages = [*state.messages, required_message(call_node, text or None)]
            state.head_node_id = call_node.id
            state.next_action = NextAction.TOOL.value
            state.bypass_cache = False
            events = _node_events(session, branch.tree_id, new_nodes)
            events.extend(_advance(session, branch))
            outcome = StepOutcome.EXECUTED
            outbox_id = add_step(session, branch.id, branch.step_seq, deps.now())
        else:
            state.messages = [*state.messages, required_message(llm_output)]
            state.head_node_id = llm_output.id
            state.next_action = NextAction.NONE.value
            events = _node_events(session, branch.tree_id, new_nodes)
            if text:
                final = BranchStatus.COMPLETED
                reason = None
                outcome = StepOutcome.EXECUTED
            elif force_final:
                final = BranchStatus.CAPPED
                reason = "the model gave no final answer on the last allowed turn"
                outcome = StepOutcome.STOPPED
            else:
                final = BranchStatus.FAILED
                reason = "the model returned no text and no tool call"
                outcome = StepOutcome.FAILED
            events.extend(_finish(session, branch, final, deps.now(), reason))
            outbox_id = None
        session.commit()
    publish_all(deps.live_events, events)
    if outbox_id is not None:
        dispatch_soon(deps, outbox_id)
    return outcome


def _commit_tool(
    deps: EngineDeps, message: StepMessage, claim: _Claim, outcome: ToolOutcome
) -> StepOutcome:
    pending = claim.pending_tool
    assert pending is not None  # noqa: S101
    with deps.session_factory() as session:
        branch = _locked_if_current(session, message, claim)
        if branch is None:
            return StepOutcome.DISCARDED_STALE
        result = _add_node(
            session,
            branch,
            claim.head_node_id,
            NodeType.TOOL_RESULT,
            {
                "name": pending.name,
                "content": _truncate(outcome.content, claim.config.max_tool_result_chars),
                "is_error": outcome.is_error,
            },
            claim.turns_used,
        )
        state = session.get_one(BranchState, branch.id)
        state.messages = [*state.messages, required_message(result)]
        state.head_node_id = result.id
        state.next_action = NextAction.LLM.value
        state.bypass_cache = False
        events = _node_events(session, branch.tree_id, [result])
        events.extend(_advance(session, branch))
        outbox_id = add_step(session, branch.id, branch.step_seq, deps.now())
        session.commit()
    publish_all(deps.live_events, events)
    dispatch_soon(deps, outbox_id)
    return StepOutcome.EXECUTED


def _truncate(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    return f"{content[:limit]}\n[truncated: {len(content) - limit} more characters]"


def _release_to_waiting(
    deps: EngineDeps,
    message: StepMessage,
    claim: _Claim,
    delay_seconds: int,
    *,
    reset_attempts: bool = True,
) -> StepOutcome:
    with deps.session_factory() as session:
        branch = _locked_if_current(session, message, claim)
        if branch is None:
            return StepOutcome.DISCARDED_STALE
        branch.status = BranchStatus.WAITING.value
        branch.lease_owner = None
        branch.lease_until = None
        if reset_attempts:
            branch.step_attempts = 0
        events = [_status_event(session, branch)]
        # The sweeper sends this once it's due. Until then only a database row exists.
        add_step(session, branch.id, message.seq, deps.now() + timedelta(seconds=delay_seconds))
        session.commit()
    publish_all(deps.live_events, events)
    return StepOutcome.WAITING


def _fail(deps: EngineDeps, message: StepMessage, claim: _Claim, reason: str) -> StepOutcome:
    with deps.session_factory() as session:
        branch = _locked_if_current(session, message, claim)
        if branch is None:
            return StepOutcome.DISCARDED_STALE
        events = _finish(session, branch, BranchStatus.FAILED, deps.now(), reason)
        session.commit()
    publish_all(deps.live_events, events)
    return StepOutcome.FAILED


def _add_node(
    session: Session,
    branch: Branch,
    parent_id: UUID,
    node_type: NodeType,
    payload: dict[str, Any],
    turn_index: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> Node:
    node = Node(
        id=uuid4(),
        tree_id=branch.tree_id,
        branch_id=branch.id,
        parent_id=parent_id,
        type=node_type.value,
        payload=payload,
        seq=branch.next_node_seq,
        turn_index=turn_index,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    branch.next_node_seq += 1
    session.add(node)
    session.flush()
    return node


def _advance(session: Session, branch: Branch) -> list[TreeEvent]:
    branch.step_seq += 1
    branch.step_attempts = 0
    branch.status = BranchStatus.QUEUED.value
    branch.lease_owner = None
    branch.lease_until = None
    return [_status_event(session, branch)]


def _finish(
    session: Session, branch: Branch, status: BranchStatus, now: datetime, reason: str | None
) -> list[TreeEvent]:
    branch.status = status.value
    branch.status_reason = reason
    branch.finished_at = now
    branch.lease_owner = None
    branch.lease_until = None
    return [_status_event(session, branch)]


def _status_event(session: Session, branch: Branch) -> TreeEvent:
    return append_event(
        session,
        branch.tree_id,
        "branch_status",
        branch_id=str(branch.id),
        status=branch.status,
        reason=branch.status_reason,
    )


def _node_events(session: Session, tree_id: UUID, nodes: list[Node]) -> list[TreeEvent]:
    return [
        append_event(
            session,
            tree_id,
            "node_created",
            node_id=str(node.id),
            branch_id=str(node.branch_id),
            type=node.type,
        )
        for node in nodes
    ]
