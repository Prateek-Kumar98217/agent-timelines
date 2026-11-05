from typing import Any
from uuid import uuid4

from app.db.models import Node, NodeType
from app.domain.messages import estimate_tokens, messages_from_path


def node(node_type: NodeType, parent: Node | None = None, **payload: Any) -> Node:
    return Node(
        id=uuid4(), type=node_type.value, payload=payload, parent_id=parent.id if parent else None
    )


def test_path_becomes_messages_in_order() -> None:
    root = node(NodeType.ROOT, task="t")
    user = node(NodeType.USER_MSG, root, text="question")
    llm = node(
        NodeType.LLM_OUTPUT,
        user,
        text="let me look",
        tool_call={"name": "search", "arguments": {"query": "old"}},
    )
    call = node(NodeType.TOOL_CALL, llm, name="search", arguments={"query": "old"})
    result = node(NodeType.TOOL_RESULT, call, name="search", content="found", is_error=False)
    final = node(NodeType.LLM_OUTPUT, result, text="answer")

    assert messages_from_path([root, user, llm, call, result, final]) == [
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": "let me look",
            "tool_call": {
                "id": str(call.id),
                "name": "search",
                "arguments": {"query": "old"},
            },
        },
        {
            "role": "tool",
            "tool_call_id": str(call.id),
            "name": "search",
            "content": "found",
            "is_error": False,
        },
        {"role": "assistant", "content": "answer"},
    ]


def test_assistant_message_uses_the_parameters_that_ran_not_the_original_choice() -> None:
    llm = node(
        NodeType.LLM_OUTPUT, tool_call={"name": "search", "arguments": {"query": "original"}}
    )
    edited_call = node(NodeType.TOOL_CALL, llm, name="search", arguments={"query": "edited"})
    messages = messages_from_path([llm, edited_call])
    assert messages[0]["tool_call"]["arguments"] == {"query": "edited"}


def test_estimate_tokens_is_roughly_a_quarter_of_characters() -> None:
    assert estimate_tokens([{"role": "user", "content": "x" * 400}]) == 100
