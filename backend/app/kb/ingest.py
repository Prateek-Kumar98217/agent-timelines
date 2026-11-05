"""Loads the corpus into the knowledge base: `python -m app.kb.ingest path/to/corpus.jsonl`.

Safe to run repeatedly. Unchanged documents are skipped, and changed ones are replaced.
"""

import hashlib
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select

from app.db.models import KbChunk, KbDocument
from app.db.session import SessionFactory
from app.domain.scratchpad import split_chunks
from app.ports import Embedder

CHUNK_CHARS = 800
BATCH_SIZE = 64


@dataclass(frozen=True)
class IngestResult:
    added: int
    unchanged: int
    chunks: int


def ingest(
    session_factory: SessionFactory, embedder: Embedder, documents: Iterable[dict[str, str]]
) -> IngestResult:
    added = unchanged = chunk_count = 0
    with session_factory() as session:
        rows = session.execute(select(KbDocument.url, KbDocument.content_hash)).all()
    known = {row.url: row.content_hash for row in rows}
    for document in documents:
        content_hash = hashlib.sha256(document["text"].encode()).hexdigest()
        if known.get(document["url"]) == content_hash:
            unchanged += 1
            continue
        # A title line in each chunk helps both search methods when a passage only says "it".
        chunks = [
            f"{document['title']}: {chunk}" for chunk in split_chunks(document["text"], CHUNK_CHARS)
        ]
        vectors: list[list[float]] = []
        for start in range(0, len(chunks), BATCH_SIZE):
            vectors.extend(embedder.embed(chunks[start : start + BATCH_SIZE]))
        with session_factory() as session:
            session.execute(delete(KbDocument).where(KbDocument.url == document["url"]))
            row = KbDocument(
                url=document["url"],
                title=document["title"],
                topic=document["topic"],
                content_hash=content_hash,
            )
            session.add(row)
            session.flush()
            session.add_all(
                KbChunk(document_id=row.id, chunk_index=index, content=chunk, embedding=vector)
                for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
            )
            session.commit()
        added += 1
        chunk_count += len(chunks)
    return IngestResult(added, unchanged, chunk_count)


def read_corpus(path: Path) -> list[dict[str, str]]:
    with path.open() as file:
        return [json.loads(line) for line in file]


def main() -> None:
    from app.adapters.embeddings import FastEmbedEmbedder
    from app.db.session import make_session_factory
    from app.settings import get_settings

    settings = get_settings()
    result = ingest(
        make_session_factory(settings.database_url),
        FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir),
        read_corpus(Path(sys.argv[1])),
    )
    print(f"added {result.added}, unchanged {result.unchanged}, chunks {result.chunks}")


if __name__ == "__main__":
    main()
