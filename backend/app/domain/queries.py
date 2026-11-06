"""Reads shared by the engine and forking."""

from uuid import UUID

from sqlalchemy import literal, select
from sqlalchemy.orm import Session, aliased

from app.db.models import Node, NodeType
from app.domain.config import EXTRA_TURNS_PER_MESSAGE, TreeConfig


def load_config(session: Session, tree_id: UUID) -> TreeConfig:
    root = session.execute(
        select(Node).where(Node.tree_id == tree_id, Node.type == NodeType.ROOT.value)
    ).scalar_one()
    return TreeConfig.model_validate(root.payload["config"])


def path_to(session: Session, node_id: UUID) -> list[Node]:
    """Return the nodes from the root to `node_id` inclusive, following links across forks."""
    ancestors = (
        select(Node.id, Node.parent_id, literal(0).label("depth"))
        .where(Node.id == node_id)
        .cte("ancestors", recursive=True)
    )
    parent = aliased(Node)
    ancestors = ancestors.union_all(
        select(parent.id, parent.parent_id, ancestors.c.depth + 1).join(
            ancestors, parent.id == ancestors.c.parent_id
        )
    )
    return list(
        session.execute(
            select(Node)
            .join(ancestors, Node.id == ancestors.c.id)
            .order_by(ancestors.c.depth.desc())
        ).scalars()
    )


def extra_turns_on(path: list[Node], turn_cap: int) -> int:
    """Turns earned on a path: a follow-up message earns them only if it arrived with none left.

    The first message after the root is the task itself, so it's never a follow-up.
    """
    extra = 0
    for node in path[2:]:
        if node.type == NodeType.USER_MSG and node.turn_index >= turn_cap + extra:
            extra += EXTRA_TURNS_PER_MESSAGE
    return extra
