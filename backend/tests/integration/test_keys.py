import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text, update

from app.db.models import ProviderKey, User
from app.db.session import SessionFactory
from app.domain.keys import KeyStore, KeyUnreadableError, load_or_create_master_key

MASTER = b"m" * 32
API_KEY = "gsk-a-very-secret-key-WXYZ"


@pytest.fixture
def user(clean_db: SessionFactory) -> User:
    with clean_db() as session:
        row = User(id=uuid4(), kind="local")
        session.add(row)
        session.commit()
    return row


@pytest.fixture
def store(clean_db: SessionFactory) -> KeyStore:
    return KeyStore(clean_db, MASTER)


def test_round_trip_and_masked_listing(store: KeyStore, user: User) -> None:
    stored = store.put(user.id, "groq", API_KEY)
    assert stored.last_four == "WXYZ"
    assert store.get(user.id, "groq") == API_KEY
    assert [(key.provider, key.last_four) for key in store.list(user.id)] == [("groq", "WXYZ")]


def test_saving_again_replaces_the_key(store: KeyStore, user: User) -> None:
    store.put(user.id, "groq", API_KEY)
    store.put(user.id, "groq", "new-key-1234")
    assert store.get(user.id, "groq") == "new-key-1234"
    assert len(store.list(user.id)) == 1


def test_missing_and_deleted_keys(store: KeyStore, user: User) -> None:
    assert store.get(user.id, "groq") is None
    assert store.delete(user.id, "groq") is False
    store.put(user.id, "groq", API_KEY)
    assert store.delete(user.id, "groq") is True
    assert store.get(user.id, "groq") is None


def test_the_database_never_holds_plaintext(
    store: KeyStore, user: User, clean_db: SessionFactory
) -> None:
    store.put(user.id, "groq", API_KEY)
    with clean_db() as session:
        row = session.execute(text("SELECT nonce, ciphertext, last_four FROM provider_keys")).one()
    assert API_KEY.encode() not in bytes(row.ciphertext)
    assert b"very-secret" not in bytes(row.ciphertext)


def test_encrypting_twice_gives_different_ciphertext(
    store: KeyStore, user: User, clean_db: SessionFactory
) -> None:
    def ciphertext() -> bytes:
        store.put(user.id, "groq", API_KEY)
        with clean_db() as session:
            return bytes(session.execute(text("SELECT ciphertext FROM provider_keys")).scalar_one())

    assert ciphertext() != ciphertext()


def test_a_key_moved_to_another_provider_or_user_cant_be_read(
    store: KeyStore, user: User, clean_db: SessionFactory
) -> None:
    store.put(user.id, "groq", API_KEY)
    with clean_db() as session:
        session.execute(update(ProviderKey).values(provider="gemini"))
        session.commit()
    with pytest.raises(KeyUnreadableError):
        store.get(user.id, "gemini")


def test_another_master_key_cant_read_it(
    store: KeyStore, user: User, clean_db: SessionFactory
) -> None:
    store.put(user.id, "groq", API_KEY)
    with pytest.raises(KeyUnreadableError):
        KeyStore(clean_db, b"x" * 32).get(user.id, "groq")


def test_master_key_is_created_once_with_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "master.key"
    first = load_or_create_master_key(path)
    assert len(first) == 32
    assert load_or_create_master_key(path) == first
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_processes_racing_to_create_the_master_key_agree(tmp_path: Path) -> None:
    path = tmp_path / "master.key"
    barrier = threading.Barrier(8)

    def load(_: int) -> bytes:
        barrier.wait()
        return load_or_create_master_key(path)

    with ThreadPoolExecutor(8) as pool:
        assert len(set(pool.map(load, range(8)))) == 1


def test_a_damaged_master_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "master.key"
    path.write_bytes(b"short")
    with pytest.raises(ValueError, match="isn't 32 bytes"):
        load_or_create_master_key(path)
