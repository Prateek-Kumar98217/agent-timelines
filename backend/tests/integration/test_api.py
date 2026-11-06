import asyncio
from collections.abc import Iterator
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.adapters.memory import (
    FakeAuthenticator,
    FixtureBrowser,
    HashEmbedder,
    MemoryCache,
    NoopTelemetry,
)
from app.adapters.mock import MockLLM
from app.api.main import choose_provider, create_app
from app.api.stream import event_stream
from app.bootstrap import Runtime
from app.db.models import User
from app.db.session import SessionFactory
from app.domain.engine import start_task
from app.domain.keys import KeyStore
from app.domain.limits import Limits
from app.factory import Adapters
from app.ports import Principal, PrincipalKind, TreeEvent
from app.settings import Profile, Settings
from tests.helpers import CONFIG, Harness


class Api:
    def __init__(self, session_factory: SessionFactory) -> None:
        self.harness = Harness(session_factory, MockLLM(tool_turns=1))
        auth = FakeAuthenticator({"token": Principal(self.harness.user.id, PrincipalKind.LOCAL)})
        harness = self.harness
        self.adapters = adapters = Adapters(
            queue=harness.queue,
            llm=harness.llm,
            tools=harness.tools,
            cache=MemoryCache(),
            rate_limiter=harness.rate_limiter,
            auth=auth,
            live_events=harness.events,
            browser=FixtureBrowser(),
            embedder=HashEmbedder(),
            telemetry=NoopTelemetry(),
        )
        settings = Settings(profile=Profile.TEST, allow_mock_llm=True, default_provider="mock")
        self.key_store = KeyStore(session_factory, b"k" * 32)
        runtime = Runtime(settings, adapters, session_factory, harness.deps, self.key_store)
        self.runtime = runtime
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


def test_new_trees_with_the_scripted_model_offer_the_evaluation_tools(api: Api) -> None:
    tree = api.finished_tree()
    root = node_of(tree, "root")
    payload = root["payload"]
    assert isinstance(payload, dict)
    names = [tool["name"] for tool in payload["config"]["tools"]]
    assert names == [
        "web_search",
        "page_fetch",
        "task_search",
        "calculator",
        "scratchpad_write",
        "kb_search",
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
        ("user_msg", {"text": "a different task"}, 409),
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


def test_compare_totals_tokens_over_the_whole_path(api: Api) -> None:
    tree = api.finished_tree()
    main = tree["branches"][0]["id"]  # type: ignore[index]
    result = node_of(tree, "tool_result")
    fork = api.client.post(
        f"/api/nodes/{result['id']}/fork", json={"edit": {"content": "edited"}}
    ).json()
    api.harness.drain()

    after = api.client.get(f"/api/trees/{tree['id']}").json()
    comparison = api.client.get("/api/compare", params={"a": main, "b": fork["id"]}).json()

    by_id = {node["id"]: node for node in after["nodes"]}
    node = max((n for n in after["nodes"] if n["branch_id"] == fork["id"]), key=lambda n: n["seq"])
    total = 0
    while node is not None:
        total += node["input_tokens"] + node["output_tokens"]
        node = by_id.get(node["parent_id"])
    own = comparison["b"]["input_tokens"] + comparison["b"]["output_tokens"]
    assert comparison["b"]["path_tokens"] == total
    assert total > own


def test_limits_show_what_this_user_is_allowed(api: Api) -> None:
    # The shared test setup raises every limit, so this uses the real table.
    runtime = replace(api.runtime, deps=replace(api.runtime.deps, limits=Limits()))
    client = TestClient(create_app(runtime), headers={"Authorization": "Bearer token"})
    body = client.get("/api/limits").json()
    assert body == {"active_branches": 0, "max_active_branches": 5, "max_turns": 12}


def test_limits_count_only_unfinished_branches(api: Api) -> None:
    api.start()
    assert api.client.get("/api/limits").json()["active_branches"] == 1
    api.harness.drain()
    assert api.client.get("/api/limits").json()["active_branches"] == 0


def test_continue_takes_a_finished_branch_and_refuses_others(api: Api) -> None:
    tree_id, branch = api.start()
    early = api.client.post(f"/api/branches/{branch}/continue", json={"text": "More please"})
    assert early.status_code == 409
    assert "finished" in early.json()["detail"]
    api.harness.drain()
    done = api.client.post(f"/api/branches/{branch}/continue", json={"text": "More please"})
    assert done.status_code == 200
    assert done.json()["status"] == "queued"
    missing = api.client.post(f"/api/branches/{uuid4()}/continue", json={"text": "More"})
    assert missing.status_code == 404
    assert tree_id


@pytest.mark.parametrize(
    "headers",
    [{"Host": "evil.test"}, {"X-Forwarded-Host": "evil.test"}, {"Host": "evil.test:8000"}],
)
def test_a_website_pointing_its_domain_at_this_machine_is_refused(
    api: Api, headers: dict[str, str]
) -> None:
    response = api.client.get("/api/providers", headers=headers)
    assert response.status_code == 400
    assert response.text == "This API only answers on this machine."


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "localhost:8000"},
        {"Host": "127.0.0.1"},
        {"Host": "api:8000", "X-Forwarded-Host": "127.0.0.1:3001"},
        {"Host": "[::1]:8000"},
    ],
)
def test_this_machine_and_the_web_app_are_allowed(api: Api, headers: dict[str, str]) -> None:
    assert api.client.get("/api/providers", headers=headers).status_code == 200


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


