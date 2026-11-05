"""Shared fixtures. Integration tests get a real Postgres database created for the test run."""

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session

from app.db.models import Base
from app.db.session import SessionFactory, make_session_factory
from app.settings import Settings

BACKEND_DIR = Path(__file__).resolve().parent.parent


def alembic_config(url: str) -> Config:
    config = Config(BACKEND_DIR / "alembic.ini")
    config.attributes["url"] = url
    return config


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Create a throwaway database and migrate it. Start Postgres with `docker compose up`."""
    server_url = make_url(Settings().database_url)
    name = f"timelines_test_{uuid4().hex[:12]}"
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = server_url.set(database=name).render_as_string(hide_password=False)
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def session_factory(database_url: str) -> SessionFactory:
    return make_session_factory(database_url)


@pytest.fixture
def clean_db(session_factory: SessionFactory) -> SessionFactory:
    """Empty every table before the test. TRUNCATE doesn't fire the immutability trigger."""
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with session_factory() as session:
        session.execute(text(f"TRUNCATE {tables} CASCADE"))
        session.commit()
    return session_factory


@pytest.fixture
def session(clean_db: SessionFactory) -> Iterator[Session]:
    with clean_db() as db_session:
        yield db_session
