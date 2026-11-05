import json

import httpx
import pytest

from app.adapters.web import LocalWeb
from app.ports import BrowserBlockedError


def web(handler) -> LocalWeb:  # type: ignore[no-untyped-def]
    return LocalWeb("http://search", "http://browser", transport=httpx.MockTransport(handler))


def test_search_maps_results_and_applies_the_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "search"
        assert request.url.params["q"] == "everest"
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "A", "url": "https://a.test", "content": "one"},
                    {"title": "B", "url": "https://b.test"},
                    {"title": "C", "url": "https://c.test", "content": "three"},
                ]
            },
        )

    hits = web(handler).search("everest", 2)
    assert [(hit.title, hit.url, hit.snippet) for hit in hits] == [
        ("A", "https://a.test", "one"),
        ("B", "https://b.test", ""),
    ]


def test_no_results_is_an_answer_unless_every_engine_failed() -> None:
    assert web(lambda _r: httpx.Response(200, json={"results": []})).search("q", 5) == []
    failed = {"results": [], "unresponsive_engines": [["brave", "too many requests"]]}
    with pytest.raises(BrowserBlockedError, match="no search engine"):
        web(lambda _r: httpx.Response(200, json=failed)).search("q", 5)


@pytest.mark.parametrize(
    "response", [httpx.Response(500), httpx.Response(200, content=b"not json")]
)
def test_search_failures_are_blocked_errors(response: httpx.Response) -> None:
    with pytest.raises(BrowserBlockedError):
        web(lambda _r: response).search("q", 5)


def test_fetch_returns_the_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "browser"
        assert json.loads(request.content) == {"url": "https://a.test"}
        return httpx.Response(200, json={"url": "https://a.test/", "title": "T", "text": "body"})

    page = web(handler).fetch("https://a.test")
    assert (page.url, page.title, page.text) == ("https://a.test/", "T", "body")


@pytest.mark.parametrize("status", [403, 503])
def test_refused_and_unavailable_pages_carry_the_reason(status: int) -> None:
    client = web(lambda _r: httpx.Response(status, json={"detail": "the reason"}))
    with pytest.raises(BrowserBlockedError, match="the reason"):
        client.fetch("https://a.test")


def test_connection_failures_are_blocked_errors() -> None:
    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(BrowserBlockedError, match="unavailable"):
        web(down).fetch("https://a.test")
    with pytest.raises(BrowserBlockedError, match="unavailable"):
        web(down).search("q", 5)