API_KEY = "gsk-very-secret-key-ABCD"


def test_providers_list_what_is_usable(api: Api) -> None:
    providers = {item["name"]: item for item in api.client.get("/api/providers").json()}
    assert set(providers) == {"groq", "gemini", "mock"}
    assert providers["groq"]["available"] is False
    assert providers["mock"]["available"] is True


def test_saving_a_key_never_returns_it(api: Api) -> None:
    response = api.client.put("/api/keys/groq", json={"api_key": API_KEY})
    assert response.status_code == 200
    assert response.json()["key_last_four"] == "ABCD"
    assert response.json()["available"] is True
    assert API_KEY not in response.text
    listed = api.client.get("/api/providers").text
    assert API_KEY not in listed
    assert api.key_store.get(api.harness.user.id, "groq") == API_KEY


def test_a_key_is_not_echoed_by_validation_errors(api: Api) -> None:
    response = api.client.put("/api/keys/groq", json={"api_key": {"nested": API_KEY}})
    assert response.status_code == 422
    assert API_KEY not in response.text


def test_deleting_a_key_makes_the_provider_unavailable(api: Api) -> None:
    api.client.put("/api/keys/groq", json={"api_key": API_KEY})
    assert api.client.delete("/api/keys/groq").status_code == 204
    assert api.client.delete("/api/keys/groq").status_code == 404
    assert api.key_store.get(api.harness.user.id, "groq") is None


@pytest.mark.parametrize("key", ["short", "x" * 501, "        "])
def test_implausible_keys_are_rejected(api: Api, key: str) -> None:
    assert api.client.put("/api/keys/groq", json={"api_key": key}).status_code == 422


def test_keys_for_unknown_providers_are_rejected(api: Api) -> None:
    assert api.client.put("/api/keys/ollama", json={"api_key": API_KEY}).status_code == 404


def test_trees_record_the_provider_and_model_in_the_immutable_root(api: Api) -> None:
    api.client.put("/api/keys/groq", json={"api_key": API_KEY})
    tree_id = api.client.post(
        "/api/trees", json={"task": "t", "provider": "groq", "model": "some-model"}
    ).json()["tree_id"]
    root = api.client.get(f"/api/trees/{tree_id}").json()["nodes"][0]
    assert root["payload"]["config"]["provider"] == "groq"
    assert root["payload"]["config"]["model"] == "some-model"
    assert API_KEY not in str(root)
    api.harness.queue.messages.clear()


def test_a_provider_without_a_key_cant_start_a_tree(api: Api) -> None:
    response = api.client.post("/api/trees", json={"task": "t", "provider": "groq"})
    assert response.status_code == 409
    assert "add its key" in response.json()["detail"]


