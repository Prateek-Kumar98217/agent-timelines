"""Real Chromium against real local servers. A test policy treats one port as "the public web"."""

import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from browser_service.browser import BrowserService, PageUnavailableError, UrlRefusedError
from browser_service.main import create_app

ARTICLE = "<html><head><title>T</title></head><body><nav>menu</nav><main>Main text</main></body>"


class Site(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.paths: list[str] = []
        self.redirect_to = ""

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(BaseHTTPRequestHandler):
    server: Site

    def log_message(self, *_args: object) -> None:
        return None

    def _send(self, status: int, body: str, **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self) -> None:
        self.server.paths.append(self.path)
        path = urlsplit(self.path).path
        if path == "/article":
            self._send(200, ARTICLE)
        elif path == "/redirect":
            self._send(302, "", Location=self.server.redirect_to)
        elif path == "/redirect-loop":
            self._send(302, "", Location="/redirect-loop")
        elif path == "/subresource-redirect":
            self._send(200, "<main>page</main><script src='/redirect'></script>")
        elif path == "/subresource":
            target = self.server.redirect_to
            self._send(200, f"<main>page</main><script src='{target}'></script>")
        elif path == "/fetch-from-js":
            target = self.server.redirect_to
            self._send(200, f"<main>js</main><script>fetch('{target}')</script>")
        elif path == "/missing":
            self._send(404, "nope")
        elif path == "/huge":
            self._send(200, "x", Content_Length="50000000")
        else:
            self._send(200, "<main>other</main>")


def start_site() -> Site:
    site = Site()
    threading.Thread(target=site.serve_forever, daemon=True).start()
    return site


@pytest.fixture
def public() -> Iterator[Site]:
    site = start_site()
    yield site
    site.shutdown()


@pytest.fixture
def internal() -> Iterator[Site]:
    site = start_site()
    yield site
    site.shutdown()


def only(site: Site):  # type: ignore[no-untyped-def]
    prefix = site.base + "/"
    return lambda url: None if url.startswith(prefix) else "not the public site"


@pytest.fixture
async def browser(public: Site) -> AsyncIterator[BrowserService]:
    service = BrowserService(policy=only(public))
    await service.start()
    yield service
    await service.stop()


async def test_fetch_prefers_main_content(browser: BrowserService, public: Site) -> None:
    page = await browser.fetch(public.base + "/article")
    assert page.title == "T"
    assert page.text == "Main text"


async def test_direct_fetch_of_a_refused_url_is_refused(
    browser: BrowserService, internal: Site
) -> None:
    with pytest.raises(UrlRefusedError):
        await browser.fetch(internal.base + "/secret")
    assert internal.paths == []


async def test_redirect_into_an_internal_address_never_reaches_it(
    browser: BrowserService, public: Site, internal: Site
) -> None:
    public.redirect_to = internal.base + "/secret"
    with pytest.raises(PageUnavailableError):
        await browser.fetch(public.base + "/redirect")
    assert internal.paths == []
    assert browser.refused_requests == [internal.base + "/secret"]


async def test_allowed_redirects_still_work(browser: BrowserService, public: Site) -> None:
    public.redirect_to = public.base + "/article"
    page = await browser.fetch(public.base + "/redirect")
    assert page.text == "Main text"
    assert page.url == public.base + "/article"


async def test_page_subresources_cant_reach_internal_addresses(
    browser: BrowserService, public: Site, internal: Site
) -> None:
    public.redirect_to = internal.base + "/secret.js"
    await browser.fetch(public.base + "/subresource")
    assert internal.paths == []


async def test_redirecting_subresources_cant_reach_internal_addresses(
    browser: BrowserService, public: Site, internal: Site
) -> None:
    public.redirect_to = internal.base + "/secret.js"
    await browser.fetch(public.base + "/subresource-redirect")
    assert internal.paths == []
    assert browser.refused_requests == [internal.base + "/secret.js"]


async def test_redirect_loops_end_in_an_error(browser: BrowserService, public: Site) -> None:
    with pytest.raises(PageUnavailableError):
        await browser.fetch(public.base + "/redirect-loop")


async def test_page_javascript_cant_reach_internal_addresses(
    browser: BrowserService, public: Site, internal: Site
) -> None:
    public.redirect_to = internal.base + "/secret"
    await browser.fetch(public.base + "/fetch-from-js")
    assert internal.paths == []


async def test_missing_pages_and_oversized_responses_are_errors(
    browser: BrowserService, public: Site
) -> None:
    with pytest.raises(PageUnavailableError, match="404"):
        await browser.fetch(public.base + "/missing")
    with pytest.raises(PageUnavailableError):
        await browser.fetch(public.base + "/huge")


def test_http_interface_maps_errors(public: Site, internal: Site) -> None:
    service = BrowserService(policy=only(public))
    with TestClient(create_app(service)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        page = client.post("/fetch", json={"url": public.base + "/article"})
        assert page.status_code == 200
        assert page.json()["text"] == "Main text"
        assert client.post("/fetch", json={"url": internal.base + "/x"}).status_code == 403
        assert client.post("/fetch", json={"url": public.base + "/missing"}).status_code == 503
        assert client.post("/fetch", json={"url": ""}).status_code == 422
