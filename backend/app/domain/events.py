"""Ordered per-tree event log. It drives live updates and lets clients catch up with no gaps."""

from typing import Any
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import Tree, TreeEventRow
from app.ports import LiveEvents, TreeEvent


def append_event(session: Session, tree_id: UUID, event_type: str, **payload: Any) -> TreeEvent:
    """Add an event in the current transaction.

    Taking the next number locks the tree row until commit, so numbers commit in order with no gaps.
    """
    seq = session.execute(
        update(Tree)
        .where(Tree.id == tree_id)
        .values(next_event_seq=Tree.next_event_seq + 1)
        .returning(Tree.next_event_seq - 1)
    ).scalar_one()
    session.add(TreeEventRow(tree_id=tree_id, seq=seq, type=event_type, payload=payload))
    return TreeEvent(tree_id=tree_id, seq=seq, type=event_type, payload=payload)


def publish_all(live_events: LiveEvents, events: list[TreeEvent]) -> None:
    """Publish after commit. A lost publish is harmless because clients catch up from the log."""
    for event in events:
        live_events.publish(event)
