"""Alembic environment. The database URL comes from `-x url=...` or the app settings."""

from alembic import context
from sqlalchemy import create_engine, pool

from app.db.models import Base
from app.settings import Settings


def _database_url() -> str:
    url = context.config.attributes.get("url") or context.get_x_argument(as_dictionary=True).get(
        "url"
    )
    return str(url) if url else Settings().database_url


engine = create_engine(_database_url(), poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
