"""Validation of tool calls against a tree's declared tools."""

from typing import Any

from jsonschema import Draft202012Validator

from app.domain.config import TreeConfig


def tool_call_problem(config: TreeConfig, name: str, arguments: dict[str, Any]) -> str | None:
    """Describe what's wrong with a tool call, or return None if it's valid."""
    tool = config.tool(name)
    if tool is None:
        allowed = ", ".join(spec.name for spec in config.tools) or "none"
        return f"unknown tool '{name}'; allowed tools: {allowed}"
    errors = sorted(
        Draft202012Validator(tool.parameters).iter_errors(arguments), key=lambda e: list(e.path)
    )
    if errors:
        return f"invalid arguments for '{name}': {errors[0].message}"
    return None
