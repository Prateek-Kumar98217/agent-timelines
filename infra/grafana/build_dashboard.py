"""Generates agent-timelines.json. Edit this file, not the JSON."""

import json
from pathlib import Path
from typing import Any

WINDOW = "5m"


def quantiles(metric: str, by: str = "") -> list[tuple[str, str]]:
    group = f"le, {by}" if by else "le"
    suffix = f" {{{{{by}}}}}" if by else ""
    return [
        (
            f"histogram_quantile({q}, sum by ({group}) (rate({metric}_bucket[{WINDOW}])))",
            f"p{int(q * 100)}{suffix}",
        )
        for q in (0.5, 0.95)
    ]


def rate_by(metric: str, by: str) -> list[tuple[str, str]]:
    return [(f"sum by ({by}) (rate({metric}[{WINDOW}]))", f"{{{{{by}}}}}")]


PANELS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("Step latency", "s", quantiles("step_duration_seconds", "action")),
    ("Fork latency", "s", quantiles("fork_duration_seconds")),
    ("Queue wait", "s", quantiles("queue_wait_seconds")),
    (
        "Admission rejection rate",
        "percentunit",
        [
            (
                f"sum(rate(admission_rejected_total[{WINDOW}])) / (sum(rate("
                f"admission_rejected_total[{WINDOW}])) + sum(rate(admission_accepted_total"
                f"[{WINDOW}])))",
                "rejected",
            )
        ],
    ),
    ("Rejections by reason", "reqps", rate_by("admission_rejected_total", "reason")),
    (
        "Tool cache hit rate",
        "percentunit",
        [
            (
                f'sum by (tool) (rate(tool_cache_total{{result="hit"}}[{WINDOW}])) / '
                f"sum by (tool) (rate(tool_cache_total[{WINDOW}]))",
                "{{tool}}",
            )
        ],
    ),
    ("LLM latency", "s", quantiles("llm_duration_seconds", "provider")),
    ("LLM tokens", "short", rate_by("llm_tokens_total", "direction")),
    ("Tool latency at the gateway", "s", quantiles("tool_duration_seconds", "tool")),
    (
        "Gateway overhead (client p95 minus tool p95)",
        "s",
        [
            (
                f"histogram_quantile(0.95, sum by (le) (rate(tool_client_duration_seconds_bucket"
                f"[{WINDOW}]))) - histogram_quantile(0.95, sum by (le) (rate("
                f"tool_duration_seconds_bucket[{WINDOW}])))",
                "overhead",
            )
        ],
    ),
    ("Branches finished", "cps", rate_by("branch_finished_total", "status")),
    ("Step outcomes", "cps", rate_by("step_outcome_total", "outcome")),
]


def panel(index: int, title: str, unit: str, queries: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "id": index + 1,
        "title": title,
        "type": "timeseries",
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "gridPos": {"h": 8, "w": 12, "x": 12 * (index % 2), "y": 8 * (index // 2)},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
        "targets": [
            {
                "refId": chr(ord("A") + position),
                "expr": expr,
                "legendFormat": legend,
                "datasource": {"type": "prometheus", "uid": "prometheus"},
            }
            for position, (expr, legend) in enumerate(queries)
        ],
    }


def main() -> None:
    dashboard = {
        "uid": "agent-timelines",
        "title": "Agent Timelines",
        "schemaVersion": 39,
        "version": 1,
        "editable": True,
        "refresh": "10s",
        "time": {"from": "now-30m", "to": "now"},
        "panels": [
            panel(index, title, unit, queries)
            for index, (title, unit, queries) in enumerate(PANELS)
        ],
    }
    output = Path(__file__).with_name("agent-timelines.json")
    output.write_text(json.dumps(dashboard, indent=2) + "\n")


if __name__ == "__main__":
    main()
