"""Turns a root-to-leaf path of nodes into the message list the LLM sees."""

from collections.abc import Iterable
from typing import Any

from app.db.models import Node, NodeType

Message = dict[str, Any]


def message_for_node(node: Node, pending_text: str | None = None) -> Message | None:
    """Return the node's message, or None if it contributes none on its own.

    An `llm_output` that chose a tool yields nothing itself. Its `tool_call` child carries the
    message, because that child holds the parameters that really ran, including after an edit.
    """
    payload = node.payload
    match node.type:
        case NodeType.USER_MSG:
            return {"role": "user", "content": payload["text"]}
        case NodeType.LLM_OUTPUT:
            if payload.get("tool_call") is not None:
                return None
            return {"role": "assistant", "content": payload.get("text") or ""}
        case NodeType.TOOL_CALL:
            return {
                "role": "assistant",
                "content": pending_text or "",
                "tool_call": {
                    "id": str(node.id),
                    "name": payload["name"],
                    "arguments": payload["arguments"],
                },
            }
        case NodeType.TOOL_RESULT:
            return {
                "role": "tool",
                "tool_call_id": str(node.parent_id),
                "name": payload["name"],
                "content": payload["content"],
                "is_error": payload.get("is_error", False),
            }
        case _:
            return None


def required_message(node: Node, pending_text: str | None = None) -> Message:
    message = message_for_node(node, pending_text)
    if message is None:
        raise ValueError(f"{node.type} node {node.id} has no message of its own")
    return message


def messages_from_path(path: Iterable[Node]) -> list[Message]:
    messages: list[Message] = []
    pending_text: str | None = None
    for node in path:
        message = message_for_node(node, pending_text)
        if node.type == NodeType.LLM_OUTPUT and message is None:
            pending_text = node.payload.get("text")
            continue
        pending_text = None
        if message is not None:
            messages.append(message)
    return messages


def estimate_tokens(messages: Iterable[Message]) -> int:
    """A rough count, used only to avoid calls that clearly can't fit the budget."""
    return sum(len(str(message.get("content", ""))) for message in messages) // 4
