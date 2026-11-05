from collections.abc import Sequence

import pytest
from sqlalchemy import func, select

from app.adapters.memory import HashEmbedder
from app.db.models import KbChunk, KbDocument
from app.db.session import SessionFactory
from app.kb.ingest import ingest
from app.kb.search import SearchMode, search_kb

DOCS = [
    {
        "topic": "geography",
        "title": "Canberra",
        "url": "https://x.test/canberra",
        "text": "Canberra is the capital city of Australia.\n\nIt was founded in 1913.",
    },
    {
        "topic": "space",
        "title": "Mars",
        "url": "https://x.test/mars",
        "text": "Mars is the fourth planet from the Sun.",
    },
]


class LookupEmbedder:
    """Vectors chosen by keyword, so vector search has a known right answer."""

    dimensions = 384

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        lowered = text.lower()
        vector[0] = 1.0 if ("canberra" in lowered or "city" in lowered) else 0.0
        vector[1] = 1.0 if ("mars" in lowered or "red world" in lowered) else 0.0
        vector[2] = 0.01
        return vector


def counts(session_factory: SessionFactory) -> tuple[int, int]:
    with session_factory() as session:
        return (
            session.execute(select(func.count()).select_from(KbDocument)).scalar_one(),
            session.execute(select(func.count()).select_from(KbChunk)).scalar_one(),
        )


def test_ingest_stores_chunks_and_is_repeatable(clean_db: SessionFactory) -> None:
    result = ingest(clean_db, HashEmbedder(), DOCS)
    assert (result.added, result.unchanged) == (2, 0)
    assert counts(clean_db) == (2, result.chunks)
    again = ingest(clean_db, HashEmbedder(), DOCS)
    assert (again.added, again.unchanged, again.chunks) == (0, 2, 0)
    assert counts(clean_db) == (2, result.chunks)


def test_changed_documents_replace_their_chunks(clean_db: SessionFactory) -> None:
    ingest(clean_db, HashEmbedder(), DOCS)
    changed = [{**DOCS[1], "text": "Mars is red.\n\n" + "x " * 600}]
    result = ingest(clean_db, HashEmbedder(), changed)
    assert result.added == 1
    with clean_db() as session:
        contents = session.execute(
            select(KbChunk.content).join(KbDocument).where(KbDocument.url == DOCS[1]["url"])
        ).scalars()
        assert not any("fourth planet" in content for content in contents)


def test_chunks_carry_their_title(clean_db: SessionFactory) -> None:
    ingest(clean_db, HashEmbedder(), DOCS)
    hit = search_kb(clean_db, HashEmbedder(), "fourth planet", mode=SearchMode.TEXT)[0]
    assert hit.content.startswith("Mars: ")
    assert hit.url == "https://x.test/mars"


def test_text_search_matches_any_term_and_ignores_query_syntax(clean_db: SessionFactory) -> None:
    ingest(clean_db, HashEmbedder(), DOCS)
    hits = search_kb(
        clean_db, HashEmbedder(), "what's the capital (of) -Australia?", mode=SearchMode.TEXT
    )
    assert hits[0].title == "Canberra"
    assert search_kb(clean_db, HashEmbedder(), "qqqzzz", mode=SearchMode.TEXT) == []


def test_vector_search_finds_meaning_without_shared_words(clean_db: SessionFactory) -> None:
    embedder = LookupEmbedder()
    ingest(clean_db, embedder, DOCS)
    assert search_kb(clean_db, embedder, "the red world", mode=SearchMode.VECTOR)[0].title == "Mars"
    assert search_kb(clean_db, embedder, "the red world", mode=SearchMode.TEXT) == []


def test_hybrid_combines_both_and_respects_the_limit(clean_db: SessionFactory) -> None:
    embedder = LookupEmbedder()
    ingest(clean_db, embedder, DOCS)
    assert search_kb(clean_db, embedder, "the red world")[0].title == "Mars"
    assert search_kb(clean_db, embedder, "fourth planet")[0].title == "Mars"
    assert len(search_kb(clean_db, embedder, "Canberra Mars", limit=1)) == 1


@pytest.mark.parametrize("mode", list(SearchMode))
def test_an_empty_knowledge_base_returns_nothing(
    clean_db: SessionFactory, mode: SearchMode
) -> None:
    assert search_kb(clean_db, HashEmbedder(), "anything", mode=mode) == []
