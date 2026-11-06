from typing import Any

import pytest
from kb_labels import load_articles
from report import calibrate_threshold, percentile, render, summarize_class, wilson_interval
from tasks import TASKS, TASKS_BY_ID, fact_score

from app.domain.task_gate import task_problem

PRICES = {
    "note": "n",
    "options": [{"name": "x", "input_per_million": 1.0, "output_per_million": 2.0}],
}


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.id)
def test_every_fact_is_in_its_article(task: Any) -> None:
    articles = load_articles()
    for fact in task.facts:
        assert fact.evidence.lower() in articles[fact.title]["text"].lower(), fact


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.id)
def test_the_expected_answer_passes_the_fact_check(task: Any) -> None:
    assert fact_score(task, task.expected_answer) == 1.0


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.id)
def test_tasks_and_their_edits_pass_the_scope_gate(task: Any) -> None:
    for text in (task.text, task.other_task, task.paraphrase):
        assert task_problem(text) is None, text


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.id)
def test_the_contradiction_differs_from_the_real_fact(task: Any) -> None:
    assert not any(fact.evidence in task.contradiction for fact in task.facts)


def test_fact_score_counts_required_facts_and_ignores_commas() -> None:
    task = TASKS_BY_ID["peak_gap"]
    assert fact_score(task, None) == 0.0
    assert fact_score(task, "No idea.") == 0.0
    assert fact_score(task, "Aconcagua is 6,962 m.") == pytest.approx(1 / 3)
    assert fact_score(task, "6,190 and 6,962, a gap of 772") == 1.0


def test_fact_score_needs_whole_numbers() -> None:
    task = TASKS_BY_ID["apollo_gap"]
    assert fact_score(task, "19690 and 19720 and 33") == 0.0


def test_wilson_interval_behaves_on_small_samples() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)
    low, high = wilson_interval(0, 10)
    assert low == 0.0
    assert 0.2 < high < 0.4
    low, high = wilson_interval(5, 10)
    assert low < 0.5 < high


def test_percentile_interpolates() -> None:
    assert percentile([1.0, 2.0, 3.0], 0.5) == 2.0
    assert percentile([0.0, 10.0], 0.1) == pytest.approx(1.0)


def run(
    edit_class: str, similarity: float | None, facts: float = 1.0, **extra: Any
) -> dict[str, Any]:
    return {
        "task_id": "t",
        "model": "m",
        "edit_class": edit_class,
        "final_similarity": similarity,
        "source_fact_score": 1.0,
        "fact_score": facts,
        "status": "completed",
        "tool_call_overlap": 1.0,
        "turns": 5,
        "tokens": 1000,
        "input_tokens": 800,
        "output_tokens": 200,
        "seconds": 10.0,
        **extra,
    }


def test_threshold_comes_only_from_no_op_forks() -> None:
    runs = [run("noop_user_msg", 0.9), run("noop_tool_result", 0.8), run("result_error", 0.1)]
    assert calibrate_threshold(runs) == pytest.approx(0.81)
    assert calibrate_threshold([run("result_error", 0.1)]) is None


def test_class_summary_counts_divergence_and_changed_facts() -> None:
    summary = summarize_class(
        [run("e", 0.5, 0.5), run("e", 0.95), run("e", None, status="failed", facts=0.0)], 0.9
    )
    assert (summary["runs"], summary["completed"], summary["scored"]) == (3, 2, 2)
    assert summary["divergence_rate"] == 0.5
    assert summary["facts_changed_rate"] == pytest.approx(2 / 3)


def test_report_names_edits_clearly_above_the_baseline_only() -> None:
    main = run("main", None, source_fact_score=None, tool_call_overlap=None)
    runs = [
        main,
        *[run("noop_user_msg", 0.99) for _ in range(30)],
        *[run("result_contradiction", 0.2, 0.5) for _ in range(30)],
        *[run("user_paraphrase", 0.99) for _ in range(3)],
    ]
    report = render(runs, PRICES)
    assert (
        "**Clearly above the baseline** (intervals don't overlap): result_contradiction." in report
    )
    assert "1 of 1 are inside the 4 to 12 target" in report
    assert "$0.00120" in report


def test_report_needs_a_baseline() -> None:
    with pytest.raises(ValueError, match="no-op"):
        render([run("main", None)], PRICES)
