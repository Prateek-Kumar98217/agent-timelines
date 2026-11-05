"""One-step LangGraph agent.

Each invocation routes to a single transition and ends. There is deliberately no checkpointer and
no loop: Postgres holds all state between steps, and no process stays alive while a branch waits.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from app.domain.config import TreeConfig
from app.domain.tools import tool_call_problem
from app.ports import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ToolContext,
    ToolExecutor,
    ToolOutcome,
)


@dataclass(frozen=True)
class PendingTool:
    name: str
    arguments: dict[str, Any]
    context: ToolContext


class StepState(TypedDict):
    action: str
    config: TreeConfig
    messages: Sequence[dict[str, Any]]
    owner_id: NotRequired[UUID]
    force_final: NotRequired[bool]
    pending_tool: NotRequired[PendingTool]
    llm_response: NotRequired[LLMResponse]
    tool_outcome: NotRequired[ToolOutcome]


def build_step_graph(llm: LLMProvider, tools: ToolExecutor) -> Any:
    def call_llm(state: StepState) -> dict[str, Any]:
        config = state["config"]
        force_final = state.get("force_final", False)
        request = LLMRequest(
            messages=[{"role": "system", "content": config.system_prompt}, *state["messages"]],
            tools=[] if force_final else [tool.model_dump() for tool in config.tools],
            force_final=force_final,
            provider=config.provider,
            model=config.model,
            temperature=config.temperature,
            owner_id=state.get("owner_id"),
        )
        return {"llm_response": llm.complete(request)}

    def call_tool(state: StepState) -> dict[str, Any]:
        pending = state["pending_tool"]
        problem = tool_call_problem(state["config"], pending.name, pending.arguments)
        if problem is not None:
            return {"tool_outcome": ToolOutcome(problem, is_error=True)}
        try:
            outcome = tools.execute(pending.context, pending.name, pending.arguments)
        except Exception as exc:
            # The agent sees tool failures and can work around them.
            outcome = ToolOutcome(f"{pending.name} failed: {exc}", is_error=True)
        return {"tool_outcome": outcome}

    graph = StateGraph(StepState)
    graph.add_node("llm", call_llm)
    graph.add_node("tool", call_tool)
    graph.add_conditional_edges(START, lambda state: state["action"], ["llm", "tool"])
    graph.add_edge("llm", END)
    graph.add_edge("tool", END)
    return graph.compile()
