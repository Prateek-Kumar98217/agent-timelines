"""ASGI entry point: `uvicorn app.gateway.asgi:app`."""

from app.bootstrap import get_runtime
from app.gateway.server import build_app
from app.tools.gateway import GatewayTools

_runtime = get_runtime()
app = build_app(
    GatewayTools(
        session_factory=_runtime.session_factory,
        cache=_runtime.adapters.cache,
        browser=_runtime.adapters.browser,
        embedder=_runtime.adapters.embedder,
        cache_ttl_seconds=_runtime.settings.cache_ttl_seconds,
        telemetry=_runtime.adapters.telemetry,
    ),
    _runtime.settings.gateway_token_secret,
)
