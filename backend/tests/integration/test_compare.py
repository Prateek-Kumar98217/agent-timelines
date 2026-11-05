from collections.abc import Sequence
from uuid import UUID

import pytest

from app.adapters.memory import HashEmbedder
from app.db.models import BranchScore
from app.db.session import SessionFactory
from app.domain.compare import BranchesNotComparableError, compare_branches, cosine_similarity
from app.domain.engine import start_task
from app.domain.forking import fork_from_edit
from tests.helpers import CONFIG, Harness, text_reply, tool_reply


def test_identical_finals_and_different_finals(clean_db: SessionFactory) -> None:
    harness = Harness(
        clean_db, [tool_reply("a"), text_reply("same"), text_reply("same"), text_reply("other")]
    )
    _, main = start_task(harness.deps, harness.user.id, "t", CONFIG)
    harness.drain()
    result = harness.nodes(main)[4]
    same = fork_from_edit(harness.deps, harness.user.id, result.id, {"content": "1"})
    other = fork_from_edit(harness.deps, harness.user.id, result.id, {"content": "2"})
    harness.drain()
    with clean_db() as session:
        assert compare_branches(session, main, same).same_final_text is True
        assert compare_branches(session, main, other).same_final_text is False
        assert compare_branches(session, same, other).shared_nodes == 4


def test_branch_with_no_final_answer_never_counts_as_same(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply("a"), text_reply("x")])
    _, main = start_task(harness.deps, harness.user.id, "t", CONFIG)
    _, other = start_task(harness.deps, harness.user.id, "t", CONFIG)
    with clean_db() as session, pytest.raises(BranchesNotComparableError):
        compare_branches(session, main, other)
    with clean_db() as session:
        comparison = compare_branches(session, main, main)
    assert comparison.a.final_text is None
    assert comparison.same_final_text is False
    harness.queue.messages.clear()


def finished_pair(clean_db: SessionFactory, first: str, second: str) -> tuple[Harness, UUID, UUID]:
    harness = Harness(clean_db, [tool_reply("a"), text_reply(first), text_reply(second)])
    _, main = start_task(harness.deps, harness.user.id, "t", CONFIG)
    harness.drain()
    result = harness.nodes(main)[4]
    fork = fork_from_edit(harness.deps, harness.user.id, result.id, {"content": "x"})
    harness.drain()
    return harness, main, fork


def test_identical_answers_converge(clean_db: SessionFactory) -> None:
    _, main, fork = finished_pair(clean_db, "same text", "same text")
    with clean_db() as session:
        comparison = compare_branches(session, main, fork, HashEmbedder())
    assert comparison.final_similarity == 1.0
    assert comparison.converged is True


def test_similarity_is_compared_to_the_threshold(clean_db: SessionFactory) -> None:
    class Fixed:
        dimensions = 2

        def embed(self, _texts: Sequence[str]) -> list[list[float]]:
            return [[1.0, 0.0], [0.8, 0.6]]

    _, main, fork = finished_pair(clean_db, "one", "two")
    with clean_db() as session:
        strict = compare_branches(session, main, fork, Fixed(), threshold=0.9)
        loose = compare_branches(session, main, fork, Fixed(), threshold=0.7)
    assert strict.final_similarity == pytest.approx(0.8)
    assert strict.converged is False
    assert loose.converged is True


def test_no_similarity_without_two_final_answers(clean_db: SessionFactory) -> None:
    harness = Harness(clean_db, [tool_reply("a")])
    _, main = start_task(harness.deps, harness.user.id, "t", CONFIG)
    with clean_db() as session:
        comparison = compare_branches(session, main, main, HashEmbedder())
    assert comparison.final_similarity is None
    assert comparison.converged is None
    harness.queue.messages.clear()


def test_scores_appear_in_the_comparison(clean_db: SessionFactory) -> None:
    _, main, fork = finished_pair(clean_db, "one", "two")
    with clean_db() as session:
        session.add(BranchScore(branch_id=main, metric="facts", score=0.5, scorer="checks"))
        session.commit()
        comparison = compare_branches(session, main, fork)
    assert comparison.a.scores == {"facts": 0.5}
    assert comparison.b.scores == {}


def test_cosine_similarity_handles_zero_vectors() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
