"""Behaviour every cache and rate limiter must share, on each build's implementation."""

import time
from collections.abc import Callable
from uuid import uuid4

import pytest
import redis

from app.adapters.memory import MemoryCache, MemoryRateLimiter
from app.adapters.redis_store import RedisCache, RedisRateLimiter
from app.ports import Cache, RateLimiter
from app.settings import Settings


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def client() -> redis.Redis:
    return redis.Redis.from_url(Settings().redis_url, decode_responses=True)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(params=["memory", "redis"])
def cache_and_advance(
    request: pytest.FixtureRequest, clock: FakeClock, client: redis.Redis
) -> tuple[Cache, Callable[[float], None]]:
    """A cache and a way to move its time. Redis expiry is real, so that one waits."""
    if request.param == "memory":

        def advance(seconds: float) -> None:
            clock.now += seconds

        return MemoryCache(clock), advance
    return RedisCache(client, prefix=f"test:{uuid4().hex}:"), time.sleep


@pytest.fixture(params=["memory", "redis"])
def limiter(request: pytest.FixtureRequest, clock: FakeClock, client: redis.Redis) -> RateLimiter:
    if request.param == "memory":
        return MemoryRateLimiter(clock)
    return RedisRateLimiter(client, prefix=f"test:{uuid4().hex}:", clock=clock)


def test_cache_returns_value_until_ttl(
    cache_and_advance: tuple[Cache, Callable[[float], None]],
) -> None:
    cache, advance = cache_and_advance
    cache.set("k", "v", ttl_seconds=1)
    assert cache.get("k") == "v"
    advance(1.1)
    assert cache.get("k") is None


def test_cache_miss_is_none(cache_and_advance: tuple[Cache, Callable[[float], None]]) -> None:
    assert cache_and_advance[0].get("missing") is None


def test_limiter_allows_up_to_limit_then_refuses(limiter: RateLimiter) -> None:
    assert limiter.consume("u", 3, limit=5, window_seconds=60).remaining == 2
    assert limiter.consume("u", 2, limit=5, window_seconds=60).allowed
    refused = limiter.consume("u", 1, limit=5, window_seconds=60)
    assert not refused.allowed
    assert refused.retry_after_seconds > 0
    assert refused.remaining == 0


def test_refused_request_consumes_nothing(limiter: RateLimiter) -> None:
    limiter.consume("u", 4, limit=5, window_seconds=60)
    assert not limiter.consume("u", 2, limit=5, window_seconds=60).allowed
    assert limiter.consume("u", 1, limit=5, window_seconds=60).allowed


def test_limiter_resets_next_window(limiter: RateLimiter, clock: FakeClock) -> None:
    limiter.consume("u", 5, limit=5, window_seconds=60)
    clock.now += 60
    assert limiter.consume("u", 5, limit=5, window_seconds=60).allowed


def test_limiter_keys_are_independent(limiter: RateLimiter) -> None:
    limiter.consume("a", 5, limit=5, window_seconds=60)
    assert limiter.consume("b", 5, limit=5, window_seconds=60).allowed
