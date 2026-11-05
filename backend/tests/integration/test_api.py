import asyncio
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.memory import (
    FakeAuthenticator,
    FixtureBrowser,
    HashEmbedder,
    MemoryCache,
    MemoryRateLimiter,
    NoopTelemetry,
)
from app.adapters.mock import MockLLM
from app.api.main import create_app
from app.api.stream import event_stream
from app.bootstrap import Runtime
from app.db.session import SessionFactory
from app.domain.engine import start_task
from app.factory import Adapters
from app.ports import Principal, PrincipalKind, TreeEvent
from app.settings import Profile, Settings
from tests.helpers import CONFIG, Harness


class Api:
    def __init__(self, session_factory: SessionFactory) -> None:
        self.harness = Harness(session_factory, MockLLM(tool_turns=1))
        auth = FakeAuthenticator({"token": Principal(self.harness.user.id, PrincipalKind.USER)})
        harness = self.harness
        adapters = Adapters(
            queue=harness.queue,
            llm=harness.llm,
            tools=harness.tools,
            cache=MemoryCache(),
            rate_limiter=MemoryRateLimiter(),
            auth=auth,
            live_events=harness.events,
            browser=FixtureBrowser(),
            embedder=HashEmbedder(),
            telemetry=NoopTelemetry(),
        )
        runtime = Runtime(Settings(profile=Profile.TEST), adapters, session_factory, harness.deps)
        self.client = TestClient(create_app(runtime), headers={"Authorization": "Bearer token"})

    def start(self, task: str = "the task") -> tuple[str, str]:
        response = self.client.post("/api/trees", json={"task": task})
        assert response.status_code == 201, response.text
        body = response.json()
        return body["tree_id"], body["branch_id"]

    def finished_tree(self, task: str = "the task") -> dict[str, object]:
        tree_id, _ = self.start(task)
        self.harness.drain()
        return self.client.get(f"/api/trees/{tree_id}").json()  # type: ignore[no-any-return]


@pytest.fixture
def api(clean_db: SessionFactory) -> Iterator[Api]:
    yield Api(clean_db)


def node_of(tree: dict[str, object], node_type: str) -> dict[str, object]:
    nodes = tree["nodes"]
    assert isinstance(nodes, list)
    return next(node for node in nodes if node["type"] == node_type)


def test_requests_without_a_token_are_refused(clean_db: SessionFactory) -> None:
    api = Api(clean_db)
    api.client.headers.pop("Authorization")
    assert api.client.post("/api/trees", json={"task": "t"}).status_code == 401
    assert api.client.get("/api/trees").status_code == 401


def test_health_needs_no_token(clean_db: SessionFactory) -> None:
    api = Api(clean_db)
    api.client.headers.pop("Authorization")
    assert api.client.get("/healthz").json() == {"status": "ok"}


def test_create_run_and_read_a_tree(api: Api) -> None:
    tree_id, branch_id = api.start()
    tree = api.client.get(f"/api/trees/{tree_id}").json()
    assert tree["branches"][0]["status"] == "queued"
    assert [node["type"] for node in tree["nodes"]] == ["root", "user_msg"]

    api.harness.drain()

    tree = api.client.get(f"/api/trees/{tree_id}").json()
    assert tree["branches"][0]["id"] == branch_id
    assert tree["branches"][0]["status"] == "completed"
    assert [node["type"] for node in tree["nodes"]] == [
        "root",
        "user_msg",
        "llm_output",
        "tool_call",
        "tool_result",
        "llm_output",
    ]
    assert tree["last_event_seq"] > 0


def test_new_trees_offer_the_agent_tools(api: Api) -> None:
    tree = api.finished_tree()
    root = node_of(tree, "root")
    payload = root["payload"]
    assert isinstance(payload, dict)
    names = [tool["name"] for tool in payload["config"]["tools"]]
    assert names == [
        "web_search",
        "page_fetch",
        "kb_search",
        "calculator",
        "scratchpad_write",
        "scratchpad_search",
    ]


@pytest.mark.parametrize("task", ["", "   ", "x" * 2001])
def test_bad_tasks_are_rejected(api: Api, task: str) -> None:
    assert api.client.post("/api/trees", json={"task": task}).status_code == 422


def test_list_shows_the_user_trees_newest_first(api: Api) -> None:
    api.start("first")
    api.start("second")
    listed = api.client.get("/api/trees").json()
    assert [tree["task"] for tree in listed] == ["second", "first"]
    assert listed[0]["branches"] == 1


def test_fork_creates_a_branch_and_it_runs(api: Api) -> None:
    tree = api.finished_tree()
    node = node_of(tree, "tool_result")
    response = api.client.post(f"/api/nodes/{node['id']}/fork", json={"edit": {"content": "new"}})
    assert response.status_code == 201
    branch = response.json()
    assert branch["status"] == "queued"
    assert branch["fork_node_id"] == node["id"]
    api.harness.drain()
    updated = api.client.get(f"/api/trees/{tree['id']}").json()
    statuses = {item["id"]: item["status"] for item in updated["branches"]}
    assert statuses[branch["id"]] == "completed"


