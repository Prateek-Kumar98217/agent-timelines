"""Knowledge base search. Hybrid mode merges vector and full-text rankings."""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select

from app.db.models import KbChunk, KbDocument
from app.db.session import SessionFactory
from app.ports import Embedder

CANDIDATES = 50
# The standard constant for reciprocal rank fusion. It damps the effect of the very top ranks.
RRF_K = 60


class SearchMode(StrEnum):
    VECTOR = "vector"
    TEXT = "text"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class KbHit:
    title: str
    url: str
    content: str
    score: float


def search_kb(
    session_factory: SessionFactory,
    embedder: Embedder,
    query: str,
    limit: int = 5,
    mode: SearchMode = SearchMode.HYBRID,
) -> list[KbHit]:
    with session_factory() as session:
        rankings = []
        if mode in (SearchMode.VECTOR, SearchMode.HYBRID):
            vector = embedder.embed([query])[0]
            rankings.append(
                session.execute(
                    select(KbChunk.id)
                    .order_by(KbChunk.embedding.cosine_distance(vector))
                    .limit(CANDIDATES)
                )
                .scalars()
                .all()
            )
        if mode in (SearchMode.TEXT, SearchMode.HYBRID):
            terms = func.websearch_to_tsquery("english", _any_term(query))
            rankings.append(
                session.execute(
                    select(KbChunk.id)
                    .where(KbChunk.search.op("@@")(terms))
                    .order_by(func.ts_rank_cd(KbChunk.search, terms).desc())
                    .limit(CANDIDATES)
                )
                .scalars()
                .all()
            )
        scores: dict[object, float] = {}
        for ranking in rankings:
            for rank, chunk_id in enumerate(ranking, start=1):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (RRF_K + rank)
        top = sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)[:limit]
        if not top:
            return []
        rows = {
            row.id: row
            for row in session.execute(
                select(KbChunk.id, KbChunk.content, KbDocument.title, KbDocument.url)
                .join(KbDocument, KbDocument.id == KbChunk.document_id)
                .where(KbChunk.id.in_(top))
            )
        }
    return [KbHit(rows[key].title, rows[key].url, rows[key].content, scores[key]) for key in top]


def _any_term(query: str) -> str:
    """Questions rarely contain every word of a passage, so match any of their terms."""
    words = "".join(c if c.isalnum() else " " for c in query).split()
    return " OR ".join(words)
