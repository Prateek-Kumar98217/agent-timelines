from uuid import uuid4

import pytest

from app.adapters.mock import EchoTools, MockLLM
from app.ports import LLMRequest, ProviderRateLimitedError, ToolContext

TOOL = {
    "name": "search",
    "description": "d",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query", "limit"],
    },
}


def request(text: str = "hi", tool_results: int = 0, force_final: bool = False) -> LLMRequest:
    messages = [{"role": "user", "content": text}]
    messages += [{"role": "tool", "content": "r"}] * tool_results
    return LLMRequest(messages=messages, tools=[] if force_final else [TOOL])


def test_calls_a_tool_with_valid_arguments_then_answers() -> None:
    llm = MockLLM(tool_turns=2)
    first = llm.complete(request())
    assert first.tool_call is not None
    assert first.tool_call.arguments == {"query": "hi", "limit": 1}
    assert llm.complete(request(tool_results=1)).tool_call is not None
    final = llm.complete(request(tool_results=2))
    assert final.tool_call is None
    assert final.text


def test_answers_when_no_tools_are_offered() -> None:
    assert MockLLM().complete(request(force_final=True)).text


def test_latency_is_applied() -> None:
    slept: list[float] = []
    MockLLM(latency_seconds=0.5, sleep=slept.append).complete(request())
    assert slept == [0.5]


def test_rate_limit_marker_refuses_once_per_task() -> None:
    llm = MockLLM()
    with pytest.raises(ProviderRateLimitedError) as error:
        llm.complete(request("task [rate-limit:7]"))
    assert error.value.retry_after_seconds == 7
    assert llm.complete(request("task [rate-limit:7]")) is not None


def test_echo_tools_reflect_arguments() -> None:
    context = ToolContext(uuid4(), uuid4())
    outcome = EchoTools().execute(context, "search", {"query": "x"})
    assert "search" in outcome.content
    assert "x" in outcome.content