@pytest.mark.parametrize(
    ("node_type", "edit", "status"),
    [
        ("root", {"text": "x"}, 409),
        ("user_msg", {"text": "the task"}, 422),
        ("user_msg", {"text": ""}, 422),
        ("tool_call", {"arguments": {"wrong": 1}}, 422),
    ],
)
def test_fork_errors_map_to_status_codes(
    api: Api, node_type: str, edit: dict[str, object], status: int
) -> None:
    node = node_of(api.finished_tree(), node_type)
    response = api.client.post(f"/api/nodes/{node['id']}/fork", json={"edit": edit})
    assert response.status_code == status, response.text


def test_fork_of_unknown_node_is_404(api: Api) -> None:
    response = api.client.post(f"/api/nodes/{uuid4()}/fork", json={"edit": {"text": "x"}})
    assert response.status_code == 404


def test_cancel_stops_a_running_branch_and_refuses_a_finished_one(api: Api) -> None:
    _, branch_id = api.start()
    response = api.client.post(f"/api/branches/{branch_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert api.client.post(f"/api/branches/{branch_id}/cancel").status_code == 409
    assert api.client.post(f"/api/branches/{uuid4()}/cancel").status_code == 404
    api.harness.drain()


def test_compare_finds_where_branches_split(api: Api) -> None:
    tree = api.finished_tree()
    main = tree["branches"][0]["id"]  # type: ignore[index]
    node = node_of(tree, "tool_result")
    fork = api.client.post(
        f"/api/nodes/{node['id']}/fork", json={"edit": {"content": "edited"}}
    ).json()
    api.harness.drain()

    comparison = api.client.get("/api/compare", params={"a": main, "b": fork["id"]}).json()

    tool_call = node_of(tree, "tool_call")
    assert comparison["last_common_node_id"] == tool_call["id"]
    assert comparison["shared_nodes"] == 4
    assert comparison["a"]["final_text"]
    assert comparison["tool_call_overlap"] == 1.0
    assert comparison["same_final_text"] is True


def test_compare_measures_different_tool_calls(api: Api) -> None:
    tree = api.finished_tree()
    main = tree["branches"][0]["id"]  # type: ignore[index]
    node = node_of(tree, "tool_call")
    payload = node["payload"]
    assert isinstance(payload, dict)
    fork = api.client.post(
        f"/api/nodes/{node['id']}/fork", json={"edit": {"arguments": {"query": "different"}}}
    ).json()
    api.harness.drain()
    comparison = api.client.get("/api/compare", params={"a": main, "b": fork["id"]}).json()
    assert comparison["tool_call_overlap"] == 0.0
    assert comparison["shared_nodes"] == 3


def test_compare_across_trees_is_refused(api: Api) -> None:
    _, one = api.start("one")
    _, two = api.start("two")
    assert api.client.get("/api/compare", params={"a": one, "b": two}).status_code == 404


def test_events_can_be_read_after_a_position(api: Api) -> None:
    tree_id, _ = api.start()
    api.harness.drain()
    events = api.client.get(f"/api/trees/{tree_id}/events").json()
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    later = api.client.get(f"/api/trees/{tree_id}/events", params={"after": 3}).json()
    assert later == events[3:]


async def _collect(stream: object, count: int) -> list[str]:
    frames: list[str] = []
    async for frame in stream:  # type: ignore[attr-defined]
        frames.append(frame)
        if len(frames) == count:
            break
    await stream.aclose()  # type: ignore[attr-defined]
    return frames


def test_stream_replays_history_then_delivers_live_events(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, MockLLM(tool_turns=0))
    tree_id, _ = start_task(harness.deps, harness.user.id, "t", CONFIG)

    async def scenario() -> list[str]:
        stream = event_stream(clean_db, harness.events, tree_id, after=0, poll_seconds=0.05)
        collector = asyncio.create_task(_collect(stream, 5))
        await asyncio.sleep(0.05)
        await asyncio.to_thread(harness.drain)
        return await asyncio.wait_for(collector, timeout=5)

    frames = asyncio.run(scenario())
    data = [frame for frame in frames if frame.startswith("id:")]
    assert [int(frame.split("\n")[0].removeprefix("id: ")) for frame in data] == list(
        range(1, len(data) + 1)
    )
    assert "event: branch_created" in frames[0]


def test_stream_recovers_events_that_pub_sub_dropped(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, MockLLM(tool_turns=0))
    tree_id, _ = start_task(harness.deps, harness.user.id, "t", CONFIG)

    class SilentEvents:
        """Pub/sub that never delivers anything."""

        def publish(self, event: TreeEvent) -> None:
            return None

        async def subscribe(self, tree_id: UUID) -> object:
            await asyncio.Event().wait()
            yield

    async def scenario() -> list[str]:
        stream = event_stream(
            clean_db,
            SilentEvents(),  # type: ignore[arg-type]
            tree_id,
            after=3,
            poll_seconds=0.02,
        )
        collector = asyncio.create_task(_collect(stream, 3))
        await asyncio.sleep(0.02)
        await asyncio.to_thread(harness.drain)
        return await asyncio.wait_for(collector, timeout=5)

    frames = asyncio.run(scenario())
    assert any(frame.startswith("id: 4") for frame in frames)
