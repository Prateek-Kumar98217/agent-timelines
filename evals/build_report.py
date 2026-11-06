"""Builds the test report the website serves at /report.html, from the published results.

    uv run --all-groups python evals/build_report.py --count-tests   # also recount the tests
    uv run --all-groups python evals/build_report.py

Every test here ran locally. Nothing in the report comes from a deployed build.
"""

import argparse
import html
import json
import re
import statistics
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from report import (
    NOOP_CLASSES,
    calibrate_threshold,
    cost_per_branch,
    load_runs,
    summarize_class,
)

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "docs" / "eval"
OUTPUT = ROOT / "web" / "public" / "report.html"
PRICES = Path(__file__).with_name("prices.json")

MACHINE = "One laptop: AMD Ryzen 7 7840HS, RTX 4050 with 6 GB, 14 GB of memory available to WSL"

EDIT_NAMES = {
    "user_other_task": "A different task*",
    "user_paraphrase": "The same task, reworded*",
    "result_contradiction": "A tool result that contradicts the source",
    "result_irrelevant": "An irrelevant tool result",
    "result_error": "A tool error in place of the result",
    "call_other_query": "A different tool query",
    "main": "Original run",
}
TASK_NAMES = {
    "apollo_gap": "Years between two Apollo launches",
    "peak_gap": "Height difference between two mountains",
    "nobel_gap": "Years between two Nobel Prizes",
}
SCENARIO_NAMES = {
    "one_user": "One user",
    "five_users": "5 users",
    "twenty_users": "20 users",
    "overload_user_cap": "60 users against a low branch limit",
    "overload_queue_limit": "60 users against a low queue limit",
}
# Checked by hand on the running stack, with no scripted model. Each one was seen to work.
LIVE_CHECKS = [
    (
        "Web fetch into the task's RAG",
        "A real page from example.com was stored with its source, split into 2 chunks.",
    ),
    (
        "Search after the fetch",
        "A real <code>task_search</code> through the MCP gateway returned that page.",
    ),
    (
        "Search from before the fetch",
        "The same search on a fork from before the fetch found nothing, so the path rule holds.",
    ),
    (
        "Editing a fetched page",
        "The fork saw only the edited page, the model answered from it, "
        "and the original branch's store was unchanged.",
    ),
    (
        "Continuing a branch",
        "A branch at its turn limit took another message, gained 4 turns and answered, "
        "using a tree with a low limit so that it was quick.",
    ),
    ("Editing the task", "The API refused it with a 409."),
]
FAILURE_CHECKS = [
    "20 parallel forks, with a stream of events showing no gaps",
    "30 of 40 simultaneous forks accepted at a limit of 30, with the other 10 refused",
    "A worker killed mid-step: the branch resumed with no duplicate or missing steps",
    "The message broker stopped: nothing lost, and the branch finished after it returned",
    "The message broker killed during a send: the step wasn't lost",
    "A waiting branch: no queue message and no active worker",
    "The browser container: no route to internal services, no secrets, no access to agent tools",
    "A saved API key: absent from every log, trace and metric",
    "One trace across the API, worker and gateway, with all 12 dashboard panels returning data",
]


def esc(value: object) -> str:
    return html.escape(str(value))


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.1f} s"


