"""Scores branch answers with DeepEval, using free-tier Gemini as the judge.

Put GEMINI_API_KEY in a git-ignored .env file, then:
    uv run --all-groups python evals/judge.py --check     # one call, to confirm the key works
    uv run --all-groups python evals/judge.py             # score everything not yet scored

Scores are saved one at a time, so a run stopped by the daily quota resumes where it left off.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
sys.path.insert(0, str(Path(__file__).parent))

import openai
from deepeval.metrics import GEval
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from tasks import TASKS_BY_ID, EvalTask, fact_score

from app.db.models import BranchScore
from app.db.session import SessionFactory, make_session_factory
from app.settings import get_settings

ROOT = Path(__file__).resolve().parent.parent
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-2.5-flash"
METRIC = "judge_correctness"
# About 10 requests a minute. Free-tier limits vary by model and change without notice, so pacing
# stays conservative and 429s are still handled.
MIN_SECONDS_BETWEEN_CALLS = 6.5
MAX_RATE_LIMIT_RETRIES = 4
EVALUATION_STEPS = [
    "Check whether the actual output states every fact in the expected output.",
    "Treat different wording and number formats as equivalent when the facts agree.",
    "Lower the score heavily for any stated fact that contradicts the expected output.",
    "Ignore extra detail in the actual output unless it contradicts the expected output.",
]


class JudgeAuthError(Exception):
    """The API key was rejected. The provider's response text is never included."""


class JudgeQuotaError(Exception):
    """Still rate limited after retries, which usually means the daily quota is used up."""


class GeminiJudge(DeepEvalBaseLLM):
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = GEMINI_BASE_URL,
        min_interval: float = MIN_SECONDS_BETWEEN_CALLS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        self._model_name = model
        self._min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last_call = float("-inf")
        self.calls = 0
        super().__init__(model)

    def load_model(self) -> Any:
        return self._client

    def get_model_name(self) -> str:
        return self._model_name

    def generate(self, prompt: str) -> str:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            wait = self._last_call + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last_call = self._clock()
            self.calls += 1
            try:
                response = self._client.chat.completions.create(
                    model=self._model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                )
                return response.choices[0].message.content or ""
            except openai.RateLimitError as error:
                if attempt == MAX_RATE_LIMIT_RETRIES:
                    raise JudgeQuotaError("still rate limited after retries") from None
                self._sleep(_retry_after(error, attempt))
            except openai.AuthenticationError, openai.PermissionDeniedError:
                raise JudgeAuthError("the API key was rejected") from None
            except openai.BadRequestError:
                # Gemini reports a bad key as a 400, and its text can echo request details.
                raise JudgeAuthError(
                    "the request was refused; check the API key and model name"
                ) from None
        raise JudgeQuotaError("still rate limited after retries")

    async def a_generate(self, prompt: str) -> str:
        return await asyncio.to_thread(self.generate, prompt)


def _retry_after(error: openai.RateLimitError, attempt: int) -> float:
    try:
        return max(1.0, float(error.response.headers.get("retry-after", "")))
    except ValueError:
        return 15.0 * 2**attempt


def correctness_metric(judge: DeepEvalBaseLLM) -> GEval:
    return GEval(
        name="Correctness",
        evaluation_steps=EVALUATION_STEPS,
        evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT],
        model=judge,
        async_mode=False,
        threshold=0.7,
    )


def judge_answer(judge: DeepEvalBaseLLM, task: EvalTask, answer: str) -> tuple[float, str]:
    metric = correctness_metric(judge)
    metric.measure(
        LLMTestCase(input=task.text, actual_output=answer, expected_output=task.expected_answer),
        _show_indicator=False,
    )
    return float(metric.score or 0.0), str(metric.reason or "")


def already_scored(session_factory: SessionFactory) -> set[str]:
    with session_factory() as session:
        rows = session.execute(
            select(BranchScore.branch_id).where(BranchScore.metric == METRIC)
        ).scalars()
        return {str(branch_id) for branch_id in rows}


