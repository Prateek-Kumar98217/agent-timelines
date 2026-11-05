"""Web access for the local build: search through SearXNG, pages through the browser service."""

import httpx

from app.ports import BrowserBlockedError, PageText, SearchResult


class LocalWeb:
    def __init__(
        self,
        searxng_url: str,
        browser_url: str,
        transport: httpx.BaseTransport | None = None,
        search_timeout_seconds: float = 20.0,
        fetch_timeout_seconds: float = 30.0,
    ) -> None:
        self._searxng_url = searxng_url
        self._browser_url = browser_url
        self._transport = transport
        self._search_timeout = search_timeout_seconds
        self._fetch_timeout = fetch_timeout_seconds

    def search(self, query: str, limit: int) -> list[SearchResult]:
        try:
            with httpx.Client(transport=self._transport, timeout=self._search_timeout) as client:
                response = client.get(
                    f"{self._searxng_url}/search", params={"q": query, "format": "json"}
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BrowserBlockedError(f"web search is unavailable: {exc}") from exc
        results = body.get("results", [])
        if not results and body.get("unresponsive_engines"):
            raise BrowserBlockedError("no search engine answered")
        return [
            SearchResult(item.get("title", ""), item["url"], item.get("content") or "")
            for item in results[:limit]
            if item.get("url")
        ]

    def fetch(self, url: str) -> PageText:
        try:
            with httpx.Client(transport=self._transport, timeout=self._fetch_timeout) as client:
                response = client.post(f"{self._browser_url}/fetch", json={"url": url})
        except httpx.HTTPError as exc:
            raise BrowserBlockedError(f"the browser is unavailable: {exc}") from exc
        if response.status_code in (403, 422, 503):
            raise BrowserBlockedError(_detail(response))
        if response.is_error:
            raise BrowserBlockedError(f"the browser returned {response.status_code}")
        body = response.json()
        return PageText(body["url"], body["title"], body["text"])


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json()["detail"])
    except ValueError, KeyError, TypeError:
        return f"the browser returned {response.status_code}"
