import pytest

from app.db.session import SessionFactory
from app.domain.compare import BranchesNotComparableError, compare_branches
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
