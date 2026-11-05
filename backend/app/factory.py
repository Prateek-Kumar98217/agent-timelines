"""Builds the adapters for a profile. This is the only place that chooses between builds."""

from dataclasses import dataclass
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


def build_adapters(settings: Settings) -> Adapters:
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
            return _build_local(settings)
        case Profile.DEPLOYED:
            raise ProfileNotAvailableError(
                f"adapters for the {settings.profile.value} profile are not implemented yet"
            )


def _build_local(settings: Settings) -> Adapters:
    import redis

    from app.adapters.celery_queue import CeleryStepQueue, make_celery
    from app.adapters.mock import EchoTools, MockLLM
    from app.adapters.redis_store import RedisCache, RedisLiveEvents, RedisRateLimiter

    client: redis.Redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return Adapters(
        queue=CeleryStepQueue(make_celery(settings)),
        # Real providers, tools, embeddings and telemetry arrive in milestones 5 and 7.
        llm=MockLLM(settings.mock_llm_latency_seconds, settings.mock_llm_tool_turns),
        tools=EchoTools(settings.mock_tool_latency_seconds),
        cache=RedisCache(client),
        rate_limiter=RedisRateLimiter(client),
        auth=LocalAuthenticator(),
        live_events=RedisLiveEvents(settings.redis_url),
        browser=FixtureBrowser(),
        embedder=HashEmbedder(),
        telemetry=NoopTelemetry(),
    )
