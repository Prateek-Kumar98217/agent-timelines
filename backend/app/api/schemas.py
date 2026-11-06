"""Request and response bodies."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

MAX_TASK_CHARS = 2_000


class CreateTree(BaseModel):
    task: str = Field(min_length=1, max_length=MAX_TASK_CHARS)
    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, min_length=1, max_length=120)


class SaveKey(BaseModel):
    # SecretStr keeps the key out of logs, reprs and validation errors.
    api_key: SecretStr


class ProviderOut(BaseModel):
    name: str
    needs_key: bool
    available: bool
    default_model: str
    key_last_four: str | None


class LimitsOut(BaseModel):
    active_branches: int
    max_active_branches: int
    max_turns: int


class ContinueRequest(BaseModel):
    text: str = Field(min_length=1)


class ForkRequest(BaseModel):
    edit: dict[str, Any]


class Created(BaseModel):
    tree_id: UUID
    branch_id: UUID


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    branch_id: UUID
    parent_id: UUID | None
    edited_from_id: UUID | None
    type: str
    payload: dict[str, Any]
    seq: int
    turn_index: int
    input_tokens: int
    output_tokens: int
    created_at: datetime


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tree_id: UUID
    parent_branch_id: UUID | None
    fork_node_id: UUID | None
    status: str
    status_reason: str | None
    turns_used: int
    extra_turns: int
    input_tokens: int
    output_tokens: int
    created_at: datetime
    finished_at: datetime | None


class TreeSummary(BaseModel):
    id: UUID
    task: str
    created_at: datetime
    branches: int


class TreeOut(BaseModel):
    id: UUID
    branches: list[BranchOut]
    nodes: list[NodeOut]
    last_event_seq: int


class EventOut(BaseModel):
    seq: int
    type: str
    payload: dict[str, Any]


class BranchSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    branch_id: UUID
    status: str
    turns_used: int
    input_tokens: int
    output_tokens: int
    path_tokens: int
    final_text: str | None
    tool_calls: list[str]
    scores: dict[str, float]


class ComparisonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    a: BranchSummaryOut
    b: BranchSummaryOut
    last_common_node_id: UUID
    shared_nodes: int
    same_final_text: bool
    tool_call_overlap: float
    final_similarity: float | None
    converged: bool | None
