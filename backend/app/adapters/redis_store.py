"""Cache, rate limiter and live events on Redis, used by the local build."""

import json
import math
import time
from collections.abc import AsyncGenerator, Callable
from typing import cast
from uuid import UUID

import redis
import redis.asyncio as redis_async

from app.ports import RateDecision, TreeEvent

# Adds to the window's count only if it stays within the limit, so a refusal consumes nothing.
CONSUME_SCRIPT = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
local amount = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
if used + amount > limit then
  return {0, used}
end
redis.call('INCRBY', KEYS[1], amount)
redis.call('EXPIRE', KEYS[1], ARGV[3])
return {1, used + amount}
"""


class RedisCache:
    def __init__(self, client: redis.Redis, prefix: str = "cache:") -> None:
        self._client = client
        self._prefix = prefix

    def get(self, key: str) -> str | None:
        value = self._client.get(self._prefix + key)
        return cast("str | None", value)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._client.set(self._prefix + key, value, ex=ttl_seconds)


class RedisRateLimiter:
    """Fixed-window counter, atomic across processes."""

    def __init__(
        self,
        client: redis.Redis,
        prefix: str = "limit:",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._consume = client.register_script(CONSUME_SCRIPT)
        self._client = client
        self._prefix = prefix
        self._clock = clock

    def consume(self, key: str, amount: int, limit: int, window_seconds: int) -> RateDecision:
        now = self._clock()
        window = int(now // window_seconds)
        allowed, used = self._consume(
            keys=[f"{self._prefix}{key}:{window}"], args=[amount, limit, window_seconds * 2]
        )
        if allowed:
            return RateDecision(True, limit - int(used))
        retry_after = max(1, math.ceil((window + 1) * window_seconds - now))
        return RateDecision(False, max(0, limit - int(used)), retry_after)

    def charge(self, key: str, amount: int, window_seconds: int) -> None:
        window_key = f"{self._prefix}{key}:{int(self._clock() // window_seconds)}"
        pipeline = self._client.pipeline()
        pipeline.incrby(window_key, amount)
        pipeline.expire(window_key, window_seconds * 2)
        pipeline.execute()


class RedisLiveEvents:
    """Best-effort pub/sub. Clients recover anything missed from the event log."""

    def __init__(self, url: str, prefix: str = "tree-events:") -> None:
        self._url = url
        self._prefix = prefix
        self._publisher: redis.Redis = redis.Redis.from_url(url, decode_responses=True)

    def publish(self, event: TreeEvent) -> None:
        body = json.dumps({"seq": event.seq, "type": event.type, "payload": event.payload})
        self._publisher.publish(f"{self._prefix}{event.tree_id}", body)

    async def subscribe(self, tree_id: UUID) -> AsyncGenerator[TreeEvent]:
        client = redis_async.Redis.from_url(self._url, decode_responses=True)
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(f"{self._prefix}{tree_id}")
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                body = json.loads(message["data"])
                yield TreeEvent(tree_id, body["seq"], body["type"], body["payload"])
        finally:
            await pubsub.aclose()  # type: ignore[no-untyped-call]
            await client.aclose()
