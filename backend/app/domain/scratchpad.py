"""A task's retrieval store: pages fetched and notes saved, searched by meaning.

Each chunk belongs to one branch and names the node that produced it. A fork copies only the chunks
from nodes on its own path, so a branch can never read what another branch found.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import ScratchpadChunk
from app.db.session import SessionFactory
from app.ports import Embedder, ToolContext

CHUNK_CHARS = 800
PAGE_TOOL = "page_fetch"
# Bounds what one page can add to the database, however long the page is.
MAX_PAGE_CHUNKS = 64


@dataclass(frozen=True)
class ScratchpadHit:
    content: str
    distance: float


def split_chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split on paragraph breaks where possible, and hard-split anything longer than `size`."""
    chunks: list[str] = []
    current = ""
    for paragraph in (part.strip() for part in text.split("\n\n")):
        if not paragraph:
            continue
        if current and len(current) + 2 + len(paragraph) > size:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
        while len(current) > size:
            chunks.append(current[:size])
            current = current[size:]
    if current:
        chunks.append(current)
    return chunks


def page_chunks(content: str) -> list[str]:
    """Split a fetched page, labelling each chunk with where it came from."""
    title, _, rest = content.partition("\n")
    url, _, text = rest.partition("\n")
    if not url.startswith(("http://", "https://")):
        # An edited result doesn't have to keep the page format.
        return split_chunks(content)[:MAX_PAGE_CHUNKS]
    return [f"From {title} ({url})\n{chunk}" for chunk in split_chunks(text)[:MAX_PAGE_CHUNKS]]


def embed_page(
    embedder: Embedder, tool_name: str, content: str, is_error: bool
) -> list[tuple[str, list[float]]]:
    """Prepare a tool result for the store. This is slow, so call it outside any transaction."""
    if tool_name != PAGE_TOOL or is_error:
        return []
    chunks = page_chunks(content)
    return list(zip(chunks, embedder.embed(chunks), strict=True)) if chunks else []


def add_page(
    session: Session,
    branch_id: UUID,
    node_id: UUID,
    page: list[tuple[str, list[float]]],
) -> None:
    """Store a page in the transaction that creates its result node, so neither exists alone."""
    if page:
        session.execute(
            insert(ScratchpadChunk)
            .values(
                [
                    {
                        "id": uuid4(),
                        "branch_id": branch_id,
                        "source_node_id": node_id,
                        "chunk_index": index,
                        "content": chunk,
                        "embedding": vector,
                    }
                    for index, (chunk, vector) in enumerate(page)
                ]
            )
            .on_conflict_do_nothing(constraint="uq_scratchpad_write_once")
        )


def write_note(
    session_factory: SessionFactory, embedder: Embedder, context: ToolContext, text: str
) -> int:
    """Save a note. A retry of the same tool call writes nothing new."""
    chunks = split_chunks(text)
    if not chunks:
        return 0
    vectors = embedder.embed(chunks)
    with session_factory() as session:
        session.execute(
            insert(ScratchpadChunk)
            .values(
                [
                    {
                        "id": uuid4(),
                        "branch_id": context.branch_id,
                        "source_node_id": context.tool_call_node_id,
                        "chunk_index": index,
                        "content": chunk,
                        "embedding": vector,
                    }
                    for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
                ]
            )
            .on_conflict_do_nothing(constraint="uq_scratchpad_write_once")
        )
        session.commit()
    return len(chunks)


def search_notes(
    session_factory: SessionFactory,
    embedder: Embedder,
    branch_id: UUID,
    query: str,
    limit: int = 5,
) -> list[ScratchpadHit]:
    vector = embedder.embed([query])[0]
    distance = ScratchpadChunk.embedding.cosine_distance(vector).label("distance")
    with session_factory() as session:
        rows = session.execute(
            select(ScratchpadChunk.content, distance)
            .where(ScratchpadChunk.branch_id == branch_id)
            .order_by(distance)
            .limit(limit)
        ).all()
    return [ScratchpadHit(row.content, float(row.distance)) for row in rows]
