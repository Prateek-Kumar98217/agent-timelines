r"""Check that traces, metrics, logs and dashboards work on the running local stack.

APP_ALLOW_MOCK_LLM=true APP_DEFAULT_PROVIDER=mock docker compose up -d --build --wait api
uv run python scripts/smoke_observability.py
"""

import json
import subprocess
import time
from typing import Any

import httpx

API = "http://127.0.0.1:8000"
PROMETHEUS = "http://127.0.0.1:9090"
TEMPO = "http://127.0.0.1:3200"
LOKI = "http://127.0.0.1:3100"
GRAFANA = "http://127.0.0.1:3000"
FINISHED = {"completed", "capped", "budget_exhausted", "failed", "cancelled"}
FAKE_KEY = "gsk-observability-canary-WXYZ"
client = httpx.Client(timeout=20)


def retry(description: str, check: Any, timeout: float = 90) -> Any:
    """Telemetry arrives in batches, so wait for it instead of asserting straight away."""
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out waiting for {description}")
        time.sleep(2)


def drive_traffic() -> None:
    created = client.post(f"{API}/api/trees", json={"task": "Observability check"}).json()
    tree_id, branch_id = created["tree_id"], created["branch_id"]

    def tree() -> dict[str, Any]:
        return client.get(f"{API}/api/trees/{tree_id}").json()

    def finished(ids: list[str]) -> bool:
        statuses = {b["id"]: b["status"] for b in tree()["branches"]}
        return all(statuses[item] in FINISHED for item in ids)

    retry("the main branch", lambda: finished([branch_id]))
    result = next(n for n in tree()["nodes"] if n["type"] == "tool_result")
    fork = client.post(
        f"{API}/api/nodes/{result['id']}/fork", json={"edit": {"content": "Observability fork"}}
    ).json()["id"]
    retry("the fork", lambda: finished([fork]))
    # Identical searches, so the tool cache gets hits as well as misses.
    for _ in range(2):
        again = client.post(f"{API}/api/trees", json={"task": "Repeat this search"}).json()
        retry(
            "the repeat",
            lambda item=again: (
                next(
                    b["status"]
                    for b in client.get(f"{API}/api/trees/{item['tree_id']}").json()["branches"]
                )
                in FINISHED
            ),
        )
    # More tasks at once than the branch cap, to produce rejections.
    responses = [client.post(f"{API}/api/trees", json={"task": f"Burst {i}"}) for i in range(8)]
    assert any(response.status_code == 429 for response in responses)
    assert client.put(f"{API}/api/keys/groq", json={"api_key": FAKE_KEY}).status_code == 200
    print("ok   traffic sent: run, fork, cache hits, rejections, saved key")


def check_trace_spans_all_services() -> None:
    def find() -> dict[str, Any] | None:
        response = client.get(
            f"{TEMPO}/api/search", params={"q": '{ name = "gateway.tool" }', "limit": 5}
        )
        traces = response.json().get("traces") or []
        return traces[0] if traces else None

    trace_id = retry("a trace through the gateway", find)["traceID"]

    def services() -> set[str] | None:
        body = client.get(f"{TEMPO}/api/traces/{trace_id}").json()
        found = set()
        for batch in body.get("batches", body.get("resourceSpans", [])):
            for attribute in batch["resource"]["attributes"]:
                if attribute["key"] == "service.name":
                    found.add(attribute["value"]["stringValue"])
        return found if {"api", "worker", "gateway"} <= found else None

    print(f"ok   one trace spans {sorted(retry('all services in one trace', services))}")


def query(expression: str) -> list[Any]:
    response = client.get(f"{PROMETHEUS}/api/v1/query", params={"query": expression})
    assert response.status_code == 200, f"{expression}: {response.text}"
    return response.json()["data"]["result"]


def check_dashboard() -> None:
    def loaded() -> dict[str, Any] | None:
        response = client.get(f"{GRAFANA}/api/dashboards/uid/agent-timelines")
        return response.json() if response.status_code == 200 else None

    dashboard = retry("the dashboard", loaded)["dashboard"]
    pending = {
        target["expr"]: panel["title"]
        for panel in dashboard["panels"]
        for target in panel["targets"]
    }

    def all_have_data() -> bool:
        for expression in list(pending):
            if query(expression):
                del pending[expression]
        return not pending

    try:
        retry("every dashboard query to return data", all_have_data, timeout=120)
    except TimeoutError:
        raise AssertionError(f"panels with no data: {sorted(set(pending.values()))}") from None
    print(f"ok   dashboard loaded; all {len(dashboard['panels'])} panels return data")


def check_series_are_bounded() -> None:
    names = client.get(f"{PROMETHEUS}/api/v1/label/branch_id/values").json()["data"]
    assert names == []
    instances = {item["metric"].get("instance") for item in query("step_outcome_total")}
    assert len(instances) > 1, "worker processes share one instance, so counters would collide"
    print(f"ok   no branch IDs in metrics; {len(instances)} worker processes report separately")


def check_logs_arrive_with_no_secrets() -> None:
    def lines(selector: str) -> list[str]:
        response = client.get(
            f"{LOKI}/loki/api/v1/query_range",
            params={"query": selector, "limit": 5000, "since": "1h"},
        )
        streams = response.json()["data"]["result"]
        return [value[1] for stream in streams for value in stream["values"]]

    found = retry(
        "worker logs in Loki",
        lambda: lines('{service_name="worker"}') or lines('{service_name=~".+"}'),
    )
    everything = "\n".join(found + lines('{service_name=~".+"}'))
    assert FAKE_KEY not in everything
    container_logs = subprocess.run(
        ["docker", "compose", "logs", "api", "worker", "gateway"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    assert FAKE_KEY not in container_logs
    spans = client.get(
        f"{TEMPO}/api/search", params={"q": f'{{ .api_key = "{FAKE_KEY}" }}', "limit": 1}
    )
    assert not spans.json().get("traces")
    assert FAKE_KEY not in json.dumps(query("admission_accepted_total"))
    print(f"ok   {len(found)} log lines in Loki; the saved key is in no log, span or metric")


def main() -> None:
    drive_traffic()
    check_trace_spans_all_services()
    check_dashboard()
    check_series_are_bounded()
    check_logs_arrive_with_no_secrets()
    client.delete(f"{API}/api/keys/groq")
    print("all observability checks passed")


if __name__ == "__main__":
    main()
