"""A deterministic LLM and tools for running the real stack with no provider or API key.

The local build uses these until real providers are configured, and load tests use them so that
results measure the platform rather than a provider's limits.
"""

import hashlib
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from app.domain.messages import estimate_tokens
from app.ports import (
    JsonDict,
    LLMRequest,
    LLMResponse,
    ProviderRateLimitedError,
    ToolCall,
    ToolContext,
    ToolOutcome,
    Usage,
)

RATE_LIMIT_MARKER = re.compile(r"\[rate-limit:(\d+)\]")


class MockLLM:
    """Calls a tool `tool_turns` times, then answers. It prefers the local knowledge base.

    A task containing `[rate-limit:N]` is refused once per process with an N-second retry.
    """

    def __init__(
        self,
        latency_seconds: float = 0.0,
        tool_turns: int = 1,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.latency_seconds = latency_seconds
        self.tool_turns = tool_turns
        self._sleep = sleep
        self._refused: set[str] = set()
        self._lock = threading.Lock()

    def complete(self, request: LLMRequest) -> LLMResponse:
        if self.latency_seconds:
            self._sleep(self.latency_seconds)
        question = next(
            (m["content"] for m in reversed(request.messages) if m["role"] == "user"), ""
        )
        self._maybe_refuse(question)
        usage = Usage(estimate_tokens(request.messages), 20)
        tool_results = sum(1 for m in request.messages if m["role"] == "tool")
        if request.tools and tool_results < self.tool_turns:
            tool = next((t for t in request.tools if t["name"] == "kb_search"), request.tools[0])
            return LLMResponse(
                usage=usage,
                tool_call=ToolCall(tool["name"], _arguments_for(tool["parameters"], question)),
            )
        return LLMResponse(usage=usage, text=f"Answer to '{question}' from {tool_results} tools.")

    def _maybe_refuse(self, question: str) -> None:
        match = RATE_LIMIT_MARKER.search(question)
        if match is None:
            return
        key = hashlib.sha256(question.encode()).hexdigest()
        with self._lock:
            if key in self._refused:
                return
            self._refused.add(key)
        raise ProviderRateLimitedError(int(match.group(1)))


def _arguments_for(schema: dict[str, Any], text: str) -> JsonDict:
    properties = schema.get("properties", {})
    return {
        name: text if properties.get(name, {}).get("type") == "string" else 1
        for name in schema.get("required", [])
    }


class EchoTools:
    """Returns a fixed result built from the arguments."""

    def __init__(
        self, latency_seconds: float = 0.0, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self.latency_seconds = latency_seconds
        self._sleep = sleep

    def execute(self, context: ToolContext, name: str, arguments: JsonDict) -> ToolOutcome:
        if self.latency_seconds:
            self._sleep(self.latency_seconds)
        return ToolOutcome(f"Mock result of {name} for {arguments}")
