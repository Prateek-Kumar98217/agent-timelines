from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Branch, BranchStatus, Node, NodeType, Tree, User
from tests.conftest import alembic_config


def _tree_with_root(session: Session) -> tuple[Branch, Node]:
    user = User(id=uuid4(), kind="local")
    tree = Tree(id=uuid4(), owner_id=user.id)
    branch = Branch(id=uuid4(), tree_id=tree.id, owner_id=user.id, status=BranchStatus.QUEUED.value)
    root = Node(
        id=uuid4(),
        tree_id=tree.id,
        branch_id=branch.id,
        type=NodeType.ROOT.value,
        payload={"task": "t"},
        seq=0,
        turn_index=0,
    )
    session.add(user)
    session.flush()
    session.add(tree)
    session.flush()
    session.add(branch)
    session.flush()
    session.add(root)
    session.flush()
    return branch, root


def test_migrations_match_models(database_url: str) -> None:
    command.check(alembic_config(database_url))


def test_migration_downgrades_and_upgrades_cleanly(database_url: str) -> None:
    config = alembic_config(database_url)
    command.downgrade(config, "base")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('nodes')")).scalar() is None
    engine.dispose()
    command.upgrade(config, "head")


def test_node_update_is_rejected_by_orm_and_raw_sql(session: Session) -> None:
    _, root = _tree_with_root(session)
    session.commit()
    with pytest.raises(DBAPIError, match="nodes are immutable"):
        session.execute(update(Node).where(Node.id == root.id).values(payload={"task": "changed"}))
    session.rollback()
    with pytest.raises(DBAPIError, match="nodes are immutable"):
        session.execute(text("UPDATE nodes SET seq = 99"))
    session.rollback()
    assert session.get(Node, root.id).payload == {"task": "t"}  # type: ignore[union-attr]


def test_node_with_children_cannot_be_deleted(session: Session) -> None:
    branch, root = _tree_with_root(session)
    session.add(
        Node(
            tree_id=root.tree_id,
            branch_id=branch.id,
            parent_id=root.id,
            type=NodeType.USER_MSG.value,
            payload={"text": "hi"},
            seq=1,
            turn_index=0,
        )
    )
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(text("DELETE FROM nodes WHERE id = :id"), {"id": root.id})
    session.rollback()


def test_step_seq_uniqueness_blocks_duplicate_nodes(session: Session) -> None:
    branch, root = _tree_with_root(session)
    duplicate = Node(
        tree_id=root.tree_id,
        branch_id=branch.id,
        parent_id=root.id,
        type=NodeType.USER_MSG.value,
        payload={"text": "hi"},
        seq=root.seq,
        turn_index=0,
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError, match="uq_nodes_branch_seq"):
        session.flush()
    session.rollback()


def test_a_non_root_node_needs_a_parent_and_a_tree_has_one_root(session: Session) -> None:
    branch, root = _tree_with_root(session)
    session.add(
        Node(
            tree_id=root.tree_id,
            branch_id=branch.id,
            type=NodeType.ROOT.value,
            payload={},
            seq=5,
            turn_index=0,
        )
    )
    with pytest.raises(IntegrityError, match="uq_nodes_one_root_per_tree"):
        session.flush()
    session.rollback()
    branch, root = _tree_with_root(session)
    session.add(
        Node(
            tree_id=root.tree_id,
            branch_id=branch.id,
            type=NodeType.USER_MSG.value,
            payload={},
            seq=5,
            turn_index=0,
        )
    )
    with pytest.raises(IntegrityError, match="only_root_has_no_parent"):
        session.flush()
    session.rollback()
