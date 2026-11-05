"""Resource limits per kind of user, and the checks that enforce them."""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import NON_COMPLETED_STATUSES, Branch, BranchStatus, Tree, User
from app.ports import PrincipalKind, RateLimiter

DAY_SECONDS = 86_400


class AdmissionRejectedError(Exception):
    """New work was refused to protect resources. The message is safe to show."""

    def __init__(self, reason: str, message: str, retry_after_seconds: int = 60) -> None:
        super().__init__(message)
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class UserLimits:
    active_branches: int
    trees: int
    tokens_per_day: int
    forks_per_day: int | None = None
    writes_per_minute: int = 120


def _default_table() -> dict[str, UserLimits]:
    return {
        PrincipalKind.LOCAL.value: UserLimits(5, 1_000, 200_000),
        PrincipalKind.USER.value: UserLimits(3, 1, 100_000, forks_per_day=20, writes_per_minute=30),
        PrincipalKind.GUEST.value: UserLimits(1, 1, 10_000, forks_per_day=3, writes_per_minute=10),
    }


@dataclass(frozen=True)
class Limits:
    by_kind: dict[str, UserLimits] = field(default_factory=_default_table)
    # Branches waiting for a worker, across all users. Beyond this, new work is refused.
    max_queued_branches: int = 100

    def for_kind(self, kind: str) -> UserLimits:
        return self.by_kind[kind]


def tokens_key(user_id: UUID) -> str:
    return f"tokens:{user_id}"


def admit_tree(session: Session, limits: Limits, rate_limiter: RateLimiter, owner_id: UUID) -> None:
    """Check limits for a new tree and hold the user's lock until the transaction ends."""
    user_limits = _locked_limits(session, limits, owner_id)
    trees = session.execute(
        select(func.count()).select_from(Tree).where(Tree.owner_id == owner_id)
    ).scalar_one()
    if trees >= user_limits.trees:
        raise AdmissionRejectedError(
            "trees", f"you've reached the limit of {user_limits.trees} trees", DAY_SECONDS
        )
    _check_tokens(rate_limiter, owner_id, user_limits)
    _check_active_and_queue(session, limits, user_limits, owner_id)


def admit_fork(
    session: Session,
    limits: Limits,
    rate_limiter: RateLimiter,
    owner_id: UUID,
    *,
    will_run: bool,
) -> None:
    """Check limits for a fork. One that closes at once uses no compute, so skips those checks."""
    user_limits = _locked_limits(session, limits, owner_id)
    if will_run:
        _check_tokens(rate_limiter, owner_id, user_limits)
        _check_active_and_queue(session, limits, user_limits, owner_id)
    if user_limits.forks_per_day is not None:
        decision = rate_limiter.consume(
            f"forks:{owner_id}", 1, user_limits.forks_per_day, DAY_SECONDS
        )
        if not decision.allowed:
            raise AdmissionRejectedError(
                "forks_per_day",
                f"you've used all {user_limits.forks_per_day} forks for today",
                decision.retry_after_seconds,
            )


def daily_tokens_left(rate_limiter: RateLimiter, user_id: UUID, limit: int) -> int:
    decision = rate_limiter.consume(tokens_key(user_id), 0, limit, DAY_SECONDS)
    return decision.remaining if decision.allowed else 0


def _locked_limits(session: Session, limits: Limits, owner_id: UUID) -> UserLimits:
    """Lock the user's row, so concurrent requests can't all pass a count-then-insert check."""
    user = session.get(User, owner_id, with_for_update={"key_share": True})
    if user is None:
        raise ValueError(f"unknown user {owner_id}")
    return limits.for_kind(user.kind)


def _check_tokens(rate_limiter: RateLimiter, owner_id: UUID, user_limits: UserLimits) -> None:
    if daily_tokens_left(rate_limiter, owner_id, user_limits.tokens_per_day) <= 0:
        raise AdmissionRejectedError(
            "tokens_per_day",
            f"you've used all {user_limits.tokens_per_day} tokens for today",
            DAY_SECONDS,
        )


def _check_active_and_queue(
    session: Session, limits: Limits, user_limits: UserLimits, owner_id: UUID
) -> None:
    statuses = [status.value for status in NON_COMPLETED_STATUSES]
    active = session.execute(
        select(func.count())
        .select_from(Branch)
        .where(Branch.owner_id == owner_id, Branch.status.in_(statuses))
    ).scalar_one()
    if active >= user_limits.active_branches:
        raise AdmissionRejectedError(
            "active_branches",
            f"you have {active} unfinished branches, and the limit is "
            f"{user_limits.active_branches}; wait for one to finish or cancel it",
            30,
        )
    # Counted in the database, so this works during a broker outage and on any queue.
    queued = session.execute(
        select(func.count()).select_from(Branch).where(Branch.status == BranchStatus.QUEUED.value)
    ).scalar_one()
    if queued >= limits.max_queued_branches:
        raise AdmissionRejectedError("queue_full", "the system is busy; try again shortly", 30)
