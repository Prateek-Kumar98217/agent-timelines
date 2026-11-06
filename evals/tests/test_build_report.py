"""The report page, built from the published results."""

import json
from pathlib import Path

from build_report import build, markdown_table
from report import load_runs

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "docs" / "eval"


def page(judge_reason: str | None = None) -> str:
    judge = json.loads((EVAL / "judge.json").read_text())
    if judge_reason is not None:
        judge["answers"][0]["reason"] = judge_reason
    return build(
        load_runs(EVAL / "runs.jsonl"),
        judge,
        json.loads((EVAL / "recall.json").read_text()),
        json.loads((EVAL / "load.json").read_text()),
        {
            "model": {"steps": 10, "p50": 30.0, "p95": 90.0},
            "tool": {"steps": 10, "p50": 0.2, "p95": 1.5},
        },
        {"Backend": 5},
        markdown_table((ROOT / "docs" / "model-benchmark.md").read_text()),
        (ROOT / "docs" / "LIMITATIONS.md").read_text(),
        "2026-01-01",
    )


def test_it_says_where_the_results_came_from() -> None:
    assert "Every result here comes from local tests" in page()
    assert "This measures the platform, not a model" in page()


def test_every_section_is_there() -> None:
    built = page()
    for section in ("edits", "judge", "recall", "load", "speed", "tests", "limits"):
        assert f'id="{section}"' in built
        assert f'href="#{section}"' in built


def test_text_from_a_model_cant_become_markup() -> None:
    built = page("<script>alert(1)</script>")
    assert "<script>" not in built
    assert "&lt;script&gt;" in built


def test_it_has_no_external_requests_or_secrets() -> None:
    built = page()
    assert "<script" not in built
    assert 'src="http' not in built
    assert 'href="http' not in built
    assert "AIza" not in built
    assert "gsk_" not in built
