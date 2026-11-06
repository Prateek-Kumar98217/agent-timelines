"""Builds the adapters for a profile. This is the only place that chooses between builds."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.adapters.memory import (
    FakeAuthenticator,
    FixtureBrowser,
    FixtureTools,
    HashEmbedder,
    InlineQueue,
    MemoryCache,
    MemoryLiveEvents,
    MemoryRateLimiter,
    NoopTelemetry,
    ScriptedLLM,
)
from app.ports import (
    Authenticator,
    Browser,
    Cache,
    Embedder,
    LiveEvents,
    LLMProvider,
    Principal,
    PrincipalKind,
    RateLimiter,
    StepQueue,
    Telemetry,
    ToolExecutor,
)
from app.settings import Profile, Settings

LOCAL_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


class ProfileNotAvailableError(RuntimeError):
    """The profile's adapters have not been built yet."""


class LocalAuthenticator:
    """The local build has one user unless GitHub sign-in is configured."""

    def authenticate(self, token: str | None) -> Principal | None:
        return Principal(LOCAL_USER_ID, PrincipalKind.LOCAL)


@dataclass(frozen=True)
class Adapters:
    queue: StepQueue
    llm: LLMProvider
    tools: ToolExecutor
    cache: Cache
    rate_limiter: RateLimiter
    auth: Authenticator
    live_events: LiveEvents
    browser: Browser
    embedder: Embedder
    telemetry: Telemetry


KeyLookup = Callable[[Any, str], str | None]


def build_adapters(settings: Settings, key_lookup: KeyLookup | None = None) -> Adapters:
    match settings.profile:
        case Profile.TEST:
            return Adapters(
                queue=InlineQueue(),
                llm=ScriptedLLM([]),
                tools=FixtureTools(),
                cache=MemoryCache(),
                rate_limiter=MemoryRateLimiter(),
                auth=FakeAuthenticator(),
                live_events=MemoryLiveEvents(),
                browser=FixtureBrowser(),
                embedder=HashEmbedder(),
                telemetry=NoopTelemetry(),
            )
        case Profile.LOCAL:
            return _build_local(settings, key_lookup or (lambda _user, _provider: None))
        case Profile.DEPLOYED:
            raise ProfileNotAvailableError(
                f"adapters for the {settings.profile.value} profile are not implemented yet"
            )


def _build_local(settings: Settings, key_lookup: KeyLookup) -> Adapters:
    import redis

    from app.adapters.celery_queue import CeleryStepQueue, make_publisher
    from app.adapters.embeddings import FastEmbedEmbedder
    from app.adapters.llm import RoutedLLM
    from app.adapters.mcp_tools import McpToolExecutor
    from app.adapters.mock import MockLLM
    from app.adapters.redis_store import RedisCache, RedisLiveEvents, RedisRateLimiter
    from app.adapters.telemetry import OtelTelemetry
    from app.adapters.web import LocalWeb

    client: redis.Redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    mock = (
        MockLLM(settings.mock_llm_latency_seconds, settings.mock_llm_tool_turns)
        if settings.allow_mock_llm
        else None
    )
    telemetry: Telemetry = (
        OtelTelemetry(settings.service_name, settings.otel_endpoint)
        if settings.otel_endpoint
        else NoopTelemetry()
    )
    return Adapters(
        queue=CeleryStepQueue(make_publisher(settings)),
        llm=RoutedLLM(key_lookup, settings.ollama_url, mock),
        tools=McpToolExecutor(
            settings.gateway_url, settings.gateway_token_secret, telemetry=telemetry
        ),
        cache=RedisCache(client),
        rate_limiter=RedisRateLimiter(client),
        auth=LocalAuthenticator(),
        live_events=RedisLiveEvents(settings.redis_url),
        browser=LocalWeb(settings.searxng_url, settings.browser_url),
        embedder=FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir),
        telemetry=telemetry,
    )
