"""Wires settings, adapters and the database into something runnable."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from app.db.models import User
from app.db.session import SessionFactory, make_session_factory
from app.domain.deps import EngineDeps
from app.domain.keys import KeyStore, load_or_create_master_key
from app.factory import LOCAL_USER_ID, Adapters, build_adapters
from app.settings import Profile, Settings, get_settings


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    adapters: Adapters
    session_factory: SessionFactory
    deps: EngineDeps
    key_store: KeyStore | None = None


def build_runtime(settings: Settings) -> Runtime:
    session_factory = make_session_factory(settings.database_url)
    key_store = None
    if settings.profile is Profile.LOCAL:
        ensure_local_user(session_factory)
        master_key = load_or_create_master_key(Path(settings.master_key_file))
        key_store = KeyStore(session_factory, master_key)
    adapters = build_adapters(settings, key_store.get if key_store else None)
    deps = EngineDeps(
        session_factory=session_factory,
        queue=adapters.queue,
        llm=adapters.llm,
        tools=adapters.tools,
        live_events=adapters.live_events,
        lease_seconds=settings.lease_seconds,
    )
    return Runtime(settings, adapters, session_factory, deps, key_store)


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