def table(headers: list[str], rows: list[list[str]], first_is_text: bool = True) -> str:
    """Build a table. Cells are HTML, so callers escape anything that came from outside."""
    head = "".join(
        f'<th scope="col"{"" if i == 0 and first_is_text else " class=num"}>{h}</th>'
        for i, h in enumerate(headers)
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<th scope="row">{cell}</th>' if i == 0 else f"<td class=num>{cell}</td>"
            for i, cell in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return (
        '<div class="scroll"><table><thead><tr>'
        f"{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def edit_section(runs: list[dict[str, Any]]) -> str:
    threshold = calibrate_threshold(runs)
    assert threshold is not None
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        by_class[run["edit_class"]].append(run)
    noop = [run for name in NOOP_CLASSES for run in by_class[name]]

    def row(name: str, items: list[dict[str, Any]]) -> list[str]:
        summary = summarize_class(items, threshold)
        low, high = summary["divergence_ci"]
        return [
            name,
            str(summary["runs"]),
            f"{percent(summary['divergence_rate'])} ({low * 100:.0f} to {high * 100:.0f}%)",
            percent(summary["facts_changed_rate"]),
            percent(summary["all_facts_rate"]),
        ]

    edits = {
        name: items
        for name, items in by_class.items()
        if name != "main" and name not in NOOP_CLASSES
    }
    ordered = sorted(
        edits.items(), key=lambda item: -(summarize_class(item[1], threshold)["facts_changed_rate"])
    )
    contradiction = summarize_class(by_class["result_contradiction"], threshold)
    mains = by_class["main"]
    statuses = [run["status"] for run in runs]
    prices = json.loads(PRICES.read_text())
    finished = [run for run in runs if run["status"] == "completed"]
    costs = [
        [esc(option["name"]), f"${cost_per_branch(finished, option):.5f}"]
        for option in prices["options"]
    ]
    return f"""
<section id="edits">
  <h2>What editing a step changes</h2>
  <p>Each of 8 tasks ran once on <code>qwen3:4b</code>, then each kind of edit was forked from it
  twice: {len(runs)} branches in all. Models vary between runs with no edit at all, so no-op forks
  set the baseline. An answer counts as diverged when its similarity to the source falls below
  {threshold:.3f}, the 10th percentile of those no-op forks.</p>
  {
        table(
            ["Edit", "Runs", "Answer diverged (95% interval)", "Facts changed", "All facts right"],
            [row("No edit (baseline)", noop)]
            + [row(EDIT_NAMES.get(name, esc(name)), items) for name, items in ordered],
        )
    }
  <p class="note">* Editing the task itself isn't possible in the product. These forks went through
  the engine directly, and they measure how sensitive the model is.</p>
  <p class="finding"><strong>Similarity follows wording, not facts.</strong> A contradicting source
  changed the facts in {percent(contradiction["facts_changed_rate"])} of runs, yet only
  {percent(contradiction["divergence_rate"])} counted as diverged, because a changed date barely
  moves the score. That's why the compare view marks the changed words.</p>
  <p>{sum(1 for r in mains if r["fact_score"] == 1.0)} of {len(mains)} original runs got every
  required fact. Those runs took {min(r["turns"] for r in mains)} to
  {max(r["turns"] for r in mains)} turns. {statuses.count("completed")} of {len(runs)} branches
  completed. The other failed after the model hit its 2-minute call limit three times on one step,
  which is the retry limit working as designed. Samples are small, so read the intervals rather
  than single percentages. Convergence is based on one model and 8 tasks.</p>
  <h3>Cost per branch</h3>
  <p>Local models have no bill. This is what a completed branch would cost on a similar hosted
  model, on average. The median branch used
  {statistics.median(r["tokens"] for r in finished):,.0f} tokens. Prices come from third-party
  trackers, as of {esc(prices["as_of"])}.</p>
  {table(["Priced at", "Mean cost per completed branch"], costs)}
</section>"""


def judge_section(judge: dict[str, Any]) -> str:
    rows = [
        [
            f"{esc(TASK_NAMES.get(a['task'], a['task']))}<br>"
            f'<span class="quiet">{esc(EDIT_NAMES.get(a["edit"], a["edit"]))}</span>',
            f"{a['judge']:.2f}",
            f"{a['facts']:.2f}",
            f'<span class="reason">{esc(a["reason"])}</span>',
        ]
        for a in judge["answers"]
    ]
    return f"""
<section id="judge">
  <h2>LLM as a judge</h2>
  <p>DeepEval's GEval metric with <code>{esc(judge["model"])}</code> scored {judge["judged"]} fixed
  answers: three tasks, each with its original run and a fork given a contradicting source. It runs
  by hand only, never in CI or on a schedule, and the set is deliberately small.</p>
  <p class="finding"><strong>The judge agreed with the deterministic fact check on
  {round(judge["agreement"] * judge["judged"])} of {judge["judged"]} answers.</strong> With so few,
  this shows the two checks don't obviously conflict. It doesn't show the judge is accurate in
  general.</p>
  {table(["Answer", "Judge", "Fact check", "The judge's reason"], rows)}
</section>"""


def recall_section(recall: list[dict[str, Any]]) -> str:
    rows = [
        [
            esc(
                {"vector": "Vector", "text": "Full text", "hybrid": "Hybrid"}.get(
                    m["mode"], m["mode"]
                )
            ),
            *(percent(m["document_recall"][k]) for k in ("1", "3", "5", "10")),
            f"{m['mean_reciprocal_rank']:.2f}",
            f"{m['median_ms']:.0f} ms",
        ]
        for m in recall
    ]
    questions = recall[0]["questions"]
    return f"""
<section id="recall">
  <h2>Retrieval (recall@k)</h2>
  <p>{questions} hand-reviewed questions against the fixed Wikipedia evaluation corpus. Recall@k is
  how often the right document is in the top k results.</p>
  {table(["Search", "@1", "@3", "@5", "@10", "MRR", "Median time"], rows)}
  <p>Vector search did best, so it's the default. These settings were chosen on the same questions,
  so the numbers are slightly optimistic. They describe the evaluation corpus. Real tasks search
  the pages they fetch themselves, which this doesn't measure.</p>
</section>"""


def load_section(load: list[dict[str, Any]]) -> str:
    rows = [
        [
            esc(SCENARIO_NAMES.get(r["scenario"], r["scenario"])),
            str(r["accepted"]),
            percent(r["rejection_rate"]),
            f"{seconds(r['fork_mean'])} / {seconds(r['fork_p95'])}",
            f"{seconds(r['queue_wait_mean'])} / {seconds(r['queue_wait_p95'])}",
            percent(r.get("cache_hit_rate")),
        ]
        for r in load
    ]
    return f"""
<section id="load">
  <h2>Load</h2>
  <p>Each scenario ran for 90 seconds with Locust, against a scripted model at 0.5 seconds per call
  and 4 workers. <strong>This measures the platform, not a model.</strong></p>
  {
        table(
            [
                "Scenario",
                "Accepted",
                "Refused",
                "Fork mean / p95",
                "Queue wait mean / p95",
                "Cache hits",
            ],
            rows,
        )
    }
  <p>Under overload the system refuses new work rather than falling behind: the refusal rate climbs
  while forks stay under 200 ms at p95. These numbers include waiting for the broker to confirm
  each send. That added about 4 ms on average with one user, and about 140 ms to the p95 at worst.
  p95 values come from histogram buckets, so they can overstate. Cache hits are for
  the scripted model's knowledge base searches.</p>
</section>"""


def steps_section(steps: dict[str, Any], benchmark: list[list[str]]) -> str:
    rows = [
        [name, f"{item['steps']:,}", seconds(item["p50"]), seconds(item["p95"])]
        for name, item in (("Model step", steps["model"]), ("Tool step", steps["tool"]))
    ]
    return f"""
<section id="speed">
  <h2>Real step times</h2>
  <p>From the experiment's own steps on <code>qwen3:4b</code>. Other work shared the machine during
  it, so this isn't a benchmark.</p>
  {table(["Step", "Steps", "p50", "p95"], rows)}
  <h3>Choosing the local model</h3>
  <p>Each model ran 6 short tasks twice through the full stack.</p>
  {table(benchmark[0], benchmark[1:])}
  <p><code>qwen3:4b</code> found the answer every time, though it's slower, so it's the Ollama
  default. Hosted Groq and Gemini models have only been tested against a local stub.</p>
</section>"""


def tests_section(tests: dict[str, int]) -> str:
    rows = [[esc(name), f"{count:,}"] for name, count in tests.items()]
    total = sum(tests.values())
    checks = "".join(f"<li>{esc(item)}</li>" for item in FAILURE_CHECKS)
    live = "".join(f"<tr><th scope=row>{a}</th><td>{b}</td></tr>" for a, b in LIVE_CHECKS)
    return f"""
<section id="tests">
  <h2>Automated and live checks</h2>
  <p>{total:,} automated tests. None of them call a real model.</p>
  {table(["Suite", "Tests"], rows)}
  <h3>Failure checks on the running stack</h3>
  <p>Run from <code>scripts/smoke_local.py</code> and <code>scripts/smoke_observability.py</code>,
  in containers, with a scripted model.</p>
  <ul>{checks}</ul>
  <p class="finding"><strong>The log check failed on GitHub.</strong> Loki refuses all writes once
  its disk is 90% full, and it reports this as "Ingester is shutting down". Forcing that limit
  locally reproduced the same failure. The job now frees disk space before it builds.</p>
  <p class="finding"><strong>CI found a real bug.</strong> A step was marked as sent without the
  broker confirming it, so a broker dying at that moment lost the message and left the branch stuck
  for 5 minutes. Sends now wait for confirmation, which is why forks in the load test above got
  slower under heavy load. A request can now wait about 6 seconds if the broker stops answering.</p>
  <h3>Live checks with the real model</h3>
  <p>Run once by hand on the full stack, with <code>qwen3:4b</code>.</p>
  <div class="scroll"><table><tbody>{live}</tbody></table></div>
  <p class="finding"><strong>The model rarely searches.</strong> It answered from the fetched page
  without calling <code>task_search</code>, even when the task asked for it, so the search above was
  triggered with an edit. On short tasks with this model, the task's RAG often goes unused.</p>
</section>"""


def limits_section(limitations: str) -> str:
    open_part = limitations.split("## Open", 1)[1].split("## Closed", 1)[0]
    rows = []
    for line in open_part.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] != "Limitation" and not cells[0].startswith("---"):
            render = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(cells[0]).replace("&#x27;", "'"))
            rows.append(f"<tr><th scope=row>{render}</th><td>{esc(cells[2])}</td></tr>")
    return f"""
<section id="limits">
  <h2>Known limits</h2>
  <p>What's still open, from <code>docs/LIMITATIONS.md</code>. Deferred items belong to the deployed
  build, which isn't built yet.</p>
  <div class="scroll"><table>
  <thead><tr><th scope=col>Limit</th><th scope=col>Status</th></tr></thead>
  <tbody>{"".join(rows)}</tbody></table></div>
</section>"""


