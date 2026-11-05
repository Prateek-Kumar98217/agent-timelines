"""Local embeddings with fastembed. The model runs in-process, with no API or key."""

from collections.abc import Sequence
from threading import Lock
from typing import Any


class FastEmbedEmbedder:
    """Loads the model on first use, so processes that never embed don't pay for it."""

    dimensions = 384

    def __init__(self, model: str, cache_dir: str | None = None) -> None:
        self._model_name = model
        self._cache_dir = cache_dir
        self._model: Any = None
        self._lock = Lock()

    def _loaded(self) -> Any:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(self._model_name, cache_dir=self._cache_dir)
            return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._loaded().embed(list(texts))]
