"""Measures knowledge base retrieval with the labelled questions.

Needs the corpus ingested: uv run python -m app.kb.ingest kb/corpus.jsonl
Run with: uv run python evals/recall.py
"""

import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from kb_labels import Label, load_articles, load_labels, problems

from app.adapters.embeddings import FastEmbedEmbedder
from app.db.session import make_session_factory
from app.kb.search import KbHit, SearchMode, search_kb
from app.settings import get_settings

CUTOFFS = (1, 3, 5, 10)
OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "eval" / "recall.json"


@dataclass(frozen=True)
class ModeResult:
    mode: str
    questions: int
    document_recall: dict[int, float]
    passage_recall: dict[int, float]
    mean_reciprocal_rank: float
    median_ms: float


def rank_of(hits: list[KbHit], label: Label, *, passage: bool) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if hit.title == label.title and (
            not passage or label.snippet.lower() in hit.content.lower()
        ):
            return rank
    return None


def score_mode(session_factory, embedder, labels: list[Label], mode: SearchMode) -> ModeResult:  # type: ignore[no-untyped-def]
    documents, passages, times = [], [], []
    for label in labels:
        started = time.perf_counter()
        hits = search_kb(session_factory, embedder, label.question, max(CUTOFFS), mode)
        times.append((time.perf_counter() - started) * 1000)
        documents.append(rank_of(hits, label, passage=False))
        passages.append(rank_of(hits, label, passage=True))

    def recall(ranks: list[int | None], k: int) -> float:
        return sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks)

    return ModeResult(
        mode=mode.value,
        questions=len(labels),
        document_recall={k: recall(documents, k) for k in CUTOFFS},
        passage_recall={k: recall(passages, k) for k in CUTOFFS},
        mean_reciprocal_rank=statistics.mean(1 / rank if rank else 0.0 for rank in documents),
        median_ms=statistics.median(times),
    )


def main() -> None:
    labels = load_labels()
    invalid = problems(labels, load_articles())
    if invalid:
        raise SystemExit("invalid labels:\n" + "\n".join(invalid))
    settings = get_settings()
    session_factory = make_session_factory(settings.database_url)
    embedder = FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir)
    embedder.embed(["warm up"])
    results = [score_mode(session_factory, embedder, labels, mode) for mode in SearchMode]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps([asdict(result) for result in results], indent=2) + "\n")
    print(f"{len(labels)} questions, {settings.embedding_model}")
    for result in results:
        print(
            f"{result.mode:7} doc@1 {result.document_recall[1]:.2f}"
            f" @3 {result.document_recall[3]:.2f} @5 {result.document_recall[5]:.2f}"
            f" @10 {result.document_recall[10]:.2f} | passage@5 {result.passage_recall[5]:.2f}"
            f" | MRR {result.mean_reciprocal_rank:.2f} | {result.median_ms:.0f} ms"
        )


if __name__ == "__main__":
    main()
