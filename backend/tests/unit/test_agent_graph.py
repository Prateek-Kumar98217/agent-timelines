from typing import Any
from uuid import uuid4

import pytest

from app.adapters.memory import FixtureTools, ScriptedLLM
from app.agent.graph import PendingTool, build_step_graph
from app.domain.config import ToolSpec, TreeConfig
from app.domain.tools import tool_call_problem
from app.ports import (
    BrowserBlockedError,
    LLMResponse,
    ProviderRateLimitedError,
    ToolCall,
    ToolContext,
    ToolOutcome,
    Usage,
)

SEARCH = ToolSpec(
    name="search",
    description="Search",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)
CONFIG = TreeConfig(system_prompt="sys", tools=[SEARCH])


def pending(name: str = "search", **arguments: Any) -> PendingTool:
    return PendingTool(name, arguments, ToolContext(uuid4(), uuid4()))


def test_llm_step_sends_system_prompt_and_tools_and_calls_nothing_else() -> None:
    response = LLMResponse(usage=Usage(1, 1), tool_call=ToolCall("search", {"query": "q"}))
    llm, tools = ScriptedLLM([response]), FixtureTools()
    result = build_step_graph(llm, tools).invoke(
        {"action": "llm", "config": CONFIG, "messages": [{"role": "user", "content": "hi"}]}
    )
    assert result["llm_response"] is response
    assert tools.calls == []
    request = llm.requests[0]
    assert request.messages[0] == {"role": "system", "content": "sys"}
    assert [tool["name"] for tool in request.tools] == ["search"]


def test_force_final_offers_no_tools() -> None:
    llm = ScriptedLLM([LLMResponse(usage=Usage(1, 1), text="done")])
    build_step_graph(llm, FixtureTools()).invoke(
        {"action": "llm", "config": CONFIG, "messages": [], "force_final": True}
    )
    assert llm.requests[0].tools == []
    assert llm.requests[0].force_final


def test_tool_step_runs_the_tool_and_not_the_llm() -> None:
    llm, tools = ScriptedLLM([]), FixtureTools()
    tool = pending(query="q")
    result = build_step_graph(llm, tools).invoke(
        {"action": "tool", "config": CONFIG, "messages": [], "pending_tool": tool}
    )
    assert not result["tool_outcome"].is_error
    assert llm.requests == []
    assert tools.calls == [(tool.context, "search", {"query": "q"})]


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        (pending("delete_everything", query="q"), "unknown tool"),
        (pending(query=5), "invalid arguments"),
        (pending(), "invalid arguments"),
    ],
)
def test_bad_tool_calls_become_errors_without_executing(tool: PendingTool, expected: str) -> None:
    tools = FixtureTools()
    result = build_step_graph(ScriptedLLM([]), tools).invoke(
        {"action": "tool", "config": CONFIG, "messages": [], "pending_tool": tool}
    )
    assert result["tool_outcome"].is_error
    assert expected in result["tool_outcome"].content
    assert tools.calls == []


def test_tool_failures_become_errors_the_agent_can_see() -> None:
    def blocked(_context: ToolContext, _arguments: dict[str, Any]) -> ToolOutcome:
        raise BrowserBlockedError("captcha")

    result = build_step_graph(ScriptedLLM([]), FixtureTools({"search": blocked})).invoke(
        {"action": "tool", "config": CONFIG, "messages": [], "pending_tool": pending(query="q")}
    )
    assert result["tool_outcome"] == ToolOutcome("search failed: captcha", is_error=True)


def test_provider_rate_limit_propagates_so_the_branch_can_wait() -> None:
    llm = ScriptedLLM([ProviderRateLimitedError(7)])
    with pytest.raises(ProviderRateLimitedError):
        build_step_graph(llm, FixtureTools()).invoke(
            {"action": "llm", "config": CONFIG, "messages": []}
        )


def test_tool_call_problem_accepts_valid_calls() -> None:
    assert tool_call_problem(CONFIG, "search", {"query": "q"}) is None
