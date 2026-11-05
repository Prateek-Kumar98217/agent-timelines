"""Reads web pages with headless Chromium.

Pages are untrusted and run JavaScript, so each request gets a fresh context, and every network
request a page makes passes the URL policy, including each hop of a redirect. Network isolation of
the service itself is the second layer, since a DNS answer can change after a check.
"""

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urljoin

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

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Chrome/146 Safari/537.36 agent-timelines/0.1"
BLOCKED_RESOURCES = {"image", "media", "font"}
MAX_RESPONSE_BYTES = 5_000_000
MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_TEXT_CHARS = 20_000
CONTENT_SELECTORS = ("main", "article", "[role=main]", "body")


class UrlRefusedError(Exception):
    """The URL isn't allowed by the policy."""


class PageUnavailableError(Exception):
    """The page couldn't be read: a block, timeout or network failure."""


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str


class BrowserService:
    def __init__(
        self,
        policy: UrlPolicy = public_web_only,
        concurrency: int = 2,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._policy = policy
        self._slots = asyncio.Semaphore(concurrency)
        self._timeout_ms = timeout_seconds * 1000
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

    async def _main_text(self, page: PlaywrightPage) -> str:
        for selector in CONTENT_SELECTORS:
            locator = page.locator(selector)
            if await locator.count():
                text = await locator.first.inner_text()
                if text.strip():
                    return _clean_text(text)
        return ""


def _clean_text(text: str) -> str:
    collapsed = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return collapsed[:MAX_TEXT_CHARS]


def _first_line(error: Exception) -> str:
    return str(error).split("\n", maxsplit=1)[0]
