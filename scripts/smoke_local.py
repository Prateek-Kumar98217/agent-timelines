"""End-to-end and failure test against the running local stack.

Start it with short leases and slow steps, so failures land mid-step and recovery is quick:
    APP_LEASE_SECONDS=10 APP_MOCK_LLM_LATENCY_SECONDS=1 \
    APP_ALLOW_MOCK_LLM=true APP_DEFAULT_PROVIDER=mock APP_LOCAL_ACTIVE_BRANCHES=30 \
        docker compose up -d --build --wait api worker beat
    uv run python scripts/smoke_local.py
"""

import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

BASE = "http://127.0.0.1:8000"
FINISHED = {"completed", "capped", "budget_exhausted", "failed", "cancelled"}
client = httpx.Client(base_url=BASE, timeout=15)


def compose(*args: str, timeout: int = 180) -> str:
    result = subprocess.run(
        ["docker", "compose", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    return result.stdout


def create(task: str) -> tuple[str, str]:
    response = client.post("/api/trees", json={"task": task})
    response.raise_for_status()
    body = response.json()
    return body["tree_id"], body["branch_id"]


def tree(tree_id: str) -> dict[str, Any]:
    response = client.get(f"/api/trees/{tree_id}")
    response.raise_for_status()
    return response.json()


def status(tree_id: str, branch_id: str) -> str:
    return next(b["status"] for b in tree(tree_id)["branches"] if b["id"] == branch_id)


def wait_finished(tree_id: str, branch_ids: list[str], timeout: float = 120) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    while True:
        statuses = {
            b["id"]: b["status"] for b in tree(tree_id)["branches"] if b["id"] in branch_ids
        }
        if all(value in FINISHED for value in statuses.values()):
            return statuses
        if time.monotonic() > deadline:
            raise TimeoutError(f"branches still unfinished: {statuses}")
        time.sleep(0.25)


def wait_for_status(tree_id: str, branch_id: str, wanted: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while (current := status(tree_id, branch_id)) != wanted:
        if time.monotonic() > deadline or current in FINISHED:
            raise TimeoutError(f"wanted {wanted}, branch is {current}")
        time.sleep(0.02)


def nodes_of(data: dict[str, Any], branch_id: str) -> list[dict[str, Any]]:
    return sorted((n for n in data["nodes"] if n["branch_id"] == branch_id), key=lambda n: n["seq"])


def fork(node_id: str, edit: dict[str, Any]) -> str:
    response = client.post(f"/api/nodes/{node_id}/fork", json={"edit": edit})
    response.raise_for_status()
    return response.json()["id"]


def check_run_forks_and_stream() -> None:
    tree_id, main = create("How tall is Everest?")
    frames: list[dict[str, Any]] = []
    stop = threading.Event()

    def listen() -> None:
        with (
            httpx.Client(base_url=BASE, timeout=None) as sse,
            sse.stream("GET", f"/api/trees/{tree_id}/stream") as response,
        ):
            event: dict[str, Any] = {}
            for line in response.iter_lines():
                if line.startswith("id: "):
                    event = {"seq": int(line[4:])}
                elif line.startswith("event: "):
                    event["type"] = line[7:]
                elif line.startswith("data: "):
                    event["payload"] = json.loads(line[6:])
                    frames.append(event)
                if stop.is_set():
                    return

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    assert wait_finished(tree_id, [main])[main] == "completed"

    data = tree(tree_id)
    nodes = {n["type"]: n for n in reversed(nodes_of(data, main))}
    before = {n["id"]: n["payload"] for n in data["nodes"]}

    edits = [
        (nodes["llm_output"]["id"], {"text": "I know this: 8,849 m."}),
        (nodes["tool_call"]["id"], {"arguments": {"query": "Everest in feet"}}),
        (nodes["tool_result"]["id"], {"content": "Edited evidence"}),
    ]
    forks = [fork(node_id, edit) for node_id, edit in edits]
    forks += [fork(nodes["tool_result"]["id"], {"content": f"Parallel {i}"}) for i in range(16)]
    refused_task = client.post(
        f"/api/nodes/{nodes['user_msg']['id']}/fork", json={"edit": {"text": "How tall is K2?"}}
    )
    assert refused_task.status_code == 409, refused_task.text
    statuses = wait_finished(tree_id, forks)
    assert set(statuses.values()) == {"completed"}, statuses

    after = tree(tree_id)
    payloads = {n["id"]: n["payload"] for n in after["nodes"]}
    assert all(payloads[node_id] == payload for node_id, payload in before.items())
    assert status(tree_id, main) == "completed"

    comparison = client.get("/api/compare", params={"a": main, "b": forks[3]}).json()
    assert comparison["last_common_node_id"] == nodes["tool_call"]["id"]

    time.sleep(1)
    stop.set()
    seqs = [frame["seq"] for frame in frames]
    assert seqs == list(range(1, after["last_event_seq"] + 1)), "stream had gaps"
    print(f"ok   run, 20 parallel forks, compare, stream ({len(seqs)} events, no gaps)")


def active_tasks() -> str:
    return compose(
        "exec", "-T", "worker", "celery", "-A", "app.worker.celery_app", "inspect", "active"
    )


def queue_messages() -> int:
    output = compose(
        "exec", "-T", "rabbitmq", "rabbitmqctl", "-q", "list_queues", "name", "messages"
    )
    for line in output.splitlines():
        parts = line.split()
        if parts and parts[0] == "steps":
            return int(parts[1])
    raise AssertionError(f"no steps queue in: {output}")


def check_limits_and_scope() -> None:
    refused = client.post("/api/trees", json={"task": "Write a Python script to rename files"})
    assert refused.status_code == 422, refused.text

    tree_id, main = create("Reach the branch cap")
    assert wait_finished(tree_id, [main])[main] == "completed"
    result_id = next(n["id"] for n in nodes_of(tree(tree_id), main) if n["type"] == "tool_result")
    # More than the cap of 30, sent at once, so the database lock has to hold under real load.
    edits = [{"content": f"Cap test {i}"} for i in range(40)]
    with ThreadPoolExecutor(10) as pool:
        responses = list(
            pool.map(
                lambda edit: client.post(f"/api/nodes/{result_id}/fork", json={"edit": edit}), edits
            )
        )
    accepted = [r.json()["id"] for r in responses if r.status_code == 201]
    rejected = [r for r in responses if r.status_code == 429]
    assert len(accepted) + len(rejected) == len(edits), [r.status_code for r in responses]
    assert len(accepted) <= 30
    assert rejected
    assert {r.json()["reason"] for r in rejected} == {"active_branches"}
    assert all("Retry-After" in r.headers for r in rejected)
    for branch_id in accepted:
        client.post(f"/api/branches/{branch_id}/cancel")
    print(f"ok   limits: {len(accepted)} of 40 parallel forks accepted, {len(rejected)} got 429")


def check_tools_through_gateway() -> None:
    tree_id, branch = create("capital of Australia")
    assert wait_finished(tree_id, [branch])[branch] == "completed"
    nodes = nodes_of(tree(tree_id), branch)
    result = next(n for n in nodes if n["type"] == "tool_result")
    assert not result["payload"]["is_error"], result["payload"]
    assert "Source: https://simple.wikipedia.org/" in result["payload"]["content"]
    print("ok   kb_search through the MCP gateway returned cited results")


def check_browser_isolation() -> None:
    probe = (
        "import socket\n"
        "for host, port in [('postgres',5432),('redis',6379),('rabbitmq',5672),('api',8000)]:\n"
        "    try:\n"
        "        socket.create_connection((host,port),timeout=2).close(); print('REACHED',host)\n"
        "    except OSError: pass\n"
    )
    output = compose("exec", "-T", "browser", "python", "-c", probe)
    assert "REACHED" not in output, output
    secrets = compose("exec", "-T", "browser", "env")
    assert "SECRET" not in secrets
    assert "DATABASE" not in secrets

    # The gateway has to share a network with the browser, so a call from there must do nothing.
    unsigned_call = (
        "import json,urllib.request\n"
        "body=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call','params':"
        "{'name':'calculator','arguments':{'expression':'6*7'}}}).encode()\n"
        "request=urllib.request.Request('http://gateway:8100/mcp',data=body,headers={"
        "'Content-Type':'application/json','Accept':'application/json, text/event-stream',"
        "'MCP-Protocol-Version':'2025-06-18'})\n"
        "try:\n"
        "    print(urllib.request.urlopen(request,timeout=10).read().decode())\n"
        "except Exception as error:\n"
        "    print('refused',error)\n"
    )
    answer = compose("exec", "-T", "browser", "python", "-c", unsigned_call)
    assert "42" not in answer, answer
    print("ok   browser can't reach internal services, holds no secrets, can't run gateway tools")


def check_waiting_uses_no_compute() -> None:
    tree_id, branch = create(f"Wait please [rate-limit:25] {time.time()}")
    wait_for_status(tree_id, branch, "waiting")
    time.sleep(2)
    assert status(tree_id, branch) == "waiting"
    assert queue_messages() == 0
    active = active_tasks()
    assert "empty" in active or "- empty -" in active, active
    print("ok   waiting branch: no queue message and no active worker task")
    client.post(f"/api/branches/{branch}/cancel").raise_for_status()


def check_worker_killed_mid_step() -> None:
    tree_id, branch = create("Survive a worker crash")
    wait_for_status(tree_id, branch, "running")
    compose("kill", "-s", "SIGKILL", "worker")
    print("     worker killed while the branch was running")
    # `start` keeps the container's settings. `up` could recreate it with a different lease.
    compose("start", "worker")
    assert wait_finished(tree_id, [branch])[branch] == "completed"
    nodes = nodes_of(tree(tree_id), branch)
    assert [n["seq"] for n in nodes] == list(range(len(nodes)))
    assert nodes[-1]["type"] == "llm_output"
    print("ok   SIGKILLed worker: branch resumed with no duplicate or missing nodes")


def check_broker_outage() -> None:
    compose("stop", "rabbitmq")
    started = time.monotonic()
    tree_id, branch = create("Survive a broker outage")
    elapsed = time.monotonic() - started
    assert status(tree_id, branch) == "queued"
    time.sleep(3)
    assert status(tree_id, branch) == "queued"
    print(f"     task accepted in {elapsed:.1f}s with the broker down")
    compose("start", "rabbitmq")
    assert wait_finished(tree_id, [branch], timeout=180)[branch] == "completed"
    print("ok   broker outage: nothing lost, branch completed after recovery")


def main() -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    check_run_forks_and_stream()
    check_limits_and_scope()
    check_tools_through_gateway()
    check_browser_isolation()
    check_waiting_uses_no_compute()
    check_worker_killed_mid_step()
    check_broker_outage()
    print("all smoke checks passed")


if __name__ == "__main__":
    main()
