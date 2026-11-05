"""Helpers for engine tests."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.adapters.memory import (
    FixtureTools,
    InlineQueue,
    MemoryLiveEvents,
    MemoryRateLimiter,
    ScriptedLLM,
)
from app.db.models import Branch, BranchState, Node, User
from app.db.session import SessionFactory
from app.domain.config import ToolSpec, TreeConfig
from app.domain.engine import EngineDeps, StepOutcome, run_step
from app.domain.limits import Limits, UserLimits
from app.ports import LLMProvider, LLMResponse, ToolCall, Usage

SEARCH = ToolSpec(
    name="search",
    description="Search",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
)
# Tests of other behaviour shouldn't trip resource limits. Tests of limits pass their own.
GENEROUS_LIMITS = Limits(
    {
        kind: UserLimits(10_000, 10_000, 100_000_000, writes_per_minute=10_000)
        for kind in ("local", "user", "guest")
    },
    max_queued_branches=10_000,
)
CONFIG = TreeConfig(system_prompt="sys", tools=[SEARCH])


def text_reply(text: str = "final answer", tokens: int = 10) -> LLMResponse:
    return LLMResponse(usage=Usage(tokens, tokens), text=text)


def tool_reply(query: str = "q", tokens: int = 10, text: str | None = None) -> LLMResponse:
    return LLMResponse(
        usage=Usage(tokens, tokens), text=text, tool_call=ToolCall("search", {"query": query})
    )


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


class Harness:
    """An engine wired to in-memory adapters and a real database."""

    def __init__(
        self,
        session_factory: SessionFactory,
        llm: LLMProvider | Sequence[Any] = (),
        limits: Limits | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.queue = InlineQueue()
        self.llm: Any = llm if hasattr(llm, "complete") else ScriptedLLM(llm)
        self.tools = FixtureTools()
        self.events = MemoryLiveEvents()
        # A frozen clock, so a test can never straddle a rate limit window.
        self.rate_limiter = MemoryRateLimiter(clock=lambda: 1_000_000.0)
        self.clock = Clock()
        self.deps = EngineDeps(
            session_factory=session_factory,
            queue=self.queue,
            llm=self.llm,
            tools=self.tools,
            live_events=self.events,
            rate_limiter=self.rate_limiter,
            limits=limits or GENEROUS_LIMITS,
            now=self.clock,
        )
        with session_factory() as session:
            self.user = User(id=uuid4(), kind="local")
            session.add(self.user)
            session.commit()

    def drain(self) -> list[StepOutcome]:
        return [run_step(self.deps, message) for message in self.queue.drain()]

    def branch(self, branch_id: UUID) -> Branch:
        with self.session_factory() as session:
            return session.get_one(Branch, branch_id)

    def state(self, branch_id: UUID) -> BranchState:
        with self.session_factory() as session:
            return session.get_one(BranchState, branch_id)

    def nodes(self, branch_id: UUID) -> list[Node]:
        with self.session_factory() as session:
            return list(
                session.execute(
                    select(Node).where(Node.branch_id == branch_id).order_by(Node.seq)
                ).scalars()
            )

    def types(self, branch_id: UUID) -> list[str]:
        return [node.type for node in self.nodes(branch_id)]
