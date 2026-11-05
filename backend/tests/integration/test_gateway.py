"""The gateway's tools and caching, then the same over real HTTP through an MCP client."""

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import uvicorn
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.adapters.mcp_tools import McpToolExecutor
from app.adapters.memory import FixtureBrowser, HashEmbedder, MemoryCache
from app.adapters.telemetry import OtelTelemetry
from app.db.models import Branch, BranchStatus, Node, NodeType, Tree, User
from app.db.session import SessionFactory
from app.domain.config import AGENT_TOOLS
from app.gateway.server import build_app, build_server
from app.gateway.tokens import InvalidToolTokenError, mint_tool_token, read_tool_token
from app.kb.ingest import ingest
from app.ports import PageText, SearchResult, ToolContext
from app.tools.gateway import GatewayTools, UnknownToolError

SECRET = "test-secret-with-enough-length-for-hs256"


class CountingBrowser(FixtureBrowser):
    def __init__(self) -> None:
        super().__init__(
            results={"q": [SearchResult("Title", "https://a.test", "Snippet")]},
            pages={"https://a.test": PageText("https://a.test", "Page", "Body text")},
        )
        self.searches = 0

    def search(self, query: str, limit: int) -> list[SearchResult]:
        self.searches += 1
        return super().search(query, limit)


@pytest.fixture
def browser() -> CountingBrowser:
    return CountingBrowser()


@pytest.fixture
def tools(clean_db: SessionFactory, browser: CountingBrowser) -> GatewayTools:
    return GatewayTools(clean_db, MemoryCache(), browser, HashEmbedder())


def make_branch(session_factory: SessionFactory) -> tuple[UUID, UUID]:
    with session_factory() as session:
        user = User(id=uuid4(), kind="local")
        session.add(user)
        session.flush()
        tree = Tree(id=uuid4(), owner_id=user.id)
        session.add(tree)
        session.flush()
        branch = Branch(
            id=uuid4(), tree_id=tree.id, owner_id=user.id, status=BranchStatus.RUNNING.value
        )
        session.add(branch)
        session.flush()
        node = Node(
            id=uuid4(),
            tree_id=tree.id,
            branch_id=branch.id,
            type=NodeType.ROOT.value,
            payload={},
            seq=0,
            turn_index=0,
        )
        session.add(node)
        session.commit()
        return branch.id, node.id


def context(bypass: bool = False) -> ToolContext:
    return ToolContext(uuid4(), uuid4(), bypass)


def test_web_search_formats_results(tools: GatewayTools) -> None:
    outcome = tools.execute(context(), "web_search", {"query": "q"})
    assert not outcome.is_error
    assert "Title\nhttps://a.test\nSnippet" in outcome.content


def test_page_fetch_formats_the_page(tools: GatewayTools) -> None:
    outcome = tools.execute(context(), "page_fetch", {"url": "https://a.test"})
    assert outcome.content == "Page\nhttps://a.test\n\nBody text"


def test_repeated_searches_use_the_cache(tools: GatewayTools, browser: CountingBrowser) -> None:
    first = tools.execute(context(), "web_search", {"query": "q"})
    second = tools.execute(context(), "web_search", {"query": "q"})
    assert first == second
    assert browser.searches == 1
    assert tools.cache_counts == {"web_search:miss": 1, "web_search:hit": 1}


def test_an_edited_call_runs_live_and_refreshes_the_cache(
    tools: GatewayTools, browser: CountingBrowser
) -> None:
    tools.execute(context(), "web_search", {"query": "q"})
    browser.results["q"] = [SearchResult("New", "https://new.test", "")]

    live = tools.execute(context(bypass=True), "web_search", {"query": "q"})

    assert "New" in live.content
    assert browser.searches == 2
    assert "New" in tools.execute(context(), "web_search", {"query": "q"}).content
    assert browser.searches == 2


def test_failures_are_errors_and_are_not_cached(
    tools: GatewayTools, browser: CountingBrowser
) -> None:
    outcome = tools.execute(context(), "web_search", {"query": "blocked"})
    assert outcome.is_error
    assert "no fixture" in outcome.content
    browser.results["blocked"] = [SearchResult("Now works", "https://b.test", "")]
    assert "Now works" in tools.execute(context(), "web_search", {"query": "blocked"}).content


def test_calculator_runs_and_reports_errors(tools: GatewayTools) -> None:
    assert tools.execute(context(), "calculator", {"expression": "2 + 2"}).content == "4"
    bad = tools.execute(context(), "calculator", {"expression": "__import__('os')"})
    assert bad.is_error


def test_kb_search_cites_sources(tools: GatewayTools, clean_db: SessionFactory) -> None:
    ingest(
        clean_db,
        HashEmbedder(),
        [{"topic": "t", "title": "Mars", "url": "https://x.test/mars", "text": "Mars is red."}],
    )
    outcome = tools.execute(context(), "kb_search", {"query": "Mars red"})
    assert "Mars: Mars is red." in outcome.content
    assert "Source: https://x.test/mars" in outcome.content


