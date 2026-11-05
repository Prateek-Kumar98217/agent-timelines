"""Fetches pages and search results with headless Chromium.

Pages are untrusted and run JavaScript, so each request gets a fresh context, and every network
request a page makes passes the URL policy, including each hop of a redirect. Network isolation of
the service itself is the second layer, since a DNS answer can change after a check.
"""

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error,
    Playwright,
    Route,
    async_playwright,
)
from playwright.async_api import Page as PlaywrightPage
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from browser_service.policy import UrlPolicy, public_web_only

SEARCH_URL = "https://html.duckduckgo.com/html/?q={query}"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Chrome/146 Safari/537.36 agent-timelines/0.1"
BLOCKED_RESOURCES = {"image", "media", "font"}
MAX_RESPONSE_BYTES = 5_000_000
MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_TEXT_CHARS = 20_000
CONTENT_SELECTORS = ("main", "article", "[role=main]", "body")
CHALLENGE_MARKERS = (".anomaly-modal__title", "form#challenge-form", ".challenge-form")


class UrlRefusedError(Exception):
    """The URL isn't allowed by the policy."""


class PageUnavailableError(Exception):
    """The page or search couldn't be read: a block, CAPTCHA, timeout or network failure."""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str


@dataclass(frozen=True)
class Politeness:
    """Limits on search traffic, so this tool stays a light user of the search engine."""

    min_search_interval_seconds: float = 2.0
    challenge_cooldown_seconds: float = 300.0


class BrowserService:
    def __init__(
        self,
        policy: UrlPolicy = public_web_only,
        search_url: str = SEARCH_URL,
        concurrency: int = 2,
        timeout_seconds: float = 15.0,
        politeness: Politeness | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._search_url = search_url
        self._slots = asyncio.Semaphore(concurrency)
        self._timeout_ms = timeout_seconds * 1000
        self._politeness = politeness or Politeness()
        self._clock = clock
        self._search_lock = asyncio.Lock()
        self._last_search = float("-inf")
        self._cooldown_until = float("-inf")
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self.refused_requests: list[str] = []

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]
        )

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def fetch(self, url: str) -> Page:
        problem = self._policy(url)
        if problem:
            raise UrlRefusedError(problem)
        async with self._slots:
            return await self._read_page(url)

    async def search(self, query: str, limit: int) -> list[SearchHit]:
        now = self._clock()
        if now < self._cooldown_until:
            wait = int(self._cooldown_until - now)
            raise PageUnavailableError(f"search is paused for {wait}s after a challenge")
        async with self._search_lock:
            wait_for = self._last_search + self._politeness.min_search_interval_seconds
            delay = wait_for - self._clock()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_search = self._clock()
            async with self._slots:
                return await self._read_results(query, limit)

    async def _new_context(self) -> tuple[BrowserContext, dict[str, str]]:
        if self._browser is None:
            raise RuntimeError("the browser hasn't started")
        context = await self._browser.new_context(
            user_agent=USER_AGENT,
            accept_downloads=False,
            service_workers="block",
            java_script_enabled=True,
        )
        context.set_default_timeout(self._timeout_ms)
        final_urls: dict[str, str] = {}

        async def guard(route: Route) -> None:
            await self._guard(route, final_urls)

        await context.route("**/*", guard)
        await context.route_web_socket("**/*", lambda socket: socket.close())
        return context, final_urls

    async def _guard(self, route: Route, final_urls: dict[str, str]) -> None:
        request = route.request
        if request.resource_type in BLOCKED_RESOURCES:
            await route.abort("blockedbyclient")
            return
        url = request.url
        try:
            for _hop in range(MAX_REDIRECTS + 1):
                if self._policy(url):
                    self.refused_requests.append(url)
                    await route.abort("blockedbyclient")
                    return
                response = await route.fetch(url=url, max_redirects=0, timeout=self._timeout_ms)
                location = response.headers.get("location")
                if response.status not in REDIRECT_STATUSES or not location:
                    break
                # Chromium follows a redirect it's handed without calling this guard again, which
                # would let a public page redirect into a private address. So redirects are
                # followed here, with the policy checked before every hop.
                url = urljoin(url, location)
            else:
                await route.abort("failed")
                return
            length = response.headers.get("content-length", "0")
            if length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
                await route.abort("blockedbyclient")
                return
            final_urls[request.url] = url
            await route.fulfill(response=response)
        except Error:
            await route.abort("failed")

    async def _read_page(self, url: str) -> Page:
        context, final_urls = await self._new_context()
        try:
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded")
            if response is None or response.status >= 400:
                status = response.status if response else "no response"
                raise PageUnavailableError(f"the page returned {status}")
            text = await self._main_text(page)
            return Page(url=final_urls.get(page.url, page.url), title=await page.title(), text=text)
        except PlaywrightTimeoutError as exc:
            raise PageUnavailableError("the page timed out") from exc
        except Error as exc:
            raise PageUnavailableError(f"couldn't load the page: {_first_line(exc)}") from exc
        finally:
            await context.close()

    async def _read_results(self, query: str, limit: int) -> list[SearchHit]:
        context, _final_urls = await self._new_context()
        try:
            page = await context.new_page()
            url = self._search_url.format(query=quote_plus(query))
            response = await page.goto(url, wait_until="domcontentloaded")
            status = response.status if response else 0
            challenged = status in (202, 403, 429) or await self._has_challenge(page)
            if challenged:
                self._cooldown_until = self._clock() + self._politeness.challenge_cooldown_seconds
                raise PageUnavailableError("the search engine asked for a challenge")
            if status >= 400:
                raise PageUnavailableError(f"the search engine returned {status}")
            return await _parse_results(page, limit)
        except PlaywrightTimeoutError as exc:
            raise PageUnavailableError("the search timed out") from exc
        except Error as exc:
            raise PageUnavailableError(f"couldn't load results: {_first_line(exc)}") from exc
        finally:
            await context.close()

    async def _has_challenge(self, page: PlaywrightPage) -> bool:
        for selector in CHALLENGE_MARKERS:
            if await page.locator(selector).count():
                return True
        return False

    async def _main_text(self, page: PlaywrightPage) -> str:
        for selector in CONTENT_SELECTORS:
            locator = page.locator(selector)
            if await locator.count():
                text = await locator.first.inner_text()
                if text.strip():
                    return _clean_text(text)
        return ""


async def _parse_results(page: PlaywrightPage, limit: int) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for result in await page.locator(".result:not(.result--ad)").all():
        if len(hits) >= limit:
            break
        link = result.locator("a.result__a").first
        if not await link.count():
            continue
        href = await link.get_attribute("href") or ""
        snippet = result.locator(".result__snippet").first
        hits.append(
            SearchHit(
                title=(await link.inner_text()).strip(),
                url=_real_url(href, page.url),
                snippet=(await snippet.inner_text()).strip() if await snippet.count() else "",
            )
        )
    return hits


def _real_url(href: str, base: str) -> str:
    """Result links go through a redirector, with the real address in `uddg`."""
    absolute = urljoin(base, href)
    target = parse_qs(urlsplit(absolute).query).get("uddg")
    return target[0] if target else absolute


def _clean_text(text: str) -> str:
    collapsed = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return collapsed[:MAX_TEXT_CHARS]


def _first_line(error: Exception) -> str:
    return str(error).split("\n", maxsplit=1)[0]
