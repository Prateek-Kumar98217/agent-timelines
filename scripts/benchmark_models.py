"""Compare models on tool-calling behaviour by running real tasks through the local stack.

uv run python scripts/benchmark_models.py qwen3:4b llama3.1:8b --provider ollama
"""

import argparse
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

FINISHED = {"completed", "capped", "budget_exhausted", "failed", "cancelled"}
TASKS = [
    (
        "Using the knowledge base, find the year Apollo 11 landed on the Moon. Then use the "
        "calculator to work out how many years before 2000 that was.",
        ["kb_search", "calculator"],
        "31",
    ),
    (
        "Use the knowledge base to find the capital of Australia and of New Zealand. Save each "
        "answer to the scratchpad, then report both.",
        ["kb_search", "scratchpad_write"],
        "Canberra",
    ),
    (
        "Which planet does the moon Titan orbit? Check the knowledge base.",
        ["kb_search"],
        "Saturn",
    ),
    (
        "Use the calculator to convert 8849 metres to feet, multiplying by 3.28084.",
        ["calculator"],
        "29032",
    ),
    (
        "Fact check with the knowledge base: Marie Curie won two Nobel Prizes. Say true or false.",
        ["kb_search"],
        "true",
    ),
    (
        "Plan a 3-stop trip of Tokyo, Bangkok and Hanoi. Use the knowledge base to confirm which "
        "country each city is in, then list the stops with their countries.",
        ["kb_search"],
        "Vietnam",
    ),
]


def run_task(
    client: httpx.Client, provider: str, model: str, task: str, timeout: float
) -> dict[str, Any]:
    created = client.post("/api/trees", json={"task": task, "provider": provider, "model": model})
    created.raise_for_status()
    tree_id, branch_id = created.json()["tree_id"], created.json()["branch_id"]
    started = time.monotonic()
    while True:
        tree = client.get(f"/api/trees/{tree_id}").json()
        branch = next(item for item in tree["branches"] if item["id"] == branch_id)
        if branch["status"] in FINISHED:
            break
        if time.monotonic() - started > timeout:
            client.post(f"/api/branches/{branch_id}/cancel")
            branch["status"] = "timed_out"
            break
        time.sleep(1)
    nodes = sorted(
        (node for node in tree["nodes"] if node["branch_id"] == branch_id),
        key=lambda node: node["seq"],
    )
    last = nodes[-1]["payload"] if nodes[-1]["type"] == "llm_output" else {}
    return {
        "status": branch["status"],
        "reason": branch["status_reason"],
        "seconds": time.monotonic() - started,
        "turns": branch["turns_used"],
        "tokens": branch["input_tokens"] + branch["output_tokens"],
        "tools": [node["payload"]["name"] for node in nodes if node["type"] == "tool_call"],
        "tool_errors": sum(
            1 for node in nodes if node["type"] == "tool_result" and node["payload"]["is_error"]
        ),
        "text": last.get("text") or "",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="+")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--output", type=Path, default=Path("docs/model-benchmark.md"))
    args = parser.parse_args()

    client = httpx.Client(base_url="http://127.0.0.1:8000", timeout=20)
    rows = []
    for model in args.models:
        results = []
        for repeat in range(args.repeats):
            for index, (task, tools, expected) in enumerate(TASKS):
                result = run_task(client, args.provider, model, task, args.timeout)
                result["used_expected_tools"] = all(tool in result["tools"] for tool in tools)
                compact = result["text"].replace(",", "").lower()
                result["answer_found"] = expected.lower() in compact
                results.append(result)
                print(
                    f"{model} run {repeat + 1} task {index + 1}: {result['status']} "
                    f"{result['turns']} turns {result['seconds']:.0f}s tools={result['tools']} "
                    f"errors={result['tool_errors']} answer={result['answer_found']}",
                    flush=True,
                )
        count = len(results)
        rows.append(
            f"| {model} | {count} "
            f"| {sum(r['status'] == 'completed' for r in results)} "
            f"| {sum(r['used_expected_tools'] for r in results)} "
            f"| {sum(r['answer_found'] for r in results)} "
            f"| {sum(r['tool_errors'] for r in results)} "
            f"| {statistics.mean(r['turns'] for r in results):.1f} "
            f"| {statistics.median(r['seconds'] for r in results):.0f} "
            f"| {statistics.median(r['tokens'] for r in results):.0f} |"
        )
    args.output.write_text(
        "# Model benchmark\n\n"
        f"{args.provider} models on {len(TASKS)} short tasks, repeated {args.repeats} times, "
        "through the full local stack. Generated by `scripts/benchmark_models.py`. "
        "The answer check is a simple text match, and this is a small sample, so treat "
        "differences of a run or two as noise.\n\n"
        "| Model | Runs | Completed | Used expected tools | Answer found | Tool errors "
        "| Mean turns | Median seconds | Median tokens |\n"
        "|---|---|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
    )
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
