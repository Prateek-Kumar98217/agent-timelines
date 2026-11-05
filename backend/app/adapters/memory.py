"""In-process, deterministic adapters for the test profile. They make no network calls."""

import asyncio
import hashlib
import math
import time
from collections import defaultdict, deque
from collections.abc import AsyncGenerator, Callable, Iterable, Iterator, Sequence
from contextlib import AbstractContextManager, nullcontext
from uuid import UUID

from app.ports import (
    BrowserBlockedError,
    JsonDict,
    LLMRequest,
    LLMResponse,
    PageText,
    Principal,
    RateDecision,
    SearchResult,
    StepMessage,
    ToolContext,
    ToolOutcome,
    TreeEvent,
)

Clock = Callable[[], float]


class InlineQueue:
    """Holds messages until a test drains them, so ordering is explicit."""

    def __init__(self) -> None:
        self.messages: deque[StepMessage] = deque()

    def enqueue(self, message: StepMessage) -> None:
        self.messages.append(message)

    def depth(self) -> int:
        return len(self.messages)

    def drain(self) -> Iterator[StepMessage]:
        """Yield messages until empty, including any enqueued while draining."""
        while self.messages:
            yield self.messages.popleft()


class ScriptedLLM:
    """Replays a fixed list of responses and records each request."""

    def __init__(self, responses: Iterable[LLMResponse | Exception]) -> None:
        self._responses = deque(responses)
        self.requests: list[LLMRequest] = []

    def add(self, response: LLMResponse | Exception) -> None:
        self._responses.append(response)

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("ScriptedLLM ran out of responses")
        response = self._responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


ToolHandler = Callable[[ToolContext, JsonDict], ToolOutcome]


class FixtureTools:
    """Runs handlers supplied by the test and records every call."""

    def __init__(self, handlers: dict[str, ToolHandler] | None = None) -> None:
        self.handlers = handlers or {}
        self.calls: list[tuple[ToolContext, str, JsonDict]] = []

    def execute(self, context: ToolContext, name: str, arguments: JsonDict) -> ToolOutcome:
        self.calls.append((context, name, arguments))
        handler = self.handlers.get(name)
        if handler is None:
            return ToolOutcome(f"result of {name}({arguments})")
        return handler(context, arguments)


class MemoryCache:
    def __init__(self, clock: Clock = time.monotonic) -> None:
        self._clock = clock
        self._items: dict[str, tuple[str, float]] = {}

    def get(self, key: str) -> str | None:
        item = self._items.get(key)
        if item is None:
            return None
        value, expires_at = item
        if self._clock() >= expires_at:
            del self._items[key]
            return None
        return value

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._items[key] = (value, self._clock() + ttl_seconds)


class MemoryRateLimiter:
    """Fixed-window counter. A refused request consumes nothing."""

    def __init__(self, clock: Clock = time.time) -> None:
        self._clock = clock
        self._counts: dict[tuple[str, int], int] = defaultdict(int)

    def consume(self, key: str, amount: int, limit: int, window_seconds: int) -> RateDecision:
        now = self._clock()
        window = int(now // window_seconds)
        used = self._counts[(key, window)]
        if used + amount > limit:
            retry_after = max(1, math.ceil((window + 1) * window_seconds - now))
            return RateDecision(False, max(0, limit - used), retry_after)
        self._counts[(key, window)] = used + amount
        return RateDecision(True, limit - used - amount)

    def charge(self, key: str, amount: int, window_seconds: int) -> None:
        self._counts[(key, int(self._clock() // window_seconds))] += amount


class FakeAuthenticator:
    """Maps tokens to principals. Unknown or missing tokens are unauthenticated."""

    def __init__(self, principals: dict[str, Principal] | None = None) -> None:
        self.principals = principals or {}

    def authenticate(self, token: str | None) -> Principal | None:
        return self.principals.get(token) if token else None


class MemoryLiveEvents:
    def __init__(self) -> None:
        self._subscribers: dict[UUID, list[asyncio.Queue[TreeEvent]]] = defaultdict(list)
        self.published: list[TreeEvent] = []

    def publish(self, event: TreeEvent) -> None:
        self.published.append(event)
        for queue in self._subscribers[event.tree_id]:
            queue.put_nowait(event)

    async def subscribe(self, tree_id: UUID) -> AsyncGenerator[TreeEvent]:
        queue: asyncio.Queue[TreeEvent] = asyncio.Queue()
        self._subscribers[tree_id].append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers[tree_id].remove(queue)


class FixtureBrowser:
    """Serves canned pages and results. Anything else counts as blocked."""

    def __init__(
        self,
        results: dict[str, list[SearchResult]] | None = None,
        pages: dict[str, PageText] | None = None,
    ) -> None:
        self.results = results or {}
        self.pages = pages or {}

    def search(self, query: str, limit: int) -> list[SearchResult]:
        if query not in self.results:
            raise BrowserBlockedError(f"no fixture for query: {query}")
        return self.results[query][:limit]

    def fetch(self, url: str) -> PageText:
        if url not in self.pages:
            raise BrowserBlockedError(f"no fixture for url: {url}")
        return self.pages[url]


class HashEmbedder:
    """Repeatable unit vectors from a hash. Equal text gives equal vectors; no semantics."""

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        raw: list[float] = []
        counter = 0
        while len(raw) < self.dimensions:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            raw.extend(byte - 127.5 for byte in digest)
            counter += 1
        vector = raw[: self.dimensions]
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector]


class NoopTelemetry:
    def span(self, name: str, attributes: JsonDict | None = None) -> AbstractContextManager[None]:
        return nullcontext()

    def record(self, metric: str, value: float, attributes: JsonDict | None = None) -> None:
        return None
