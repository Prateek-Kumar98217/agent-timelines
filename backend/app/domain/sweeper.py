"""Periodic recovery. It sends due outbox rows and re-queues branches whose message was lost.

Sweeps are safe to run at any frequency or concurrently: the worst outcome is a duplicate message,
which the engine skips.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, and_, exists, or_, select

from app.db.models import Branch, BranchStatus, StepOutbox
from app.domain.deps import EngineDeps
from app.domain.outbox import add_step, dispatch

# How long since the last send before a branch counts as stranded.
RUNNING_RESEND_SECONDS = 30
QUEUED_RESEND_SECONDS = 300


@dataclass(frozen=True)
class SweepResult:
    requeued: int
    dispatched: int


def sweep(deps: EngineDeps) -> SweepResult:
    now = deps.now()
    running_cutoff = now - timedelta(seconds=RUNNING_RESEND_SECONDS)
    queued_cutoff = now - timedelta(seconds=QUEUED_RESEND_SECONDS)

    def sent_before(cutoff: datetime) -> ColumnElement[bool]:
        return or_(Branch.last_dispatched_at.is_(None), Branch.last_dispatched_at < cutoff)

    expired_running = and_(
        Branch.status == BranchStatus.RUNNING.value,
        Branch.lease_until < now,
        sent_before(running_cutoff),
    )
    lost_message = and_(
        Branch.status.in_([BranchStatus.QUEUED.value, BranchStatus.WAITING.value]),
        sent_before(queued_cutoff),
    )
    has_outbox_row = exists().where(StepOutbox.branch_id == Branch.id)
    with deps.session_factory() as session:
        stranded = session.execute(
            select(Branch.id, Branch.step_seq)
            .where(or_(expired_running, lost_message), ~has_outbox_row)
            .with_for_update(skip_locked=True, key_share=True)
        ).all()
        for branch_id, step_seq in stranded:
            add_step(session, branch_id, step_seq, now)
        session.commit()
    return SweepResult(requeued=len(stranded), dispatched=dispatch(deps))
