"""Compares two branches of a tree by their root-to-leaf paths."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Branch, BranchState, Node, NodeType
from app.domain.queries import path_to


class BranchesNotComparableError(Exception):
    """The branches are missing or belong to different trees."""


@dataclass(frozen=True)
class BranchSummary:
    branch_id: UUID
    status: str
    turns_used: int
    input_tokens: int
    output_tokens: int
    final_text: str | None
    tool_calls: list[str]


@dataclass(frozen=True)
class Comparison:
    a: BranchSummary
    b: BranchSummary
    last_common_node_id: UUID
    shared_nodes: int
    same_final_text: bool
    tool_call_overlap: float


def _summary(branch: Branch, path: list[Node]) -> BranchSummary:
    last = path[-1]
    final_text = None
    if last.type == NodeType.LLM_OUTPUT and last.payload.get("tool_call") is None:
        final_text = last.payload.get("text")
    return BranchSummary(
        branch_id=branch.id,
        status=branch.status,
        turns_used=branch.turns_used,
        input_tokens=branch.input_tokens,
        output_tokens=branch.output_tokens,
        final_text=final_text,
        tool_calls=[
            f"{node.payload['name']}({_sorted_arguments(node)})"
            for node in path
            if node.type == NodeType.TOOL_CALL
        ],
    )


def _sorted_arguments(node: Node) -> str:
    return ", ".join(f"{key}={value!r}" for key, value in sorted(node.payload["arguments"].items()))


def _overlap(a: list[str], b: list[str]) -> float:
    """Jaccard similarity of the distinct tool calls. Two branches with none count as identical."""
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return 1.0
    return len(set_a & set_b) / len(set_a | set_b)


def compare_branches(session: Session, branch_a: UUID, branch_b: UUID) -> Comparison:
    a, b = session.get(Branch, branch_a), session.get(Branch, branch_b)
    if a is None or b is None or a.tree_id != b.tree_id:
        raise BranchesNotComparableError("both branches must exist in the same tree")
    path_a = path_to(session, session.get_one(BranchState, a.id).head_node_id)
    path_b = path_to(session, session.get_one(BranchState, b.id).head_node_id)

    shared = 0
    for node_a, node_b in zip(path_a, path_b, strict=False):
        if node_a.id != node_b.id:
            break
        shared += 1

    summary_a, summary_b = _summary(a, path_a), _summary(b, path_b)
    return Comparison(
        a=summary_a,
        b=summary_b,
        last_common_node_id=path_a[shared - 1].id,
        shared_nodes=shared,
        same_final_text=(
            summary_a.final_text is not None and summary_a.final_text == summary_b.final_text
        ),
        tool_call_overlap=_overlap(summary_a.tool_calls, summary_b.tool_calls),
    )
