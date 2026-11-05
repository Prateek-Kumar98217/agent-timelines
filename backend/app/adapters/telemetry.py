"""OpenTelemetry traces, metrics and logs, sent over OTLP."""

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from opentelemetry import context as otel_context
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Status, StatusCode

from app.ports import JsonDict

METRIC_EXPORT_MILLIS = 5_000
# Seconds, from a quick tool call to a slow local model.
DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 60, 120, 300)

# Every metric is declared here, so an unbounded name or label can't slip in. Branch and tree IDs
# belong on spans. As metric labels they'd create a new series for every branch.
HISTOGRAMS = {
    "step.duration": ("s", {"action", "outcome"}),
    "llm.duration": ("s", {"provider"}),
    "tool.duration": ("s", {"tool", "is_error"}),
    "tool.client_duration": ("s", {"tool"}),
    "fork.duration": ("s", {"node_type"}),
    "task.start_duration": ("s", set()),
    "queue.wait": ("s", set()),
}
COUNTERS = {
    "step.outcome": {"outcome"},
    "llm.tokens": {"provider", "direction"},
    "tool.cache": {"tool", "result"},
    "admission.accepted": {"kind"},
    "admission.rejected": {"reason"},
    "branch.finished": {"status"},
}


class OtelTelemetry:
    def __init__(
        self,
        service_name: str,
        endpoint: str | None = None,
        *,
        span_exporter: SpanExporter | None = None,
        metric_reader: MetricReader | None = None,
        export_logs: bool = True,
    ) -> None:
        # Each process needs its own ID. Without one, Celery's worker processes report as a single
        # source, and their cumulative counters overwrite each other.
        resource = Resource.create(
            {"service.name": service_name, "service.instance.id": uuid4().hex}
        )
        self._tracer_provider = TracerProvider(resource=resource)
        self._tracer_provider.add_span_processor(
            # An injected exporter is for tests, which need each span delivered immediately.
            SimpleSpanProcessor(span_exporter)
            if span_exporter
            else BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"), schedule_delay_millis=1_000
            )
        )
        reader = metric_reader or PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics"),
            export_interval_millis=METRIC_EXPORT_MILLIS,
        )
        self._meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        self._tracer = self._tracer_provider.get_tracer("agent_timelines")
        meter = self._meter_provider.get_meter("agent_timelines")
        self._histograms = {
            name: meter.create_histogram(
                name, unit=unit, explicit_bucket_boundaries_advisory=DURATION_BUCKETS
            )
            for name, (unit, _labels) in HISTOGRAMS.items()
        }
        self._counters = {name: meter.create_counter(name) for name in COUNTERS}
        if endpoint and export_logs:
            self._send_logs(resource, endpoint)

    @staticmethod
    def _send_logs(resource: Resource, endpoint: str) -> None:
        provider = LoggerProvider(resource=resource)
        provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint}/v1/logs"))
        )
        logging.getLogger().addHandler(LoggingHandler(level=logging.INFO, logger_provider=provider))
        logging.getLogger().setLevel(logging.INFO)
        # The exporter's own HTTP calls would otherwise be logged, and then exported, forever.
        for noisy in ("urllib3", "httpx", "httpcore", "httpcore2", "httpx2"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    @contextmanager
    def span(
        self,
        name: str,
        attributes: JsonDict | None = None,
        parent: dict[str, str] | None = None,
    ) -> Iterator[None]:
        context = extract(parent) if parent else None
        with self._tracer.start_as_current_span(
            name,
            context=context,
            attributes=attributes,
            # Exception text can quote provider responses, so only the type is recorded.
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield
            except BaseException as exc:
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise

    def record(self, metric: str, value: float, attributes: JsonDict | None = None) -> None:
        if metric in self._histograms:
            allowed: Sequence[str] = tuple(HISTOGRAMS[metric][1])
            self._histograms[metric].record(value, _only(attributes, allowed))
        elif metric in self._counters:
            self._counters[metric].add(value, _only(attributes, tuple(COUNTERS[metric])))
        else:
            raise ValueError(f"unknown metric: {metric}")

    def trace_context(self) -> dict[str, str]:
        carrier: dict[str, str] = {}
        inject(carrier, context=otel_context.get_current())
        return carrier

    @property
    def tracer_provider(self) -> TracerProvider:
        return self._tracer_provider

    def flush(self) -> None:
        self._tracer_provider.force_flush()
        self._meter_provider.force_flush()


def _only(attributes: JsonDict | None, allowed: Sequence[str]) -> dict[str, Any]:
    return {key: value for key, value in (attributes or {}).items() if key in allowed}
