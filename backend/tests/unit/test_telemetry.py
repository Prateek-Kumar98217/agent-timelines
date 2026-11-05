from typing import Any

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.adapters.memory import NoopTelemetry
from app.adapters.telemetry import OtelTelemetry


@pytest.fixture
def spans() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def telemetry(spans: InMemorySpanExporter, reader: InMemoryMetricReader) -> OtelTelemetry:
    return OtelTelemetry("test", span_exporter=spans, metric_reader=reader, export_logs=False)


def finished(telemetry: OtelTelemetry, spans: InMemorySpanExporter) -> list[Any]:
    telemetry.flush()
    return list(spans.get_finished_spans())


def metric_points(reader: InMemoryMetricReader) -> dict[str, list[Any]]:
    data = reader.get_metrics_data()
    assert data is not None
    points: dict[str, list[Any]] = {}
    for resource_metrics in data.resource_metrics:
        for scope in resource_metrics.scope_metrics:
            for metric in scope.metrics:
                points[metric.name] = list(metric.data.data_points)
    return points


def test_spans_nest_in_one_process(telemetry: OtelTelemetry, spans: InMemorySpanExporter) -> None:
    with telemetry.span("outer", {"branch_id": "b"}), telemetry.span("inner"):
        pass
    inner, outer = finished(telemetry, spans)
    assert inner.parent.span_id == outer.context.span_id
    assert outer.attributes["branch_id"] == "b"


def test_a_trace_continues_in_another_process(spans: InMemorySpanExporter) -> None:
    sender = OtelTelemetry("api", span_exporter=spans, metric_reader=InMemoryMetricReader())
    receiver = OtelTelemetry("worker", span_exporter=spans, metric_reader=InMemoryMetricReader())
    with sender.span("send"):
        carried = sender.trace_context()
    assert "traceparent" in carried
    with receiver.span("receive", parent=carried):
        pass
    sender.flush()
    receiver.flush()
    by_name = {span.name: span for span in spans.get_finished_spans()}
    assert by_name["receive"].context.trace_id == by_name["send"].context.trace_id
    parent = by_name["receive"].parent
    assert parent is not None
    assert parent.span_id == by_name["send"].context.span_id
    assert by_name["send"].resource.attributes["service.name"] == "api"
    assert by_name["receive"].resource.attributes["service.name"] == "worker"


def test_each_process_has_its_own_instance_id(spans: InMemorySpanExporter) -> None:
    ids = set()
    for _ in range(2):
        telemetry = OtelTelemetry("w", span_exporter=spans, metric_reader=InMemoryMetricReader())
        with telemetry.span("s"):
            pass
        telemetry.flush()
    for span in spans.get_finished_spans():
        ids.add(span.resource.attributes["service.instance.id"])
    assert len(ids) == 2


def test_errors_record_only_the_type_never_the_message(
    telemetry: OtelTelemetry, spans: InMemorySpanExporter
) -> None:
    secret = "Incorrect API key provided: gsk-secret"
    with pytest.raises(ValueError, match="secret"), telemetry.span("failing"):
        raise ValueError(secret)
    span = finished(telemetry, spans)[0]
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "ValueError"
    assert span.events == ()
    assert "gsk-secret" not in span.to_json()


def test_histograms_and_counters_are_recorded(
    telemetry: OtelTelemetry, reader: InMemoryMetricReader
) -> None:
    telemetry.record("step.duration", 0.5, {"action": "llm", "outcome": "executed"})
    telemetry.record("admission.rejected", 1, {"reason": "queue_full"})
    points = metric_points(reader)
    assert points["step.duration"][0].sum == 0.5
    assert dict(points["step.duration"][0].attributes) == {"action": "llm", "outcome": "executed"}
    assert points["admission.rejected"][0].value == 1


def test_unknown_labels_are_dropped_so_series_stay_bounded(
    telemetry: OtelTelemetry, reader: InMemoryMetricReader
) -> None:
    telemetry.record("step.outcome", 1, {"outcome": "executed", "branch_id": "unbounded-id"})
    assert dict(metric_points(reader)["step.outcome"][0].attributes) == {"outcome": "executed"}


def test_unknown_metrics_are_refused(telemetry: OtelTelemetry) -> None:
    with pytest.raises(ValueError, match="unknown metric"):
        telemetry.record("made.up", 1)


def test_noop_telemetry_is_safe_to_call() -> None:
    noop = NoopTelemetry()
    with noop.span("s", {"a": 1}, parent={"traceparent": "x"}):
        noop.record("anything", 1)
    assert noop.trace_context() == {}
