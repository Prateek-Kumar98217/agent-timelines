"""Tables for trees, branches and nodes.

Nodes are the immutable history. A database trigger rejects any UPDATE on them, and parent links
restrict deletes, so a node with descendants cannot be removed. `BranchState` is each branch's own
working copy: forking writes a new row and never shares one.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIMENSIONS = 384


class NodeType(StrEnum):
    ROOT = "root"
    USER_MSG = "user_msg"
    LLM_OUTPUT = "llm_output"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


class BranchStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    CAPPED = "capped"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"
    CANCELLED = "cancelled"


NON_COMPLETED_STATUSES = frozenset(
    {BranchStatus.QUEUED, BranchStatus.RUNNING, BranchStatus.WAITING}
)


class NextAction(StrEnum):
    LLM = "llm"
    TOOL = "tool"
    NONE = "none"


def _enum_check(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tree(Base):
    __tablename__ = "trees"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    next_event_seq: Mapped[int] = mapped_column(BigInteger, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (
        CheckConstraint(_enum_check("status", BranchStatus), name="status_valid"),
        Index("ix_branches_owner_status", "owner_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tree_id: Mapped[UUID] = mapped_column(ForeignKey("trees.id"), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    parent_branch_id: Mapped[UUID | None] = mapped_column(ForeignKey("branches.id"), index=True)
    # The node that was edited to create this branch. It belongs to the parent branch.
    fork_node_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("nodes.id", use_alter=True, name="fk_branches_fork_node_id")
    )
    status: Mapped[str] = mapped_column(String(24))
    status_reason: Mapped[str | None] = mapped_column(Text)
    # Turns used along the whole root-to-leaf path, so forks inherit them.
    turns_used: Mapped[int] = mapped_column(Integer, default=0)
    # Tokens spent by this branch only. Copied history cost nothing here.
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # The step a queue message must name to be accepted; older messages are duplicates.
    step_seq: Mapped[int] = mapped_column(Integer, default=0)
    next_node_seq: Mapped[int] = mapped_column(Integer, default=0)
    # Claims of the current step. A step that keeps killing workers is failed, not retried forever.
    step_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        UniqueConstraint("branch_id", "seq", name="uq_nodes_branch_seq"),
        CheckConstraint(_enum_check("type", NodeType), name="type_valid"),
        CheckConstraint(
            "(type = 'root') = (parent_id IS NULL)",
            name="only_root_has_no_parent",
        ),
        Index(
            "uq_nodes_one_root_per_tree",
            "tree_id",
            unique=True,
            postgresql_where=text("type = 'root'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tree_id: Mapped[UUID] = mapped_column(ForeignKey("trees.id"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id"), index=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="RESTRICT"), index=True
    )
    # The node this one replaces, when it is the first node of a fork.
    edited_from_id: Mapped[UUID | None] = mapped_column(ForeignKey("nodes.id", ondelete="RESTRICT"))
    type: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    seq: Mapped[int] = mapped_column(Integer)
    turn_index: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BranchState(Base):
    __tablename__ = "branch_state"
    __table_args__ = (CheckConstraint(_enum_check("next_action", NextAction), name="action_valid"),)

    branch_id: Mapped[UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), primary_key=True
    )
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    head_node_id: Mapped[UUID] = mapped_column(ForeignKey("nodes.id"))
    next_action: Mapped[str] = mapped_column(String(8))
    # True when the pending tool call was edited, so it must run live and skip the cache read.
    bypass_cache: Mapped[bool] = mapped_column(default=False)


class StepOutbox(Base):
    """Steps due to be sent to the queue, written in the same transaction as the work before them.

    A row with a future `available_at` is how a branch waits: nothing holds it in memory.
    """

    __tablename__ = "step_outbox"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    branch_id: Mapped[UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScratchpadChunk(Base):
    __tablename__ = "scratchpad_chunks"
    __table_args__ = (
        UniqueConstraint(
            "branch_id", "source_node_id", "chunk_index", name="uq_scratchpad_write_once"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"))
    # The tool call that wrote this chunk. Copies keep it, which is how a fork selects what it had.
    source_node_id: Mapped[UUID] = mapped_column(ForeignKey("nodes.id"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))


class KbDocument(Base):
    __tablename__ = "kb_documents"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64))


class KbChunk(Base):
    __tablename__ = "kb_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_kb_chunks_position"),
        Index(
            "ix_kb_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_kb_chunks_search", "search", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("kb_documents.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    search: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )


class TreeEventRow(Base):
    __tablename__ = "tree_events"
    __table_args__ = (UniqueConstraint("tree_id", "seq", name="uq_tree_events_tree_seq"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tree_id: Mapped[UUID] = mapped_column(ForeignKey("trees.id"))
    seq: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
