"""Loads the labelled knowledge base questions and checks them against the corpus."""

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABELS = Path(__file__).with_name("kb_labels.tsv")
CORPUS = ROOT / "kb" / "corpus.jsonl"


@dataclass(frozen=True)
class Label:
    question: str
    title: str
    snippet: str


def load_labels(path: Path = LABELS) -> list[Label]:
    labels = []
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            question, title, snippet = line.split("\t")
            labels.append(Label(question, title, snippet))
    return labels


def load_articles(path: Path = CORPUS) -> dict[str, dict[str, str]]:
    with path.open() as file:
        documents = [json.loads(line) for line in file]
    return {document["title"]: document for document in documents}


def problems(labels: list[Label], articles: dict[str, dict[str, str]]) -> list[str]:
    """A label is only valid if its article exists and really contains the snippet."""
    found = []
    seen = set()
    for label in labels:
        article = articles.get(label.title)
        if article is None:
            found.append(f"no article '{label.title}': {label.question}")
        elif label.snippet.lower() not in article["text"].lower():
            found.append(f"'{label.snippet}' not in '{label.title}': {label.question}")
        if label.question in seen:
            found.append(f"duplicate question: {label.question}")
        seen.add(label.question)
    return found
