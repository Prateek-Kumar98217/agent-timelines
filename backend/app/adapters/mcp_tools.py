"""Calls the agent's tools through the MCP gateway."""

import asyncio
import time

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from app.gateway.tokens import mint_tool_token
from app.ports import JsonDict, NoopTelemetry, Telemetry, ToolContext, ToolOutcome


class McpToolExecutor:
    """Opens one short connection per call. The server is stateless, and workers hold nothing."""

    def __init__(
        self,
        url: str,
        token_secret: str,
        timeout_seconds: float = 60.0,
        telemetry: Telemetry | None = None,
    ) -> None:
        self._url = url
        self._secret = token_secret
        self._timeout = timeout_seconds
        self._telemetry = telemetry or NoopTelemetry()

    def execute(self, context: ToolContext, name: str, arguments: JsonDict) -> ToolOutcome:
        started = time.perf_counter()
        outcome = asyncio.run(self._call(context, name, arguments))
        self._telemetry.record(
            "tool.client_duration", time.perf_counter() - started, {"tool": name}
        )
        return outcome

    async def _call(self, context: ToolContext, name: str, arguments: JsonDict) -> ToolOutcome:
        headers = {
            **self._telemetry.trace_context(),
            "Authorization": f"Bearer {mint_tool_token(context, self._secret)}",
        }
        async with httpx2.AsyncClient(headers=headers, timeout=self._timeout) as http_client:
            transport = streamable_http_client(self._url, http_client=http_client)
            async with Client(transport) as client:
                result = await client.call_tool(name, arguments)
        text = "\n".join(item.text for item in result.content if isinstance(item, TextContent))
        return ToolOutcome(text, is_error=bool(result.is_error))
