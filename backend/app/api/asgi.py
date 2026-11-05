"""ASGI entry point: `uvicorn app.api.asgi:app`."""

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.adapters.telemetry import OtelTelemetry
from app.api.main import create_app
from app.bootstrap import get_runtime

_runtime = get_runtime()
app = create_app(_runtime)
if isinstance(_runtime.adapters.telemetry, OtelTelemetry):
    # The stream stays open for as long as a client watches, so it wouldn't make a useful span.
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_runtime.adapters.telemetry.tracer_provider,
        excluded_urls="healthz,/stream$",
    )
