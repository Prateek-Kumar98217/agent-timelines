"""The judge against a local server that speaks Gemini's OpenAI-compatible API.

DeepEval's real metric code runs here. Only the model behind it is a stub.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from judge import (
    METRIC,
    GeminiJudge,
    JudgeAuthError,
    JudgeQuotaError,
    agreement,
    judge_answer,
    read_api_key,
    run_judging,
)
from sqlalchemy import select
from tasks import TASKS_BY_ID

from app.db.models import Branch, BranchScore, Tree, User
from app.db.session import SessionFactory

SECRET = "AIza-secret-key-value"


class Stub(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.statuses: list[int] = []
        self.score = 9
        self.prompts: list[str] = []
        self.keys: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/v1"


class Handler(BaseHTTPRequestHandler):
    server: Stub

    def log_message(self, *_args: object) -> None:
        return None

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self.server.prompts.append(body["messages"][0]["content"])
        self.server.keys.append(self.headers["authorization"])
        status = self.server.statuses.pop(0) if self.server.statuses else 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if status == 429:
            self.send_header("Retry-After", "3")
        self.end_headers()
        if status != 200:
            reply: dict[str, Any] = {"error": {"message": f"bad key {SECRET}"}}
        else:
            content = json.dumps({"score": self.server.score, "reason": "matches the facts"})
            reply = {
                "id": "c",
                "object": "chat.completion",
                "created": 1,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": f"```json\n{content}\n```"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        self.wfile.write(json.dumps(reply).encode())


@pytest.fixture
def stub() -> Iterator[Stub]:
    server = Stub()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def make_judge(stub: Stub, sleeps: list[float] | None = None) -> GeminiJudge:
    return GeminiJudge(
        SECRET,
        "gemini-test",
        stub.url,
        min_interval=0,
        sleep=(sleeps if sleeps is not None else []).append,
    )


def test_real_deepeval_metric_scores_through_the_judge(stub: Stub) -> None:
    judge = make_judge(stub)
    task = TASKS_BY_ID["apollo_gap"]
    score, reason = judge_answer(judge, task, "1969 and 1972, 3 years apart")
    assert score == pytest.approx(0.9)
    assert reason == "matches the facts"
    assert stub.keys[0] == f"Bearer {SECRET}"
    assert task.expected_answer in stub.prompts[0]
    assert "1969 and 1972, 3 years apart" in stub.prompts[0]
    assert judge.calls == 1


def test_rate_limits_wait_for_retry_after_then_succeed(stub: Stub) -> None:
    sleeps: list[float] = []
    stub.statuses = [429, 429]
    judge = make_judge(stub, sleeps)
    assert "score" in judge.generate("hello")
    assert sleeps.count(3.0) == 2
    assert judge.calls == 3


def test_ongoing_rate_limits_raise_a_quota_error(stub: Stub) -> None:
    stub.statuses = [429] * 10
    with pytest.raises(JudgeQuotaError):
        make_judge(stub).generate("hello")


@pytest.mark.parametrize("status", [400, 401, 403])
def test_bad_keys_give_a_clean_error_that_never_echoes_the_key(stub: Stub, status: int) -> None:
    stub.statuses = [status]
    with pytest.raises(JudgeAuthError) as error:
        make_judge(stub).generate("hello")
    assert SECRET not in str(error.value)
    assert error.value.__cause__ is None


def test_calls_are_paced(stub: Stub) -> None:
    now = [100.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    judge = GeminiJudge(SECRET, "m", stub.url, min_interval=6.5, sleep=sleep, clock=lambda: now[0])
    judge.generate("one")
    judge.generate("two")
    assert sleeps == [6.5]


def test_key_comes_from_the_environment_or_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert read_api_key(env_file) is None
    env_file.write_text('OTHER=1\nGEMINI_API_KEY="from-file"\n')
    assert read_api_key(env_file) == "from-file"
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert read_api_key(env_file) == "from-env"


def make_runs(factory: SessionFactory, count: int) -> list[dict[str, Any]]:
    with factory() as session:
        user = User(id=uuid4(), kind="local")
        session.add(user)
        session.flush()
        tree = Tree(id=uuid4(), owner_id=user.id)
        session.add(tree)
        session.flush()
        runs = []
        for _ in range(count):
            branch = Branch(id=uuid4(), tree_id=tree.id, owner_id=user.id, status="completed")
            session.add(branch)
            runs.append(
                {
                    "branch_id": str(branch.id),
                    "task_id": "apollo_gap",
                    "edit_class": "main",
                    "final_text": "1969 and 1972, 3 years apart",
                    "fact_score": 1.0,
                }
            )
        session.commit()
    return runs


def stored(factory: SessionFactory) -> dict[str, float]:
    with factory() as session:
        rows = session.execute(
            select(BranchScore.branch_id, BranchScore.score).where(BranchScore.metric == METRIC)
        ).all()
    return {str(row.branch_id): row.score for row in rows}


def test_scores_are_saved_and_a_rerun_makes_no_calls(stub: Stub, clean_db: SessionFactory) -> None:
    runs = make_runs(clean_db, 2)
    judge = make_judge(stub)
    counts = run_judging(clean_db, judge, runs)
    assert counts["scored"] == 2
    assert stored(clean_db) == {run["branch_id"]: pytest.approx(0.9) for run in runs}

    again = make_judge(stub)
    assert run_judging(clean_db, again, runs)["skipped_existing"] == 2
    assert again.calls == 0


def test_running_out_of_quota_keeps_earlier_scores_and_resumes(
    stub: Stub,
    clean_db: SessionFactory,
) -> None:
    runs = make_runs(clean_db, 3)
    judge = make_judge(stub)
    original = judge.generate
    calls = {"count": 0}

    def fails_on_second(prompt: str) -> str:
        calls["count"] += 1
        if calls["count"] == 2:
            raise JudgeQuotaError("quota")
        return original(prompt)

    judge.generate = fails_on_second  # type: ignore[method-assign]
    counts = run_judging(clean_db, judge, runs)
    assert counts["scored"] == 1
    assert counts["remaining"] == 2
    assert len(stored(clean_db)) == 1

    resumed = run_judging(clean_db, make_judge(stub), runs)
    assert resumed["scored"] == 2
    assert len(stored(clean_db)) == 3


def test_branches_with_no_answer_cost_no_call(stub: Stub, clean_db: SessionFactory) -> None:
    runs = make_runs(clean_db, 1)
    runs[0]["final_text"] = None
    judge = make_judge(stub)
    assert run_judging(clean_db, judge, runs)["no_answer"] == 1
    assert judge.calls == 0


def test_limit_caps_the_number_scored(stub: Stub, clean_db: SessionFactory) -> None:
    runs = make_runs(clean_db, 3)
    counts = run_judging(clean_db, make_judge(stub), runs, limit=1)
    assert (counts["scored"], counts["remaining"]) == (1, 2)


def test_agreement_with_the_fact_check(stub: Stub, clean_db: SessionFactory) -> None:
    runs = make_runs(clean_db, 2)
    runs[1]["fact_score"] = 0.5
    run_judging(clean_db, make_judge(stub), runs)
    result = agreement(clean_db, runs)
    assert result["judged"] == 2
    assert result["agreement"] == 0.5
    assert result["judge_pass_fact_fail"] == 1
