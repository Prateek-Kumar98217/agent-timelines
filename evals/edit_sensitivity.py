"""Measures which kinds of edit actually change outcomes, against a no-op baseline.

LLMs vary between runs with no edit at all, so an edit type only matters if it diverges more than a
fork that changes nothing. Progress is saved after every branch, so a stopped run can resume.

Start the stack with Ollama and generous limits, then run on the host with the same limits:
    export APP_OLLAMA_URL=http://ollama:11434 APP_LOCAL_TOKENS_PER_DAY=10000000
    docker compose --profile ollama up -d --build --wait api worker beat ollama
    uv run python evals/edit_sensitivity.py --model qwen3:4b
"""

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import select
from tasks import TASKS, TASKS_BY_ID, EvalTask, fact_score

from app.bootstrap import Runtime, get_runtime
from app.db.models import NON_COMPLETED_STATUSES, Branch, BranchScore, Node
from app.domain.compare import compare_branches
from app.domain.config import EVAL_TOOLS, TreeConfig
from app.domain.engine import start_task
from app.domain.forking import ForkError, fork_from_edit
from app.domain.limits import AdmissionRejectedError
from app.factory import LOCAL_USER_ID

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "eval" / "runs.jsonl"
UNFINISHED = {status.value for status in NON_COMPLETED_STATUSES}
# Each class names a node to edit and how, given the task. "noop" forks repeat the same content.
EDIT_CLASSES = (
    "noop_user_msg",
    "noop_tool_result",
    "user_paraphrase",
    "user_other_task",
    "result_contradiction",
    "result_irrelevant",
    "result_error",
    "call_other_query",
)
IRRELEVANT_RESULT = "Lake Victoria: Lake Victoria is a lake in Africa."
ERROR_RESULT = "kb_search failed: the search service is unavailable"


@dataclass
class Run:
    task_id: str
    edit_class: str
    repeat: int
    branch_id: str
    source_branch_id: str | None
    model: str
    status: str
    reason: str | None
    turns: int
    tokens: int
    input_tokens: int
    output_tokens: int
    seconds: float
    tools: list[str]
    final_text: str | None
    fact_score: float
    source_fact_score: float | None
    final_similarity: float | None
    tool_call_overlap: float | None


def nodes_of(runtime: Runtime, branch_id: UUID) -> list[Node]:
    with runtime.session_factory() as session:
        return list(
            session.execute(
                select(Node).where(Node.branch_id == branch_id).order_by(Node.seq)
            ).scalars()
        )


def wait_finished(runtime: Runtime, branch_ids: list[UUID], timeout: float = 1800) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with runtime.session_factory() as session:
            statuses = session.execute(
                select(Branch.status).where(Branch.id.in_(branch_ids))
            ).scalars()
            if not any(status in UNFINISHED for status in statuses):
                return
        time.sleep(1)
    raise TimeoutError(f"branches didn't finish: {branch_ids}")


def choose_edit(task: EvalTask, edit_class: str, nodes: list[Node]) -> tuple[Node, dict[str, Any]]:
    user_msg = next(node for node in nodes if node.type == "user_msg")
    result = next(
        (n for n in nodes if n.type == "tool_result" and n.payload["name"] == "kb_search"), None
    )
    call = next(
        (n for n in nodes if n.type == "tool_call" and n.payload["name"] == "kb_search"), None
    )
    match edit_class:
        case "noop_user_msg":
            return user_msg, {"text": user_msg.payload["text"]}
        case "user_paraphrase":
            return user_msg, {"text": task.paraphrase}
        case "user_other_task":
            return user_msg, {"text": task.other_task}
    if result is None or call is None:
        raise ValueError("the source run made no kb_search call")
    match edit_class:
        case "noop_tool_result":
            payload = result.payload
            return result, {"content": payload["content"], "is_error": payload["is_error"]}
        case "result_contradiction":
            return result, {"content": task.contradiction}
        case "result_irrelevant":
            return result, {"content": IRRELEVANT_RESULT}
        case "result_error":
            return result, {"content": ERROR_RESULT, "is_error": True}
        case "call_other_query":
            return call, {"arguments": {"query": task.other_query}}
    raise ValueError(f"unknown edit class: {edit_class}")


