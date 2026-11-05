"""Traces and metrics from real engine runs, recorded in memory."""

from typing import Any

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.adapters.telemetry import OtelTelemetry
from app.db.session import SessionFactory
from app.domain.engine import start_task
from app.domain.forking import fork_from_edit
from app.domain.sweeper import sweep
from app.ports import ProviderRateLimitedError
from tests.helpers import CONFIG, Harness, text_reply, tool_reply

SECRET_TEXT = "the-private-question-text"


class Recorded:
    def __init__(self) -> None:
        self.spans = InMemorySpanExporter()
        self.reader = InMemoryMetricReader()
        self.telemetry = OtelTelemetry(
            "test", span_exporter=self.spans, metric_reader=self.reader, export_logs=False
        )

    def finished(self) -> list[Any]:
        self.telemetry.flush()
        return list(self.spans.get_finished_spans())

    def metrics(self) -> dict[str, list[Any]]:
        data = self.reader.get_metrics_data()
        assert data is not None
        found: dict[str, list[Any]] = {}
        for resource in data.resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    found[metric.name] = list(metric.data.data_points)
        return found


@pytest.fixture
def recorded() -> Recorded:
    return Recorded()


def harness_for(session_factory: SessionFactory, recorded: Recorded, replies: list[Any]) -> Harness:
    harness = Harness(session_factory, replies)
    harness.deps.telemetry = recorded.telemetry
    return harness


def test_a_whole_branch_is_one_trace(clean_db: SessionFactory, recorded: Recorded) -> None:
    harness = harness_for(clean_db, recorded, [tool_reply(), text_reply()])
    start_task(harness.deps, harness.user.id, "t", CONFIG)
    harness.drain()

    spans = recorded.finished()

    assert {span.context.trace_id for span in spans} == {spans[0].context.trace_id}
    names = [span.name for span in spans]
    assert names.count("step.run") == 3
    assert names.count("llm.call") == 2
    assert names.count("tool.call") == 1
    assert names.count("task.start") == 1


def test_a_fork_continues_from_its_own_request_and_is_a_separate_trace(
    clean_db: SessionFactory, recorded: Recorded
) -> None:
    harness = harness_for(clean_db, recorded, [text_reply(), text_reply("fork")])
    _, main = start_task(harness.deps, harness.user.id, "t", CONFIG)
    harness.drain()
    main_trace = recorded.finished()[0].context.trace_id
    recorded.spans.clear()

    fork_from_edit(harness.deps, harness.user.id, harness.nodes(main)[1].id, {"text": "other"})
    harness.drain()

    spans = recorded.finished()
    assert {span.context.trace_id for span in spans} != {main_trace}
    assert len({span.context.trace_id for span in spans}) == 1
    assert "fork.create" in [span.name for span in spans]


def test_a_trace_survives_a_rate_limit_wait(clean_db: SessionFactory, recorded: Recorded) -> None:
    harness = harness_for(clean_db, recorded, [ProviderRateLimitedError(5), text_reply()])
    start_task(harness.deps, harness.user.id, "t", CONFIG)
    harness.drain()
    harness.clock.value = harness.clock.value.replace(second=30)
    assert sweep(harness.deps).dispatched == 1
    harness.drain()

    spans = recorded.finished()
    assert [span.name for span in spans].count("step.run") == 2
    assert len({span.context.trace_id for span in spans}) == 1


def test_spans_never_contain_task_text_or_tool_arguments(
    clean_db: SessionFactory, recorded: Recorded
) -> None:
    harness = harness_for(clean_db, recorded, [tool_reply(SECRET_TEXT), text_reply(SECRET_TEXT)])
    _, main = start_task(harness.deps, harness.user.id, SECRET_TEXT, CONFIG)
    harness.drain()
    fork_from_edit(harness.deps, harness.user.id, harness.nodes(main)[1].id, {"text": "x"})
    harness.queue.messages.clear()
    assert not any(SECRET_TEXT in span.to_json() for span in recorded.finished())


def test_failures_dont_put_error_text_in_spans(
    clean_db: SessionFactory, recorded: Recorded
) -> None:
    harness = harness_for(clean_db, recorded, [ValueError(SECRET_TEXT)])
    start_task(harness.deps, harness.user.id, "t", CONFIG)
    harness.drain()
    assert not any(SECRET_TEXT in span.to_json() for span in recorded.finished())


def test_metrics_cover_steps_tokens_queue_wait_and_outcomes(
    clean_db: SessionFactory, recorded: Recorded
) -> None:
    harness = harness_for(
        clean_db, recorded, [tool_reply(tokens=10), text_reply(tokens=5), text_reply()]
    )
    _, main = start_task(harness.deps, harness.user.id, "t", CONFIG)
    harness.drain()
    fork_from_edit(harness.deps, harness.user.id, harness.nodes(main)[1].id, {"text": "other"})
    harness.drain()

    metrics = recorded.metrics()

    def by(name: str, **labels: str) -> list[Any]:
        return [
            point
            for point in metrics[name]
            if all(point.attributes.get(key) == value for key, value in labels.items())
        ]

    assert by("step.duration", action="llm", outcome="executed")[0].count == 3
    assert by("step.duration", action="tool", outcome="executed")[0].count == 1
    assert metrics["queue.wait"][0].count == 4
    assert by("llm.tokens", direction="input")[0].value == 25
    assert by("branch.finished", status="completed")[0].value == 2
    assert by("admission.accepted", kind="tree")[0].value == 1
    assert by("admission.accepted", kind="fork")[0].value == 1
    assert by("fork.duration", node_type="user_msg")[0].count == 1
    assert metrics["task.start_duration"][0].count == 1


def test_skipped_duplicates_count_as_outcomes_without_a_duration(
    clean_db: SessionFactory, recorded: Recorded
) -> None:
    harness = harness_for(clean_db, recorded, [tool_reply(), text_reply()])
    start_task(harness.deps, harness.user.id, "t", CONFIG)
    message = harness.queue.messages[0]
    harness.queue.enqueue(message)
    harness.drain()
    metrics = recorded.metrics()
    outcomes = {point.attributes["outcome"]: point.value for point in metrics["step.outcome"]}
    assert outcomes["skipped_duplicate"] == 1
    assert sum(point.count for point in metrics["step.duration"]) == 3
