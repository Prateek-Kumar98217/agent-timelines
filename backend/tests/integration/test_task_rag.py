"""A task's store holds the pages it fetched, and a branch reads only those on its own path."""

from uuid import UUID

from app.adapters.memory import HashEmbedder
from app.db.models import Node, NodeType
from app.db.session import SessionFactory
from app.domain.config import AGENT_TOOLS, EVAL_TOOLS, TreeConfig, tools_for
from app.domain.engine import start_task
from app.domain.forking import fork_from_edit
from app.domain.scratchpad import MAX_PAGE_CHUNKS, page_chunks, search_notes
from app.ports import LLMResponse, ToolCall, ToolContext, ToolOutcome, Usage
from tests.helpers import Harness, text_reply

CONFIG = TreeConfig(system_prompt="sys", tools=AGENT_TOOLS)


def fetch(url: str) -> LLMResponse:
    return LLMResponse(usage=Usage(5, 5), tool_call=ToolCall("page_fetch", {"url": url}))


def page(_context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    url = str(arguments["url"])
    return ToolOutcome(f"Title of {url}\n{url}\n\nThe body of {url}.")


def store(harness: Harness, branch: UUID) -> list[str]:
    hits = search_notes(harness.session_factory, HashEmbedder(), branch, "anything", limit=100)
    return sorted(hit.content for hit in hits)


def body(url: str) -> str:
    return f"From Title of {url} ({url})\nThe body of {url}."


def run(clean_db: SessionFactory, *replies: LLMResponse) -> tuple[Harness, UUID]:
    harness = Harness(clean_db, replies)
    harness.tools.handlers["page_fetch"] = page
    _, branch = start_task(harness.deps, harness.user.id, "the task", CONFIG)
    harness.drain()
    return harness, branch


def of_type(harness: Harness, branch: UUID, node_type: NodeType) -> list[Node]:
    return [node for node in harness.nodes(branch) if node.type == node_type]


def test_a_fetched_page_goes_into_the_store(clean_db: SessionFactory) -> None:
    harness, branch = run(clean_db, fetch("https://a.test"), text_reply())
    assert store(harness, branch) == [body("https://a.test")]


def test_the_store_keeps_the_page_beyond_what_the_model_reads(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [fetch("https://a.test"), text_reply()])
    long_text = " ".join(f"word{i}" for i in range(3_000))
    harness.tools.handlers["page_fetch"] = lambda _c, _a: ToolOutcome(
        f"Long\nhttps://a.test\n\n{long_text}"
    )
    config = CONFIG.model_copy(update={"max_tool_result_chars": 100})
    _, branch = start_task(harness.deps, harness.user.id, "the task", config)
    harness.drain()

    result = of_type(harness, branch, NodeType.TOOL_RESULT)[0]
    assert "truncated" in result.payload["content"]
    assert "word2999" in "".join(store(harness, branch))


def test_a_failed_fetch_adds_nothing(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [fetch("https://a.test"), text_reply()])
    harness.tools.handlers["page_fetch"] = lambda _c, _a: ToolOutcome("blocked", is_error=True)
    _, branch = start_task(harness.deps, harness.user.id, "the task", CONFIG)
    harness.drain()
    assert store(harness, branch) == []


def test_other_tools_add_nothing(clean_db: SessionFactory) -> None:
    search = LLMResponse(usage=Usage(5, 5), tool_call=ToolCall("web_search", {"query": "q"}))
    harness, branch = run(clean_db, search, text_reply())
    assert store(harness, branch) == []


def test_a_fork_after_a_fetch_inherits_it_but_not_later_fetches(clean_db: SessionFactory) -> None:
    harness, main = run(
        clean_db, fetch("https://first.test"), fetch("https://second.test"), text_reply()
    )
    second_call = of_type(harness, main, NodeType.LLM_OUTPUT)[1]

    fork = fork_from_edit(harness.deps, harness.user.id, second_call.id, {"text": "done early"})

    assert store(harness, fork) == [body("https://first.test")]
    assert store(harness, main) == [body("https://first.test"), body("https://second.test")]


def test_a_branch_cant_read_a_siblings_pages(clean_db: SessionFactory) -> None:
    harness, main = run(
        clean_db,
        fetch("https://shared.test"),
        fetch("https://main-only.test"),
        text_reply(),
        fetch("https://fork-only.test"),
        text_reply(),
    )
    call = of_type(harness, main, NodeType.TOOL_RESULT)[0]

    fork = fork_from_edit(harness.deps, harness.user.id, call.id, {"content": "Not a page"})
    harness.drain()

    assert body("https://main-only.test") not in store(harness, fork)
    assert body("https://fork-only.test") in store(harness, fork)
    assert body("https://fork-only.test") not in store(harness, main)


def test_an_edited_page_replaces_the_original_on_that_path(clean_db: SessionFactory) -> None:
    harness, main = run(clean_db, fetch("https://a.test"), text_reply(), text_reply())
    result = of_type(harness, main, NodeType.TOOL_RESULT)[0]
    edited = "Edited\nhttps://a.test\n\nA different body."

    fork = fork_from_edit(harness.deps, harness.user.id, result.id, {"content": edited})
    harness.drain()

    assert store(harness, fork) == ["From Edited (https://a.test)\nA different body."]
    assert store(harness, main) == [body("https://a.test")]


def test_a_refetch_with_edited_arguments_excludes_the_original_page(
    clean_db: SessionFactory,
) -> None:
    harness, main = run(clean_db, fetch("https://a.test"), text_reply(), text_reply())
    call = of_type(harness, main, NodeType.TOOL_CALL)[0]

    fork = fork_from_edit(
        harness.deps, harness.user.id, call.id, {"arguments": {"url": "https://b.test"}}
    )
    harness.drain()

    assert store(harness, fork) == [body("https://b.test")]


def test_one_page_can_only_add_so_much() -> None:
    huge = "\n\n".join("x" * 700 for _ in range(MAX_PAGE_CHUNKS * 2))
    assert len(page_chunks(f"T\nhttps://a.test\n\n{huge}")) == MAX_PAGE_CHUNKS


def test_real_tasks_dont_get_the_evaluation_corpus() -> None:
    names = {tool.name for tool in tools_for("ollama")}
    assert "kb_search" not in names
    assert {"page_fetch", "task_search"} <= names
    assert tools_for("mock") == EVAL_TOOLS
