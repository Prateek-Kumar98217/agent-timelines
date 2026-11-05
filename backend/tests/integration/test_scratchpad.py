from uuid import UUID, uuid4

from app.adapters.memory import HashEmbedder
from app.db.models import Branch, BranchStatus, Node, NodeType, Tree, User
from app.db.session import SessionFactory
from app.domain.scratchpad import search_notes, split_chunks, write_note
from app.ports import ToolContext


def make_branch(session_factory: SessionFactory) -> tuple[UUID, UUID]:
    """Create a branch with a root node, which stands in for the tool call that writes notes."""
    with session_factory() as session:
        user = User(id=uuid4(), kind="local")
        session.add(user)
        session.flush()
        tree = Tree(id=uuid4(), owner_id=user.id)
        session.add(tree)
        session.flush()
        branch = Branch(
            id=uuid4(), tree_id=tree.id, owner_id=user.id, status=BranchStatus.RUNNING.value
        )
        session.add(branch)
        session.flush()
        node = Node(
            id=uuid4(),
            tree_id=tree.id,
            branch_id=branch.id,
            type=NodeType.ROOT.value,
            payload={},
            seq=0,
            turn_index=0,
        )
        session.add(node)
        session.commit()
        return branch.id, node.id


def test_search_returns_the_closest_note_first(clean_db: SessionFactory) -> None:
    embedder = HashEmbedder()
    branch, node = make_branch(clean_db)
    write_note(clean_db, embedder, ToolContext(branch, node), "alpha\n\n" + "b" * 900)
    hits = search_notes(clean_db, embedder, branch, "alpha")
    assert hits[0].content == "alpha"
    assert hits[0].distance < 1e-6


def test_search_only_sees_the_branch_it_was_asked_about(clean_db: SessionFactory) -> None:
    embedder = HashEmbedder()
    one, one_node = make_branch(clean_db)
    two, _ = make_branch(clean_db)
    write_note(clean_db, embedder, ToolContext(one, one_node), "private to one")
    assert search_notes(clean_db, embedder, two, "private to one") == []


def test_retrying_a_write_adds_nothing(clean_db: SessionFactory) -> None:
    embedder = HashEmbedder()
    branch, node = make_branch(clean_db)
    context = ToolContext(branch, node)
    assert write_note(clean_db, embedder, context, "same note") == 1
    write_note(clean_db, embedder, context, "same note")
    assert len(search_notes(clean_db, embedder, branch, "q")) == 1


def test_empty_note_writes_nothing(clean_db: SessionFactory) -> None:
    branch, node = make_branch(clean_db)
    assert write_note(clean_db, HashEmbedder(), ToolContext(branch, node), "  \n\n ") == 0


def test_split_chunks_keeps_paragraphs_and_limits_size() -> None:
    assert split_chunks("a\n\nb", size=10) == ["a\n\nb"]
    assert split_chunks("aaaaaa\n\nbbbbbb", size=10) == ["aaaaaa", "bbbbbb"]
    assert split_chunks("x" * 25, size=10) == ["x" * 10, "x" * 10, "x" * 5]
    assert split_chunks("  \n\n ") == []