def summarize(
    runtime: Runtime,
    task: EvalTask,
    edit_class: str,
    repeat: int,
    branch_id: UUID,
    source: Run | None,
    model: str,
    started: float,
) -> Run:
    nodes = nodes_of(runtime, branch_id)
    with runtime.session_factory() as session:
        branch = session.get_one(Branch, branch_id)
        comparison = (
            compare_branches(
                session,
                UUID(source.branch_id),
                branch_id,
                runtime.adapters.embedder,
                runtime.settings.convergence_threshold,
            )
            if source
            else None
        )
        final = comparison.b.final_text if comparison else None
        if comparison is None:
            last = nodes[-1]
            if last.type == "llm_output" and last.payload.get("tool_call") is None:
                final = last.payload.get("text")
        score = fact_score(task, final)
        session.merge(
            BranchScore(branch_id=branch_id, metric="facts", score=score, scorer="fact_check")
        )
        session.commit()
        tools = [n.payload["name"] for n in nodes if n.type == "tool_call"]
        return Run(
            task_id=task.id,
            edit_class=edit_class,
            repeat=repeat,
            branch_id=str(branch_id),
            source_branch_id=source.branch_id if source else None,
            model=model,
            status=branch.status,
            reason=branch.status_reason,
            turns=branch.turns_used,
            tokens=branch.input_tokens + branch.output_tokens,
            input_tokens=branch.input_tokens,
            output_tokens=branch.output_tokens,
            seconds=time.monotonic() - started,
            tools=tools,
            final_text=final,
            fact_score=score,
            source_fact_score=source.fact_score if source else None,
            final_similarity=comparison.final_similarity if comparison else None,
            tool_call_overlap=comparison.tool_call_overlap if comparison else None,
        )


def save(run: Run, output: Path) -> None:
    similarity = run.final_similarity
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a") as file:
        file.write(json.dumps(asdict(run), ensure_ascii=False) + "\n")
    print(
        f"{run.task_id:11} {run.edit_class:21} r{run.repeat} {run.status:16} turns={run.turns:2} "
        f"tokens={run.tokens:6} {run.seconds:5.0f}s facts={run.fact_score:.2f} "
        f"sim={similarity if similarity is None else round(similarity, 3)}",
        flush=True,
    )


def load_runs(output: Path) -> dict[tuple[str, str, int], Run]:
    if not output.exists():
        return {}
    with output.open() as file:
        runs = [Run(**json.loads(line)) for line in file]
    return {(run.task_id, run.edit_class, run.repeat): run for run in runs}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--tasks", nargs="*", default=[task.id for task in TASKS])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    runtime = get_runtime()
    config = TreeConfig(tools=EVAL_TOOLS, provider=args.provider, model=args.model)
    done = load_runs(args.output)
    for task_id in args.tasks:
        task = TASKS_BY_ID[task_id]
        main_run = done.get((task.id, "main", 0))
        if main_run is None:
            started = time.monotonic()
            _, branch_id = start_task(runtime.deps, LOCAL_USER_ID, task.text, config)
            wait_finished(runtime, [branch_id])
            main_run = summarize(runtime, task, "main", 0, branch_id, None, args.model, started)
            save(main_run, args.output)
        nodes = nodes_of(runtime, UUID(main_run.branch_id))
        for edit_class in EDIT_CLASSES:
            for repeat in range(args.repeats):
                if (task.id, edit_class, repeat) in done:
                    continue
                try:
                    node, edit = choose_edit(task, edit_class, nodes)
                    started = time.monotonic()
                    branch_id = fork_from_edit(
                        runtime.deps,
                        LOCAL_USER_ID,
                        node.id,
                        edit,
                        allow_noop=edit_class.startswith("noop"),
                    )
                except (ValueError, ForkError, AdmissionRejectedError) as error:
                    print(f"{task.id} {edit_class}: skipped: {error}", flush=True)
                    continue
                wait_finished(runtime, [branch_id])
                save(
                    summarize(
                        runtime,
                        task,
                        edit_class,
                        repeat,
                        branch_id,
                        main_run,
                        args.model,
                        started,
                    ),
                    args.output,
                )


if __name__ == "__main__":
    main()