def test_scratchpad_uses_the_context_branch_and_skips_the_cache(
    tools: GatewayTools, clean_db: SessionFactory
) -> None:
    one, one_node = make_branch(clean_db)
    two, _ = make_branch(clean_db)
    search = {"query": "note"}
    in_one, in_two = ToolContext(one, one_node), ToolContext(two, one_node)
    assert "no notes" in tools.execute(in_one, "scratchpad_search", search).content

    tools.execute(in_one, "scratchpad_write", {"text": "my note"})

    assert tools.execute(in_one, "scratchpad_search", search).content == "my note"
    assert "no notes" in tools.execute(in_two, "scratchpad_search", search).content


def test_unknown_tools_are_refused(tools: GatewayTools) -> None:
    with pytest.raises(UnknownToolError):
        tools.execute(context(), "rm_rf", {})


def test_tokens_round_trip_and_reject_tampering() -> None:
    original = ToolContext(uuid4(), uuid4(), True)
    assert read_tool_token(mint_tool_token(original, SECRET), SECRET) == original
    with pytest.raises(InvalidToolTokenError):
        read_tool_token(mint_tool_token(original, SECRET), "another-secret-of-sufficient-length")
    with pytest.raises(InvalidToolTokenError):
        read_tool_token("not-a-token", SECRET)
    expired = mint_tool_token(original, SECRET, clock=lambda: time.time() - 1000)
    with pytest.raises(InvalidToolTokenError):
        read_tool_token(expired, SECRET)


@pytest.fixture
def gateway_url(tools: GatewayTools) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(build_app(tools, SECRET), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=10)


def test_tool_call_over_real_http(gateway_url: str, tools: GatewayTools) -> None:
    outcome = McpToolExecutor(gateway_url, SECRET).execute(
        context(), "calculator", {"expression": "6 * 7"}
    )
    assert outcome.content == "42"
    assert not outcome.is_error


def test_tool_errors_arrive_as_errors_over_http(gateway_url: str) -> None:
    outcome = McpToolExecutor(gateway_url, SECRET).execute(
        context(), "calculator", {"expression": "1 / 0"}
    )
    assert outcome.is_error
    assert "couldn't evaluate" in outcome.content


def test_a_wrong_secret_runs_nothing(gateway_url: str, browser: CountingBrowser) -> None:
    outcome = McpToolExecutor(gateway_url, "wrong-secret-of-sufficient-length-here").execute(
        context(), "web_search", {"query": "q"}
    )
    assert outcome.is_error
    assert "authorized" in outcome.content
    assert browser.searches == 0


def test_the_branch_comes_from_the_token_over_http(
    gateway_url: str, clean_db: SessionFactory
) -> None:
    branch, node = make_branch(clean_db)
    other, other_node = make_branch(clean_db)
    executor = McpToolExecutor(gateway_url, SECRET)
    executor.execute(ToolContext(branch, node), "scratchpad_write", {"text": "secret note"})
    arguments: dict[str, Any] = {"query": "note"}
    mine = executor.execute(ToolContext(branch, node), "scratchpad_search", arguments)
    theirs = executor.execute(ToolContext(other, other_node), "scratchpad_search", arguments)
    assert "secret note" in mine.content
    assert "no notes" in theirs.content


def test_extra_model_supplied_arguments_cant_choose_a_branch(
    gateway_url: str, clean_db: SessionFactory
) -> None:
    branch, node = make_branch(clean_db)
    other, other_node = make_branch(clean_db)
    executor = McpToolExecutor(gateway_url, SECRET)
    executor.execute(ToolContext(branch, node), "scratchpad_write", {"text": "secret note"})
    outcome = executor.execute(
        ToolContext(other, other_node),
        "scratchpad_search",
        {"query": "note", "branch_id": str(branch)},
    )
    assert "secret note" not in outcome.content


def test_every_declared_agent_tool_exists_on_the_server(tools: GatewayTools) -> None:
    async def names() -> set[str]:
        listed = await build_server(tools, SECRET).list_tools()
        return {tool.name for tool in listed}

    assert asyncio.run(names()) == {tool.name for tool in AGENT_TOOLS}


def test_a_trace_crosses_the_gateway_over_real_http(clean_db: SessionFactory) -> None:
    spans = InMemorySpanExporter()
    client_side = OtelTelemetry(
        "worker", span_exporter=spans, metric_reader=InMemoryMetricReader(), export_logs=False
    )
    gateway_side = OtelTelemetry(
        "gateway", span_exporter=spans, metric_reader=InMemoryMetricReader(), export_logs=False
    )
    tools = GatewayTools(
        clean_db, MemoryCache(), CountingBrowser(), HashEmbedder(), telemetry=gateway_side
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(build_app(tools, SECRET), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    executor = McpToolExecutor(f"http://127.0.0.1:{port}/mcp", SECRET, telemetry=client_side)
    try:
        with client_side.span("tool.call"):
            executor.execute(context(), "calculator", {"expression": "1 + 1"})
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    client_side.flush()
    gateway_side.flush()
    by_name = {span.name: span for span in spans.get_finished_spans()}
    assert by_name["gateway.tool"].context.trace_id == by_name["tool.call"].context.trace_id
    assert by_name["gateway.tool"].resource.attributes["service.name"] == "gateway"
    assert SECRET not in by_name["gateway.tool"].to_json()
