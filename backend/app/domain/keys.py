"""Encrypted storage for users' API keys.

Keys are sealed with AES-GCM, bound to their user and provider so a stored value can't be moved to
another row. Plaintext is only returned to code that's about to call the provider.
"""

import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import ProviderKey
from app.db.session import SessionFactory

KEY_BYTES = 32
NONCE_BYTES = 12


class KeyUnreadableError(Exception):
    """A stored key can't be decrypted, usually because the master key changed."""


def load_or_create_master_key(path: Path) -> bytes:
    """Read the master key, creating it on first boot. Racing processes agree on one file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "wb") as file:
            file.write(secrets.token_bytes(KEY_BYTES))
    for _ in range(100):
        key = path.read_bytes()
        if len(key) == KEY_BYTES:
            return key
        # Another process has created the file and is still writing it.
        time.sleep(0.01)
    raise ValueError(f"the master key at {path} isn't {KEY_BYTES} bytes")


@dataclass(frozen=True)
class StoredKey:
    provider: str
    last_four: str


class KeyStore:
    def __init__(self, session_factory: SessionFactory, master_key: bytes) -> None:
        self._session_factory = session_factory
        self._cipher = AESGCM(master_key)

    def put(self, user_id: UUID, provider: str, api_key: str) -> StoredKey:
        nonce = secrets.token_bytes(NONCE_BYTES)
        ciphertext = self._cipher.encrypt(nonce, api_key.encode(), _bound_to(user_id, provider))
        last_four = api_key[-4:]
        values = {"nonce": nonce, "ciphertext": ciphertext, "last_four": last_four}
        with self._session_factory() as session:
            statement = insert(ProviderKey).values(user_id=user_id, provider=provider, **values)
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[ProviderKey.user_id, ProviderKey.provider], set_=values
                )
            )
            session.commit()
        return StoredKey(provider, last_four)

    def get(self, user_id: UUID, provider: str) -> str | None:
        with self._session_factory() as session:
            row = session.get(ProviderKey, (user_id, provider))
        if row is None:
            return None
        try:
            plain = self._cipher.decrypt(row.nonce, row.ciphertext, _bound_to(user_id, provider))
        except InvalidTag as exc:
            raise KeyUnreadableError(f"the stored {provider} key can't be read") from exc
        return plain.decode()

    def list(self, user_id: UUID) -> list[StoredKey]:
        with self._session_factory() as session:
            rows = session.execute(
                select(ProviderKey.provider, ProviderKey.last_four)
                .where(ProviderKey.user_id == user_id)
                .order_by(ProviderKey.provider)
            ).all()
        return [StoredKey(row.provider, row.last_four) for row in rows]

    def delete(self, user_id: UUID, provider: str) -> bool:
        with self._session_factory() as session:
            result = session.execute(
                delete(ProviderKey).where(
                    ProviderKey.user_id == user_id, ProviderKey.provider == provider
                )
            )
            session.commit()
        return bool(result.rowcount)  # type: ignore[attr-defined]


def _bound_to(user_id: UUID, provider: str) -> bytes:
    return f"{user_id}:{provider}".encode()
