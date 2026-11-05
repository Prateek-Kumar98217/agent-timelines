"""What the engine needs from the outside world."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.agent.graph import build_step_graph
from app.db.session import SessionFactory
from app.domain.limits import Limits
from app.ports import (
    LiveEvents,
    LLMProvider,
    NoopTelemetry,
    RateLimiter,
    StepQueue,
    Telemetry,
    ToolExecutor,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class EngineDeps:
    session_factory: SessionFactory
    queue: StepQueue
    llm: LLMProvider
    tools: ToolExecutor
    live_events: LiveEvents
    rate_limiter: RateLimiter
    limits: Limits = field(default_factory=Limits)
    telemetry: Telemetry = field(default_factory=NoopTelemetry)
    now: Callable[[], datetime] = _utcnow
    lease_seconds: int = 300
    max_step_attempts: int = 3
    graph: Any = field(init=False)

    def __post_init__(self) -> None:
        self.graph = build_step_graph(self.llm, self.tools)