def markdown_table(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            rows.append([esc(cell.strip()) for cell in line.strip().strip("|").split("|")])
    return rows


STYLE = """
:root { color-scheme: dark; --orange: #ff7a00; --text: #f2efea; --soft: #b0aba3; --line: #3a3a3a;
  --surface: #141414; }
* { box-sizing: border-box; }
body { margin: 0; background: #000; color: var(--text); line-height: 1.5;
  font-family: Overpass, "Segoe UI", system-ui, -apple-system, sans-serif; }
main { max-width: 60rem; margin: 0 auto; padding: 1rem 1rem 4rem; }
a { color: inherit; }
a:focus-visible { outline: 3px solid var(--orange); outline-offset: 2px; }
nav.top { display: flex; justify-content: space-between; align-items: baseline;
  padding: 0.5rem 0 1.5rem; }
.wordmark { color: var(--orange); font-size: 1.25rem; font-weight: 800; text-decoration: none; }
h1 { margin: 0 0 0.5rem; font-size: 2.25rem; line-height: 1.1; }
h2 { margin: 3rem 0 0.75rem; padding-top: 0.75rem; border-top: 2px solid var(--orange);
  font-size: 1.5rem; line-height: 1.2; }
h3 { margin: 1.75rem 0 0.5rem; font-size: 1.0625rem; }
p, ul { max-width: 70ch; margin: 0.75rem 0; }
li { margin: 0.25rem 0; }
code { font-family: ui-monospace, monospace; font-size: 0.875em; }
.quiet, .note, .meta { color: var(--soft); }
.note, .meta { font-size: 0.875rem; }
.toc { display: flex; flex-wrap: wrap; gap: 0.25rem 1.25rem; margin: 1rem 0 0; padding: 0;
  list-style: none; }
.finding { padding: 0.6rem 0.8rem; border-left: 4px solid var(--orange); background: #2e1a06; }
.scroll { overflow-x: auto; margin: 0.75rem 0; }
table { width: 100%; border-collapse: collapse; font-size: 0.9375rem; }
th, td { padding: 0.45rem 0.6rem 0.35rem; border-bottom: 1px solid var(--line); text-align: left;
  vertical-align: top; }
thead th { border-bottom: 2px solid var(--line); white-space: nowrap; }
tbody th { font-weight: 600; }
.num { font-variant-numeric: tabular-nums; }
.reason { display: block; min-width: 18rem; color: var(--soft); font-size: 0.875rem; }
"""


def build(
    runs: list[dict[str, Any]],
    judge: dict[str, Any],
    recall: list[dict[str, Any]],
    load: list[dict[str, Any]],
    steps: dict[str, Any],
    tests: dict[str, int],
    benchmark: list[list[str]],
    limitations: str,
    today: str,
) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test report</title>
<meta name="description" content="Local test results for agent-timelines.">
<style>{STYLE}</style>
</head>
<body>
<main>
<nav class="top">
  <a class="wordmark" href="/">agent-timelines</a><a href="/">Back to the app</a>
</nav>
<h1>Test report</h1>
<p>What was tested, how, and what it showed. <strong>Every result here comes from local tests.
Nothing has been measured on a deployed build.</strong></p>
<p class="meta">Built {esc(today)}. {esc(MACHINE)}.</p>
<ul class="toc">
  <li><a href="#edits">Editing steps</a></li>
  <li><a href="#judge">LLM as a judge</a></li>
  <li><a href="#recall">Retrieval</a></li>
  <li><a href="#load">Load</a></li>
  <li><a href="#speed">Step times</a></li>
  <li><a href="#tests">Checks</a></li>
  <li><a href="#limits">Known limits</a></li>
</ul>
{edit_section(runs)}
{judge_section(judge)}
{recall_section(recall)}
{load_section(load)}
{steps_section(steps, benchmark)}
{tests_section(tests)}
{limits_section(limitations)}
</main>
</body>
</html>
"""


def _count(command: list[str], pattern: str, cwd: Path = ROOT) -> int:
    output = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True).stdout  # noqa: S603
    return len(re.findall(pattern, output, re.MULTILINE))


def count_tests() -> dict[str, int]:
    def python(path: str) -> int:
        return _count(
            ["pytest", path, "--collect-only", "-q", "-p", "no:cacheprovider", "-W", "ignore"],
            r"^\S+::\S+",
        )

    web = ROOT / "web"
    return {
        "Backend": python("backend"),
        "Browser service": python("browser_service"),
        "Evaluation code": python("evals/tests"),
        "Web unit tests": _count(
            ["npx", "vitest", "list", "--filesOnly=false"], r"^\S+\.test\.ts > ", web
        ),
        "Browser tests of the app": _count(
            ["npx", "playwright", "test", "--list"], r"^\s+\S*app\.spec\.ts:\d+", web
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count-tests", action="store_true")
    args = parser.parse_args()
    tests_file = EVAL_DIR / "tests.json"
    if args.count_tests:
        tests_file.write_text(json.dumps(count_tests(), indent=2) + "\n")

    def read(name: str) -> Any:
        return json.loads((EVAL_DIR / name).read_text())

    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(
        build(
            load_runs(EVAL_DIR / "runs.jsonl"),
            read("judge.json"),
            read("recall.json"),
            read("load.json"),
            read("steps.json"),
            read("tests.json"),
            markdown_table((ROOT / "docs" / "model-benchmark.md").read_text()),
            (ROOT / "docs" / "LIMITATIONS.md").read_text(),
            date.today().isoformat(),
        )
    )
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
