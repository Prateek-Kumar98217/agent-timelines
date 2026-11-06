"""The real API and engine with a scripted model and tools, in a throwaway database.

Browser tests run against this, so they exercise the real rules without a GPU, a provider or the
data in the local stack. Run it with: uv run python web/e2e/api_server.py
"""

import os
import queue
import secrets
import threading
import time
from pathlib import Path
from uuid import uuid4

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text

from app.adapters.memory import (
    FixtureBrowser,
    FixtureTools,
    HashEmbedder,
    MemoryCache,
    MemoryLiveEvents,
    MemoryRateLimiter,
    NoopTelemetry,
)
from app.adapters.mock import MockLLM
from app.api.main import create_app
from app.bootstrap import Runtime, ensure_local_user, make_limits
from app.db.session import make_session_factory
from app.domain.deps import EngineDeps
from app.domain.engine import run_step
from app.domain.keys import KeyStore
from app.domain.sweeper import sweep
from app.factory import LOCAL_USER_ID, Adapters
from app.ports import Principal, PrincipalKind, StepMessage
from app.settings import Profile, Settings

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
DATABASE_PREFIX = "timelines_e2e_"
PORT = int(os.environ.get("E2E_API_PORT", "8001"))


class LocalAuth:
    def authenticate(self, token: str | None) -> Principal:
        return Principal(LOCAL_USER_ID, PrincipalKind.LOCAL)


class ThreadQueue:
    """Runs steps on one background thread, in place of Celery."""

    def __init__(self) -> None:
        self.messages: queue.Queue[StepMessage] = queue.Queue()

    def enqueue(self, message: StepMessage) -> None:
        self.messages.put(message)

    def depth(self) -> int:
        return self.messages.qsize()


def create_database(server_url: str) -> tuple[str, str]:
    name = f"{DATABASE_PREFIX}{uuid4().hex[:12]}"
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        # A run that was killed can't drop its own database, so the next one does.
        leftovers = connection.execute(
            text("SELECT datname FROM pg_database WHERE starts_with(datname, :prefix)"),
            {"prefix": DATABASE_PREFIX},
        ).scalars()
        for leftover in list(leftovers):
            connection.execute(text(f'DROP DATABASE "{leftover}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    url = make_url(server_url).set(database=name).render_as_string(hide_password=False)
    config = Config(BACKEND_DIR / "alembic.ini")
    config.attributes["url"] = url
    command.upgrade(config, "head")
    return name, url


def drop_database(server_url: str, name: str) -> None:
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


def main() -> None:
    base = Settings()
    name, url = create_database(base.database_url)
    try:
        settings = base.model_copy(
            update={"profile": Profile.TEST, "database_url": url, "allow_mock_llm": True}
        )
        sessions = make_session_factory(url)
        ensure_local_user(sessions)
        steps = ThreadQueue()
        adapters = Adapters(
            queue=steps,
            llm=MockLLM(latency_seconds=0.4, tool_turns=2),
            tools=FixtureTools(),
            cache=MemoryCache(),
            rate_limiter=MemoryRateLimiter(),
            auth=LocalAuth(),
            live_events=MemoryLiveEvents(),
            browser=FixtureBrowser(),
            embedder=HashEmbedder(),
            telemetry=NoopTelemetry(),
        )
        deps = EngineDeps(
            session_factory=sessions,
            queue=adapters.queue,
            llm=adapters.llm,
            tools=adapters.tools,
            live_events=adapters.live_events,
            rate_limiter=adapters.rate_limiter,
            limits=make_limits(settings),
            telemetry=adapters.telemetry,
            lease_seconds=settings.lease_seconds,
        )
        keys = KeyStore(sessions, secrets.token_bytes(32))
        runtime = Runtime(settings, adapters, sessions, deps, keys)

        def work() -> None:
            while True:
                run_step(deps, steps.messages.get())

        def sweeping() -> None:
            while True:
                time.sleep(2)
                sweep(deps)

        threading.Thread(target=work, daemon=True).start()
        threading.Thread(target=sweeping, daemon=True).start()
        uvicorn.run(create_app(runtime), host="127.0.0.1", port=PORT, log_level="warning")
    finally:
        drop_database(base.database_url, name)


if __name__ == "__main__":
    main()
