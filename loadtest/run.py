"""Runs load scenarios and reports latency, queue wait and rejections from the stack's own metrics.

uv run --all-groups python loadtest/run.py
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
PROMETHEUS = "http://127.0.0.1:9090"
BASE_ENV = {
    "APP_ALLOW_MOCK_LLM": "true",
    "APP_DEFAULT_PROVIDER": "mock",
    "APP_MOCK_LLM_LATENCY_SECONDS": "0.5",
    "APP_LOCAL_TOKENS_PER_DAY": "100000000",
    "APP_MAX_QUEUED_BRANCHES": "100",
}
# (name, users, branch cap, queue limit). Each varies load against the limits protecting the system.
# Virtual users all act as the one local user, so the branch cap applies to them together.
SCENARIOS = [
    ("one_user", 1, "50", "100"),
    ("five_users", 5, "50", "100"),
    ("twenty_users", 20, "50", "100"),
    ("overload_user_cap", 60, "20", "100"),
    ("overload_queue_limit", 60, "500", "25"),
]
DURATION_SECONDS = 90


def compose(env: dict[str, str], *args: str) -> None:
    subprocess.run(
        ["docker", "compose", *args], check=True, env={**os.environ, **env}, capture_output=True
    )


def instant(query: str) -> float | None:
    response = httpx.get(f"{PROMETHEUS}/api/v1/query", params={"query": query}, timeout=20)
    results = response.json()["data"]["result"]
    if not results:
        return None
    value = float(results[0]["value"][1])
    return None if value != value else value  # NaN means there were no samples


def quantile(metric: str, q: float, window: str, selector: str = "") -> float | None:
    return instant(
        f"histogram_quantile({q}, sum by (le) (rate({metric}_bucket{selector}[{window}])))"
    )


def mean(metric: str, window: str, selector: str = "") -> float | None:
    return instant(
        f"sum(rate({metric}_sum{selector}[{window}])) / "
        f"sum(rate({metric}_count{selector}[{window}]))"
    )


def total(metric: str, window: str) -> float:
    return instant(f"sum(increase({metric}[{window}]))") or 0.0


def run_scenario(name: str, users: int, cap: str, queue_limit: str) -> dict[str, Any]:
    env = {**BASE_ENV, "APP_LOCAL_ACTIVE_BRANCHES": cap, "APP_MAX_QUEUED_BRANCHES": queue_limit}
    compose(env, "up", "-d", "--wait", "--force-recreate", "api", "worker", "beat")
    time.sleep(10)
    subprocess.run(
        [
            "locust",
            "-f",
            str(ROOT / "loadtest" / "locustfile.py"),
            "--headless",
            "-H",
            "http://127.0.0.1:8000",
            "-u",
            str(users),
            "-r",
            "5",
            "-t",
            f"{DURATION_SECONDS}s",
            "--only-summary",
            "--csv",
            str(ROOT / "loadtest" / "results" / name),
        ],
        check=False,
        capture_output=True,
    )
    time.sleep(12)
    window = f"{DURATION_SECONDS + 10}s"
    accepted = total("admission_accepted_total", window)
    rejected = total("admission_rejected_total", window)
    result = {
        "scenario": name,
        "users": users,
        "branch_cap": int(cap),
        "queue_limit": int(queue_limit),
        "accepted": round(accepted),
        "rejected": round(rejected),
        "rejection_rate": rejected / (accepted + rejected) if accepted + rejected else 0.0,
        "fork_mean": mean("fork_duration_seconds", window),
        "queue_wait_mean": mean("queue_wait_seconds", window),
        "step_llm_mean": mean("step_duration_seconds", window, '{action="llm"}'),
        "step_tool_mean": mean("step_duration_seconds", window, '{action="tool"}'),
        "fork_p50": quantile("fork_duration_seconds", 0.5, window),
        "fork_p95": quantile("fork_duration_seconds", 0.95, window),
        "queue_wait_p50": quantile("queue_wait_seconds", 0.5, window),
        "queue_wait_p95": quantile("queue_wait_seconds", 0.95, window),
        "step_llm_p50": quantile("step_duration_seconds", 0.5, window, '{action="llm"}'),
        "step_llm_p95": quantile("step_duration_seconds", 0.95, window, '{action="llm"}'),
        "step_tool_p50": quantile("step_duration_seconds", 0.5, window, '{action="tool"}'),
        "step_tool_p95": quantile("step_duration_seconds", 0.95, window, '{action="tool"}'),
        "cache_hit_rate": instant(
            f'sum(increase(tool_cache_total{{result="hit"}}[{window}])) / '
            f"sum(increase(tool_cache_total[{window}]))"
        ),
    }
    reasons = httpx.get(
        f"{PROMETHEUS}/api/v1/query",
        params={"query": f"sum by (reason) (increase(admission_rejected_total[{window}]))"},
        timeout=20,
    ).json()["data"]["result"]
    result["rejected_by_reason"] = {
        item["metric"].get("reason", "?"): round(float(item["value"][1])) for item in reasons
    }
    return result


def seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}s"


def render(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Load test",
        "",
        f"Each scenario ran for {DURATION_SECONDS}s on one laptop with the mock LLM at 0.5s per "
        "call and 4 worker processes, so this measures the platform and not any model. Latencies "
        "come from the stack's own metrics. Means are measured. Percentiles are estimated from "
        "coarse histogram buckets, so they can overstate: a 0.5s step can show a p95 near 1s. "
        "Generated by `loadtest/run.py`.",
        "",
        "| Scenario | Users | Branch cap | Queue limit | Accepted | Rejected | Rejection rate "
        "| Fork mean / p95 | Queue wait mean / p95 | LLM step mean / p95 | Tool step mean / p95 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['scenario']} | {r['users']} | {r['branch_cap']} | {r['queue_limit']} "
            f"| {r['accepted']} | {r['rejected']} | {r['rejection_rate'] * 100:.0f}% "
            f"| {seconds(r['fork_mean'])} / {seconds(r['fork_p95'])} "
            f"| {seconds(r['queue_wait_mean'])} / {seconds(r['queue_wait_p95'])} "
            f"| {seconds(r['step_llm_mean'])} / {seconds(r['step_llm_p95'])} "
            f"| {seconds(r['step_tool_mean'])} / {seconds(r['step_tool_p95'])} |"
        )
    lines += ["", "**Rejections by reason:**", ""]
    for r in results:
        lines.append(f"- {r['scenario']}: {r['rejected_by_reason'] or 'none'}")
    return "\n".join(lines) + "\n"


def main() -> None:
    (ROOT / "loadtest" / "results").mkdir(exist_ok=True)
    results = []
    for scenario in SCENARIOS:
        print(f"running {scenario[0]}", flush=True)
        results.append(run_scenario(*scenario))
        print(json.dumps(results[-1]), flush=True)
    output = ROOT / "docs" / "eval"
    (output / "load.json").write_text(json.dumps(results, indent=2) + "\n")
    (output / "load.md").write_text(render(results))
    print(render(results))


if __name__ == "__main__":
    main()
