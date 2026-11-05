"""Interfaces between the core application and everything that differs per build.

The core only imports from this module. `app.factory` picks an implementation for each port
from the active profile, so the local, deployed and test builds share one codebase.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

JsonDict = dict[str, Any]


# Step queue


@dataclass(frozen=True)
class StepMessage:
    """Asks a worker to run one step of a branch. Delivery may happen more than once."""

    branch_id: UUID
    seq: int
    trace_context: dict[str, str] = field(default_factory=dict)


class StepQueue(Protocol):
    def enqueue(self, message: StepMessage) -> None: ...

    def depth(self) -> int:
        """Approximate number of messages waiting, used for admission control."""
        ...


# LLM


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: JsonDict


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LLMRequest:
    messages: Sequence[JsonDict]
    tools: Sequence[JsonDict]
    force_final: bool = False
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    owner_id: UUID | None = None


@dataclass(frozen=True)
class LLMResponse:
    """Either final text or one tool call; the agent makes one tool call per turn."""

    usage: Usage
    text: str | None = None
    tool_call: ToolCall | None = None
    dropped_tool_calls: int = 0


class ProviderRateLimitedError(Exception):
    """The provider refused the call for now. The branch waits, using no compute."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"rate limited, retry after {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class ProviderTransientError(Exception):
    """A brief provider failure, such as a timeout or a 5xx. The step is retried a few times."""


class ProviderRefusedError(Exception):
    """The provider refused the request for good, such as a bad key or an unknown model.

    The message is safe to show and store. It must never include provider response text, which can
    echo part of a key.
    """


class LLMProvider(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...


# Agent tools


@dataclass(frozen=True)
class ToolContext:
    """Identifies the call. Tools take the branch from here, never from model-chosen arguments."""

    branch_id: UUID
    tool_call_node_id: UUID
    bypass_cache: bool = False


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    is_error: bool = False


class ToolExecutor(Protocol):
    def execute(self, context: ToolContext, name: str, arguments: JsonDict) -> ToolOutcome: ...


# Cache and rate limiting


class Cache(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int = 0


class RateLimiter(Protocol):
    def consume(self, key: str, amount: int, limit: int, window_seconds: int) -> RateDecision: ...


# Auth


class PrincipalKind(StrEnum):
    LOCAL = "local"
    GUEST = "guest"
    USER = "user"


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    kind: PrincipalKind


class Authenticator(Protocol):
    def authenticate(self, token: str | None) -> Principal | None: ...


# Live events


@dataclass(frozen=True)
class TreeEvent:
    tree_id: UUID
    seq: int
    type: str
    payload: JsonDict


class LiveEvents(Protocol):
    def publish(self, event: TreeEvent) -> None: ...

    def subscribe(self, tree_id: UUID) -> AsyncIterator[TreeEvent]: ...


# Browser


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class PageText:
    url: str
    title: str
    text: str


class BrowserBlockedError(Exception):
    """A CAPTCHA, block or timeout. The agent sees this as a tool error."""


class Browser(Protocol):
    def search(self, query: str, limit: int) -> list[SearchResult]: ...

    def fetch(self, url: str) -> PageText: ...


# Embeddings


class Embedder(Protocol):
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


# Telemetry


class Telemetry(Protocol):
    def span(
        self, name: str, attributes: JsonDict | None = None
    ) -> AbstractContextManager[None]: ...

    def record(self, metric: str, value: float, attributes: JsonDict | None = None) -> None: ...
