"""MCP server exposing the agent's tools over stateless HTTP."""

from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.gateway.tokens import InvalidToolTokenError, read_tool_token
from app.ports import JsonDict, ToolContext
from app.tools.gateway import GatewayTools


def build_server(tools: GatewayTools, token_secret: str) -> MCPServer:
    server = MCPServer("agent-timelines-tools")

    def caller(ctx: Context[Any, Any]) -> ToolContext:
        request = ctx.request_context.request
        header = request.headers.get("authorization", "") if request is not None else ""
        try:
            return read_tool_token(header.removeprefix("Bearer "), token_secret)
        except InvalidToolTokenError as exc:
            raise ToolError("the call isn't authorized") from exc

    def run(ctx: Context[Any, Any], name: str, arguments: JsonDict) -> str:
        context = caller(ctx)
        request = ctx.request_context.request
        parent = dict(request.headers) if request is not None else None
        attributes = {"tool.name": name, "branch.id": str(context.branch_id)}
        with tools.telemetry.span("gateway.tool", attributes, parent=parent):
            outcome = tools.execute(context, name, arguments)
        if outcome.is_error:
            raise ToolError(outcome.content)
        return outcome.content

    @server.tool(description="Search the web. Returns titles, URLs and snippets.")
    def web_search(query: str, ctx: Context[Any, Any]) -> str:
        return run(ctx, "web_search", {"query": query})

    @server.tool(description="Read the text of a web page by URL.")
    def page_fetch(url: str, ctx: Context[Any, Any]) -> str:
        return run(ctx, "page_fetch", {"url": url})

    @server.tool(description="Search the main knowledge base.")
    def kb_search(query: str, ctx: Context[Any, Any]) -> str:
        return run(ctx, "kb_search", {"query": query})

    @server.tool(description="Evaluate an arithmetic expression.")
    def calculator(expression: str, ctx: Context[Any, Any]) -> str:
        return run(ctx, "calculator", {"expression": expression})

    @server.tool(description="Save a note to this branch's scratchpad.")
    def scratchpad_write(text: str, ctx: Context[Any, Any]) -> str:
        return run(ctx, "scratchpad_write", {"text": text})

    @server.tool(description="Search the notes saved in this branch's scratchpad.")
    def scratchpad_search(query: str, ctx: Context[Any, Any]) -> str:
        return run(ctx, "scratchpad_search", {"query": query})

    return server


def build_app(tools: GatewayTools, token_secret: str) -> Starlette:
    server = build_server(tools, token_secret)
    mcp_app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        # Only other containers can reach this, and each call carries a signed token.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    mcp_app.routes.append(Route("/healthz", healthz))
    return mcp_app
