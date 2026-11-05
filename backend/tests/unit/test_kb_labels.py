"""The labelled questions are only meaningful if the corpus really contains their answers."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "evals"))

from kb_labels import Label, load_articles, load_labels, problems  # noqa: E402


def test_every_label_is_backed_by_its_article() -> None:
    labels = load_labels()
    assert len(labels) >= 100
    assert problems(labels, load_articles()) == []


def test_problems_catches_bad_labels() -> None:
    articles = {"Mars": {"text": "Mars is red."}}
    assert problems([Label("q", "Mars", "blue")], articles)
    assert problems([Label("q", "Venus", "red")], articles)
    assert problems([Label("q", "Mars", "red"), Label("q", "Mars", "red")], articles)
    assert problems([Label("q", "Mars", "RED")], articles) == []
