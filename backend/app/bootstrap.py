"""Wires settings, adapters and the database into something runnable."""

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy.dialects.postgresql import insert

from app.db.models import User
from app.db.session import SessionFactory, make_session_factory
from app.domain.deps import EngineDeps
from app.factory import LOCAL_USER_ID, Adapters, build_adapters
from app.settings import Profile, Settings, get_settings


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    adapters: Adapters
    session_factory: SessionFactory
    deps: EngineDeps


def build_runtime(settings: Settings) -> Runtime:
    adapters = build_adapters(settings)
    session_factory = make_session_factory(settings.database_url)
    if settings.profile is Profile.LOCAL:
        ensure_local_user(session_factory)
    deps = EngineDeps(
        session_factory=session_factory,
        queue=adapters.queue,
        llm=adapters.llm,
        tools=adapters.tools,
        live_events=adapters.live_events,
        lease_seconds=settings.lease_seconds,
    )
    return Runtime(settings, adapters, session_factory, deps)


def ensure_local_user(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        session.execute(
            insert(User)
            .values(id=LOCAL_USER_ID, kind="local")
            .on_conflict_do_nothing(index_elements=[User.id])
        )
        session.commit()


@lru_cache
def get_runtime() -> Runtime:
    """One runtime per process. Workers build theirs after forking, so nothing is shared."""
    return build_runtime(get_settings())