def save_score(
    session_factory: SessionFactory, branch_id: str, score: float, reason: str, scorer: str
) -> None:
    values = {"score": score, "reason": reason, "scorer": scorer}
    with session_factory() as session:
        session.execute(
            insert(BranchScore)
            .values(branch_id=UUID(branch_id), metric=METRIC, **values)
            .on_conflict_do_update(
                index_elements=[BranchScore.branch_id, BranchScore.metric], set_=values
            )
        )
        session.commit()


def run_judging(
    session_factory: SessionFactory,
    judge: DeepEvalBaseLLM,
    runs: list[dict[str, Any]],
    limit: int | None = None,
) -> dict[str, int]:
    """Score runs that have an answer and no score yet. Stops cleanly when the quota runs out."""
    done = already_scored(session_factory)
    counts = {"scored": 0, "skipped_existing": 0, "no_answer": 0, "remaining": 0}
    for run in runs:
        if run["branch_id"] in done:
            counts["skipped_existing"] += 1
            continue
        if not run.get("final_text"):
            counts["no_answer"] += 1
            continue
        if limit is not None and counts["scored"] >= limit:
            counts["remaining"] += 1
            continue
        task = TASKS_BY_ID[run["task_id"]]
        try:
            score, reason = judge_answer(judge, task, run["final_text"])
        except JudgeQuotaError:
            print("rate limited after retries; run this again later to resume", flush=True)
            counts["remaining"] = sum(
                1
                for item in runs
                if item.get("final_text")
                and item["branch_id"] not in already_scored(session_factory)
            )
            return counts
        save_score(session_factory, run["branch_id"], score, reason, judge.get_model_name())
        counts["scored"] += 1
        print(
            f"{run['task_id']:11} {run['edit_class']:21} judge={score:.2f} "
            f"facts={fact_score(task, run['final_text']):.2f}",
            flush=True,
        )
    return counts


def agreement(session_factory: SessionFactory, runs: list[dict[str, Any]]) -> dict[str, Any]:
    """How often the judge and the deterministic fact check agree on pass or fail."""
    with session_factory() as session:
        rows = session.execute(
            select(BranchScore.branch_id, BranchScore.score).where(BranchScore.metric == METRIC)
        ).all()
    scores = {str(row.branch_id): row.score for row in rows}
    pairs = [
        (scores[run["branch_id"]] >= 0.7, run["fact_score"] == 1.0)
        for run in runs
        if run["branch_id"] in scores
    ]
    if not pairs:
        return {"judged": 0, "agreement": None}
    return {
        "judged": len(pairs),
        "agreement": sum(1 for a, b in pairs if a == b) / len(pairs),
        "judge_pass_rate": sum(1 for a, _ in pairs if a) / len(pairs),
        "fact_check_pass_rate": sum(1 for _, b in pairs if b) / len(pairs),
        "judge_pass_fact_fail": sum(1 for a, b in pairs if a and not b),
        "judge_fail_fact_pass": sum(1 for a, b in pairs if not a and b),
    }


def read_api_key(env_file: Path = ROOT / ".env") -> str | None:
    if key := os.environ.get("GEMINI_API_KEY"):
        return key
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="make one call and stop")
    parser.add_argument("--limit", type=int, help="score at most this many branches")
    parser.add_argument("--model", default=os.environ.get("JUDGE_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()

    api_key = read_api_key()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY isn't set: add it to .env in the repository root")
    judge = GeminiJudge(api_key, args.model)
    if args.check:
        try:
            reply = judge.generate('Reply with exactly this JSON: {"ok": true}')
        except (JudgeAuthError, JudgeQuotaError) as error:
            raise SystemExit(f"the judge isn't working: {error}") from None
        print(f"judge works with {args.model}; reply: {reply[:60]!r}")
        return

    session_factory = make_session_factory(get_settings().database_url)
    with (ROOT / "docs" / "eval" / "runs.jsonl").open() as file:
        runs = [json.loads(line) for line in file]
    try:
        counts = run_judging(session_factory, judge, runs, args.limit)
    except JudgeAuthError as error:
        raise SystemExit(f"the judge isn't working: {error}") from None
    summary = {**counts, "model": args.model, **agreement(session_factory, runs)}
    (ROOT / "docs" / "eval" / "judge.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
