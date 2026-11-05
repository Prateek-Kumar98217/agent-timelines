"""The adapter against a real local HTTP server that speaks the OpenAI API, including its errors."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

import pytest

from app.adapters.llm import HOSTED_PROVIDERS, OpenAICompatibleLLM, ProviderInfo, RoutedLLM
from app.adapters.mock import MockLLM
from app.ports import (
    LLMRequest,
    ProviderRateLimitedError,
    ProviderRefusedError,
    ProviderTransientError,
)

TOOL = {
    "name": "search",
    "description": "Search",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
}
SECRET_KEY = "gsk-supersecret-key-1234"


def completion(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "c",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def tool_call(name: str, arguments: str, call_id: str = "c1") -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


class Stub(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.status = 200
        self.reply: dict[str, Any] = completion({"role": "assistant", "content": "hello"})
        self.headers_out: dict[str, str] = {}
        self.requests: list[tuple[dict[str, Any], dict[str, str]]] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/v1"


class Handler(BaseHTTPRequestHandler):
    server: Stub

    def log_message(self, *_args: object) -> None:
        return None

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self.server.requests.append((body, {k.lower(): v for k, v in self.headers.items()}))
        self.send_response(self.server.status)
        for key, value in self.server.headers_out.items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.server.reply).encode())


@pytest.fixture
def stub() -> Iterator[Stub]:
    server = Stub()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def request(**overrides: Any) -> LLMRequest:
    values: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "question"},
        ],
        "tools": [TOOL],
    }
    return LLMRequest(**(values | overrides))


def llm(stub: Stub) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(stub.url, SECRET_KEY, "my-model")


def test_text_reply_and_usage(stub: Stub) -> None:
    response = llm(stub).complete(request())
    assert response.text == "hello"
    assert response.tool_call is None
    assert (response.usage.input_tokens, response.usage.output_tokens) == (11, 7)


def test_request_carries_model_tools_messages_and_key(stub: Stub) -> None:
    llm(stub).complete(request(temperature=0.3))
    body, headers = stub.requests[0]
    assert body["model"] == "my-model"
    assert body["temperature"] == 0.3
    assert body["tools"][0]["function"]["name"] == "search"
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert headers["authorization"] == f"Bearer {SECRET_KEY}"


def test_a_full_tool_history_is_sent_in_openai_form(stub: Stub) -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": "thinking",
            "tool_call": {"id": "abc", "name": "search", "arguments": {"query": "x"}},
        },
        {
            "role": "tool",
            "tool_call_id": "abc",
            "name": "search",
            "content": "found",
            "is_error": False,
        },
    ]
    llm(stub).complete(request(messages=messages))
    sent = stub.requests[0][0]["messages"]
    assert sent[2]["tool_calls"][0]["id"] == "abc"
    assert json.loads(sent[2]["tool_calls"][0]["function"]["arguments"]) == {"query": "x"}
    assert sent[3] == {"role": "tool", "tool_call_id": "abc", "content": "found"}


def test_force_final_sends_no_tools_and_says_so(stub: Stub) -> None:
    llm(stub).complete(request(tools=[], force_final=True))
    body = stub.requests[0][0]
    assert "tools" not in body
    assert "final answer" in body["messages"][0]["content"]


def test_tool_call_reply(stub: Stub) -> None:
    stub.reply = completion(
        {
            "role": "assistant",
            "content": "let me look",
            "tool_calls": [tool_call("search", '{"query": "x"}')],
        }
    )
    response = llm(stub).complete(request())
    assert response.text == "let me look"
    assert response.tool_call is not None
    assert (response.tool_call.name, response.tool_call.arguments) == ("search", {"query": "x"})


def test_only_one_tool_call_runs_and_the_rest_are_counted(stub: Stub) -> None:
    stub.reply = completion(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                tool_call("search", '{"query": "a"}', "c1"),
                tool_call("search", '{"query": "b"}', "c2"),
                tool_call("search", '{"query": "c"}', "c3"),
            ],
        }
    )
    response = llm(stub).complete(request())
    assert response.tool_call is not None
    assert response.tool_call.arguments == {"query": "a"}
    assert response.dropped_tool_calls == 2


def test_malformed_tool_arguments_become_an_empty_call_for_validation(stub: Stub) -> None:
    stub.reply = completion(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [tool_call("search", '{"query": broken')],
        }
    )
    response = llm(stub).complete(request())
    assert response.tool_call is not None
    assert (response.tool_call.name, response.tool_call.arguments) == ("search", {})


def test_thinking_blocks_are_removed(stub: Stub) -> None:
    stub.reply = completion(
        {"role": "assistant", "content": "<think>secret\nreasoning</think>\nThe answer."}
    )
    assert llm(stub).complete(request()).text == "The answer."


def test_only_thinking_counts_as_no_text(stub: Stub) -> None:
    stub.reply = completion({"role": "assistant", "content": "<think>hmm</think>"})
    assert llm(stub).complete(request()).text is None


@pytest.mark.parametrize(("header", "expected"), [("17", 17), ("2.5", 2), (None, 30), ("soon", 30)])
def test_rate_limits_carry_the_retry_delay(stub: Stub, header: str | None, expected: int) -> None:
    stub.status = 429
    stub.reply = {"error": {"message": "slow down"}}
    if header:
        stub.headers_out["Retry-After"] = header
    with pytest.raises(ProviderRateLimitedError) as error:
        llm(stub).complete(request())
    assert error.value.retry_after_seconds == expected
    assert len(stub.requests) == 1


@pytest.mark.parametrize("status", [500, 502, 503])
def test_server_errors_are_transient_with_no_client_retry(stub: Stub, status: int) -> None:
    stub.status = status
    stub.reply = {"error": {"message": "broken"}}
    with pytest.raises(ProviderTransientError):
        llm(stub).complete(request())
    assert len(stub.requests) == 1


def test_connection_failure_is_transient() -> None:
    with pytest.raises(ProviderTransientError):
        OpenAICompatibleLLM("http://127.0.0.1:1/v1", "k", "m").complete(request())


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "rejected the API key"),
        (403, "rejected the API key"),
        (404, "isn't available"),
        (400, "refused this request"),
    ],
)
def test_permanent_errors_never_echo_provider_text(stub: Stub, status: int, expected: str) -> None:
    stub.status = status
    stub.reply = {"error": {"message": f"Incorrect API key provided: {SECRET_KEY}"}}
    with pytest.raises(ProviderRefusedError, match=expected) as error:
        llm(stub).complete(request())
    assert SECRET_KEY not in str(error.value)
    assert error.value.__cause__ is None


def test_router_reads_the_owner_key_at_call_time(
    stub: Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(HOSTED_PROVIDERS, "groq", ProviderInfo("groq", stub.url, "default-model"))
    owner = uuid4()
    seen: list[tuple[object, str]] = []

    def lookup(user: object, provider: str) -> str | None:
        seen.append((user, provider))
        return "user-key"

    response = RoutedLLM(lookup, None).complete(request(provider="groq", owner_id=owner))

    assert response.text == "hello"
    assert seen == [(owner, "groq")]
    body, headers = stub.requests[0]
    assert headers["authorization"] == "Bearer user-key"
    assert body["model"] == "default-model"


def test_router_refuses_when_there_is_no_key() -> None:
    router = RoutedLLM(lambda _u, _p: None, None)
    with pytest.raises(ProviderRefusedError, match="no API key"):
        router.complete(request(provider="groq"))


def test_router_uses_ollama_without_a_key(stub: Stub) -> None:
    router = RoutedLLM(lambda _u, _p: None, stub.url.removesuffix("/v1"))
    router.complete(request(provider="ollama", model="qwen3:4b"))
    assert stub.requests[0][0]["model"] == "qwen3:4b"


@pytest.mark.parametrize("provider", ["ollama", "mock", "unknown", None])
def test_router_refuses_providers_that_are_not_configured(provider: str | None) -> None:
    with pytest.raises(ProviderRefusedError, match="isn't available"):
        RoutedLLM(lambda _u, _p: None, None).complete(request(provider=provider))


def test_router_uses_mock_only_when_given_one() -> None:
    router = RoutedLLM(lambda _u, _p: None, None, mock=MockLLM(tool_turns=0))
    assert router.complete(request(provider="mock")).text