def test_no_provider_at_all_explains_what_to_do(clean_db: SessionFactory) -> None:
    api = Api(clean_db)
    settings = Settings(profile=Profile.TEST)
    principal = Principal(api.harness.user.id, PrincipalKind.LOCAL)
    runtime = Runtime(settings, api.adapters, clean_db, api.harness.deps, api.key_store)
    with pytest.raises(HTTPException) as error:
        choose_provider(runtime, principal, None)
    assert error.value.status_code == 409
    assert "add an API key" in str(error.value.detail)


@pytest.fixture
def two_users(api: Api, clean_db: SessionFactory) -> tuple[Api, TestClient]:
    """An API with a second user, who owns nothing yet."""
    other = User(id=uuid4(), kind="local")
    with clean_db() as session:
        session.add(other)
        session.commit()
    api.adapters.auth.principals["other"] = Principal(other.id, PrincipalKind.LOCAL)  # type: ignore[attr-defined]
    client = TestClient(api.client.app, headers={"Authorization": "Bearer other"})
    return api, client


def test_another_user_gets_404_for_everything_about_a_tree(
    two_users: tuple[Api, TestClient],
) -> None:
    api, other = two_users
    tree = api.finished_tree()
    tree_id, branch_id = tree["id"], tree["branches"][0]["id"]  # type: ignore[index]
    user_node = node_of(tree, "user_msg")

    assert other.get(f"/api/trees/{tree_id}").status_code == 404
    assert other.get(f"/api/trees/{tree_id}/events").status_code == 404
    assert other.get(f"/api/trees/{tree_id}/stream").status_code == 404
    assert other.post(f"/api/branches/{branch_id}/cancel").status_code == 404
    assert other.get("/api/compare", params={"a": branch_id, "b": branch_id}).status_code == 404
    fork = other.post(f"/api/nodes/{user_node['id']}/fork", json={"edit": {"text": "mine now"}})
    assert fork.status_code == 404
    assert api.harness.queue.depth() == 0


def test_a_refusal_looks_the_same_as_a_tree_that_does_not_exist(
    two_users: tuple[Api, TestClient],
) -> None:
    api, other = two_users
    tree_id, _ = api.start()
    real = other.get(f"/api/trees/{tree_id}")
    missing = other.get(f"/api/trees/{uuid4()}")
    assert (real.status_code, real.json()) == (missing.status_code, missing.json())
    api.harness.queue.messages.clear()


def test_lists_and_keys_are_private_to_each_user(two_users: tuple[Api, TestClient]) -> None:
    api, other = two_users
    api.start("mine")
    api.client.put("/api/keys/groq", json={"api_key": API_KEY})
    assert other.get("/api/trees").json() == []
    groq = next(item for item in other.get("/api/providers").json() if item["name"] == "groq")
    assert groq["available"] is False
    api.harness.queue.messages.clear()


def test_the_owner_keeps_access(two_users: tuple[Api, TestClient]) -> None:
    api, _ = two_users
    tree = api.finished_tree()
    assert api.client.get(f"/api/trees/{tree['id']}").status_code == 200


def test_out_of_scope_tasks_are_refused_before_any_work(api: Api) -> None:
    response = api.client.post("/api/trees", json={"task": "Write a Python script to sort files"})
    assert response.status_code == 422
    assert "not coding" in response.json()["detail"]
    assert api.harness.queue.depth() == 0
    assert api.client.get("/api/trees").json() == []


def test_a_fork_cant_change_the_task(api: Api) -> None:
    tree = api.finished_tree()
    node = node_of(tree, "user_msg")
    response = api.client.post(
        f"/api/nodes/{node['id']}/fork", json={"edit": {"text": "Another question entirely"}}
    )
    assert response.status_code == 409
    assert "task can't be changed" in response.json()["detail"]
    assert api.harness.queue.depth() == 0
    after = api.client.get(f"/api/trees/{tree['id']}").json()
    assert len(after["branches"]) == len(tree["branches"])  # type: ignore[arg-type]
