"""Behaviour every cache and rate limiter must share. Later adapters join these fixtures."""

import pytest

from app.adapters.memory import MemoryCache, MemoryRateLimiter
from app.ports import Cache, RateLimiter


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def cache(clock: FakeClock) -> Cache:
    return MemoryCache(clock)


@pytest.fixture
def limiter(clock: FakeClock) -> RateLimiter:
    return MemoryRateLimiter(clock)


def test_cache_returns_value_until_ttl(cache: Cache, clock: FakeClock) -> None:
    cache.set("k", "v", ttl_seconds=10)
    assert cache.get("k") == "v"
    clock.now += 10
    assert cache.get("k") is None


def test_cache_miss_is_none(cache: Cache) -> None:
    assert cache.get("missing") is None


def test_limiter_allows_up_to_limit_then_refuses(limiter: RateLimiter) -> None:
    assert limiter.consume("u", 3, limit=5, window_seconds=60).remaining == 2
    assert limiter.consume("u", 2, limit=5, window_seconds=60).allowed
    refused = limiter.consume("u", 1, limit=5, window_seconds=60)
    assert not refused.allowed
    assert refused.retry_after_seconds > 0


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
