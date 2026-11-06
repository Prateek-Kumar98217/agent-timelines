"""Measures real model and tool step times from the experiment's nodes into docs/eval/steps.json.

Run it against the database that holds the experiment's branches.
"""

import json
from pathlib import Path
from uuid import UUID

from report import load_runs, percentile
from sqlalchemy import select

from app.db.models import Node, NodeType
from app.db.session import make_session_factory
from app.settings import get_settings

EVAL_DIR = Path(__file__).resolve().parent.parent / "docs" / "eval"


def summary(values: list[float]) -> dict[str, float]:
    return {
        "steps": len(values),
        "p50": percentile(values, 0.5),
        "p95": percentile(values, 0.95),
    }


def main() -> None:
    branches = [UUID(run["branch_id"]) for run in load_runs(EVAL_DIR / "runs.jsonl")]
    session_factory = make_session_factory(get_settings().database_url)
    with session_factory() as session:
        rows = session.execute(select(Node).where(Node.branch_id.in_(branches))).scalars().all()
        parents = {node.id: node for node in rows}
        missing = {node.parent_id for node in rows if node.parent_id} - set(parents)
        for node in session.execute(select(Node).where(Node.id.in_(missing))).scalars():
            parents[node.id] = node
    model, tool = [], []
    for node in rows:
        parent = parents.get(node.parent_id) if node.parent_id else None
        # An edited node is written at the fork, so the gap before it isn't a step.
        if parent is None or node.edited_from_id is not None:
            continue
        seconds = (node.created_at - parent.created_at).total_seconds()
        if node.type == NodeType.LLM_OUTPUT:
            model.append(seconds)
        elif node.type == NodeType.TOOL_RESULT:
            tool.append(seconds)
    result = {"model": summary(model), "tool": summary(tool)}
    (EVAL_DIR / "steps.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
