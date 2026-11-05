"""Builds the adapters for a profile. This is the only place that chooses between builds."""

from dataclasses import dataclass

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
    RateLimiter,
    StepQueue,
    Telemetry,
    ToolExecutor,
)
from app.settings import Profile, Settings


class ProfileNotAvailableError(RuntimeError):
    """The profile's adapters have not been built yet."""


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
        case Profile.LOCAL | Profile.DEPLOYED:
            raise ProfileNotAvailableError(
                f"adapters for the {settings.profile.value} profile are not implemented yet"
            )
