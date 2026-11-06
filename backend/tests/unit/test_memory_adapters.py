from uuid import uuid4

import pytest

from app.adapters.memory import (
    FakeAuthenticator,
    FixtureBrowser,
    HashEmbedder,
    InlineQueue,
    ScriptedLLM,
)
from app.ports import (
    BrowserBlockedError,
    LLMRequest,
    LLMResponse,
    Principal,
    PrincipalKind,
    ProviderRateLimitedError,
    StepMessage,
    Usage,
)


def test_inline_queue_drains_messages_added_while_draining() -> None:
    queue = InlineQueue()
    branch = uuid4()
    queue.enqueue(StepMessage(branch, 1))
    seen = []
    for message in queue.drain():
        seen.append(message.seq)
        if message.seq == 1:
            queue.enqueue(StepMessage(branch, 2))
    assert seen == [1, 2]
    assert queue.depth() == 0


def test_scripted_llm_replays_in_order_and_raises_scripted_errors() -> None:
    answer = LLMResponse(usage=Usage(1, 1), text="done")
    llm = ScriptedLLM([ProviderRateLimitedError(5), answer])
    request = LLMRequest(messages=[], tools=[])
    with pytest.raises(ProviderRateLimitedError):
        llm.complete(request)
    assert llm.complete(request) is answer
    assert len(llm.requests) == 2
    with pytest.raises(AssertionError):
        llm.complete(request)


def test_hash_embedder_is_repeatable_and_normalised() -> None:
    embedder = HashEmbedder(dimensions=384)
    first, second, other = embedder.embed(["a", "a", "b"])
    assert first == second
    assert first != other
    assert len(first) == 384
    assert sum(v * v for v in first) == pytest.approx(1.0)


def test_fixture_browser_treats_unknown_input_as_blocked() -> None:
    browser = FixtureBrowser()
    with pytest.raises(BrowserBlockedError):
        browser.search("anything", 5)
    with pytest.raises(BrowserBlockedError):
        browser.fetch("https://example.com")


def test_fake_authenticator_rejects_missing_and_unknown_tokens() -> None:
    principal = Principal(uuid4(), PrincipalKind.LOCAL)
    auth = FakeAuthenticator({"good": principal})
    assert auth.authenticate("good") == principal
    assert auth.authenticate("bad") is None
    assert auth.authenticate(None) is None
