"""Transactional outbox for step messages.

A step is recorded in the same transaction as the work before it, then sent to the queue after
commit. If the send fails or the process dies first, the sweeper sends it later. A message can be
sent twice as a result, which the engine already handles, but it can't be lost.
"""

import logging
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Branch, StepOutbox
from app.domain.deps import EngineDeps
from app.ports import StepMessage

logger = logging.getLogger(__name__)


def add_step(session: Session, branch_id: UUID, seq: int, available_at: datetime) -> UUID:
    row = StepOutbox(id=uuid4(), branch_id=branch_id, seq=seq, available_at=available_at)
    session.add(row)
    return row.id


def dispatch(deps: EngineDeps, ids: list[UUID] | None = None, limit: int = 100) -> int:
    """Send due rows to the queue. `SKIP LOCKED` lets several dispatchers run at once."""
    now = deps.now()
    query = (
        select(StepOutbox)
        .where(StepOutbox.available_at <= now)
        .order_by(StepOutbox.available_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if ids is not None:
        query = query.where(StepOutbox.id.in_(ids))
    with deps.session_factory() as session:
        rows = list(session.execute(query).scalars())
        for row in rows:
            deps.queue.enqueue(StepMessage(row.branch_id, row.seq))
            session.execute(
                update(Branch).where(Branch.id == row.branch_id).values(last_dispatched_at=now)
            )
            session.delete(row)
        session.commit()
    return len(rows)


def dispatch_soon(deps: EngineDeps, outbox_id: UUID) -> None:
    """Try to send right after commit. On failure the row stays for the sweeper."""
    try:
        dispatch(deps, [outbox_id])
    except Exception:
        logger.exception("couldn't send step %s; the sweeper will retry", outbox_id)
