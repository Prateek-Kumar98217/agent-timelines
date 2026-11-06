"""Run a scripted task, fork it at each editable node type, and print the resulting tree.

Uses the mock LLM and tools, so it needs only Postgres: `docker compose up -d --wait postgres`.
It creates a throwaway database and drops it afterwards.
"""

from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, select, text

from app.adapters.memory import (
    FixtureTools,
    HashEmbedder,
    InlineQueue,
    MemoryLiveEvents,
    ScriptedLLM,
)
from app.db.models import Branch, Node, User
from app.db.session import SessionFactory, make_session_factory
from app.domain.config import ToolSpec, TreeConfig
from app.domain.engine import EngineDeps, run_step, start_task
from app.domain.forking import fork_from_edit
from app.ports import LLMResponse, ToolCall, ToolContext, ToolOutcome, Usage
from app.settings import Settings

BACKEND = Path(__file__).resolve().parent.parent / "backend"
SEARCH = ToolSpec(
    name="search",
    description="Search the web",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)


def say(answer: str) -> LLMResponse:
    return LLMResponse(usage=Usage(40, 12), text=answer)


def search(query: str) -> LLMResponse:
    return LLMResponse(usage=Usage(30, 8), tool_call=ToolCall("search", {"query": query}))


def fake_search(_context: ToolContext, arguments: dict[str, str]) -> ToolOutcome:
    return ToolOutcome(f"Top result for '{arguments['query']}': 8,849 m")


def summary(node: Node) -> str:
    payload = node.payload
    match node.type:
        case "root":
            return f"task: {payload['task']}"
        case "user_msg":
            return payload["text"]
        case "llm_output":
            call = payload["tool_call"]
            return f"chooses {call['name']}" if call else f"answers: {payload['text']}"
        case "tool_call":
            return f"{payload['name']}({payload['arguments']})"
        case _:
            return payload["content"]


def print_tree(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        nodes = list(session.execute(select(Node).order_by(Node.created_at, Node.seq)).scalars())
        branches = {
            b.id: b for b in session.execute(select(Branch).order_by(Branch.created_at)).scalars()
        }
    names = {branch_id: f"B{i}" for i, branch_id in enumerate(branches)}
    children: dict[UUID | None, list[Node]] = {}
    for node in nodes:
        children.setdefault(node.parent_id, []).append(node)

    def walk(node: Node, depth: int) -> None:
        edited = "  <- edited" if node.edited_from_id else ""
        print(
            f"{'  ' * depth}[{names[node.branch_id]}] t{node.turn_index} {node.type}: "
            f"{summary(node)}{edited}"
        )
        for child in children.get(node.id, []):
            walk(child, depth + 1)

    walk(children[None][0], 0)
    print()
    for branch_id, branch in branches.items():
        tokens = branch.input_tokens + branch.output_tokens
        print(f"{names[branch_id]}: {branch.status}, {branch.turns_used} turns, {tokens} tokens")


def main() -> None:
    server = make_url(Settings().database_url)
    name = f"timelines_demo_{uuid4().hex[:8]}"
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = server.set(database=name).render_as_string(hide_password=False)
    try:
        config = Config(BACKEND / "alembic.ini")
        config.attributes["url"] = url
        command.upgrade(config, "head")
        session_factory = make_session_factory(url)
        queue = InlineQueue()
        deps = EngineDeps(
            session_factory=session_factory,
            queue=queue,
            llm=ScriptedLLM(
                [
                    search("height of Everest"),
                    say("Everest is 8,849 m tall."),
                    say("K2 is 8,611 m tall."),
                    say("Everest is about 29,032 ft tall."),
                    say("The source says 9,000 m, so Everest is 9,000 m tall."),
                ]
            ),
            tools=FixtureTools({"search": fake_search}),
            live_events=MemoryLiveEvents(),
            embedder=HashEmbedder(),
        )
        with session_factory() as session:
            user = User(id=uuid4(), kind="local")
            session.add(user)
            session.commit()

        def drain() -> None:
            for message in queue.drain():
                run_step(deps, message)

        tree_config = TreeConfig(tools=[SEARCH])
        _, main_branch = start_task(deps, user.id, "How tall is Everest?", tree_config)
        drain()
        with session_factory() as session:
            nodes = list(
                session.execute(
                    select(Node).where(Node.branch_id == main_branch).order_by(Node.seq)
                ).scalars()
            )
        _root, user_msg, llm_tool, call, result, _final = nodes

        fork_from_edit(deps, user.id, user_msg.id, {"text": "How tall is K2?"})
        drain()
        fork_from_edit(
            deps, user.id, call.id, {"arguments": {"query": "height of Everest in feet"}}
        )
        drain()
        fork_from_edit(deps, user.id, result.id, {"content": "Top result: 9,000 m"})
        drain()
        fork_from_edit(deps, user.id, llm_tool.id, {"text": "I already know: about 8.8 km."})
        drain()
        print_tree(session_factory)
        session_factory.kw["bind"].dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


if __name__ == "__main__":
    main()
