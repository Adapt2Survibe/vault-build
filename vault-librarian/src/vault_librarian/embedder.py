"""Embedding wrapper for vault-librarian (CONTRACTS.md § embedder.py).

Lazy-loads the sentence-transformers model on the first embed call so server
start stays fast for the stdio handshake. When the model name contains
"nomic" (case-insensitive), nomic's task prefixes are prepended — retrieval
quality depends on them. `_loader` is injectable so unit tests never load
the real model.

Hardening (2026-06-12 review wall):
- a failed model load is cached and re-raised instantly — a 500-file reindex
  must not re-attempt a dead network 500 times;
- model load and encode run under redirect_stdout(stderr): third-party code
  printing to stdout would corrupt the MCP stdio protocol framing;
- `revision` pins the model repo commit (trust_remote_code supply chain).
"""

import sys
from collections.abc import Callable
from contextlib import redirect_stdout
from typing import Any

import numpy as np


class Embedder:
    """Wraps a SentenceTransformer model behind a small, test-friendly seam."""

    def __init__(
        self,
        model_name: str,
        device: str = "cpu",
        revision: str | None = None,
        _loader: Callable | None = None,
    ):
        self._model_name = model_name
        self._device = device
        self._revision = revision
        self._loader = _loader
        self._model: Any | None = None
        self._load_error: Exception | None = None
        self._is_nomic = "nomic" in model_name.lower()

    def _get_model(self) -> Any:
        if self._load_error is not None:
            raise RuntimeError(
                f"embedding model {self._model_name!r} failed to load on a previous attempt:"
                f" {self._load_error}"
            ) from self._load_error
        if self._model is None:
            loader = self._loader
            if loader is None:  # import lazily: unit tests must never touch this
                from sentence_transformers import SentenceTransformer

                loader = SentenceTransformer
            kwargs: dict[str, Any] = {"device": self._device, "trust_remote_code": True}
            if self._revision is not None:
                kwargs["revision"] = self._revision
            try:
                with redirect_stdout(sys.stderr):
                    self._model = loader(self._model_name, **kwargs)
            except Exception as exc:
                self._load_error = exc
                raise
        return self._model

    def _encode(self, texts: list[str]) -> np.ndarray:
        model = self._get_model()
        with redirect_stdout(sys.stderr):
            out = model.encode(
                texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False
            )
        return np.asarray(out, dtype=np.float32)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """Embed document chunks. Returns (n, dim) float32, L2-normalized."""
        if not texts:
            return np.zeros((0,), dtype=np.float32)
        if self._is_nomic:
            texts = [f"search_document: {t}" for t in texts]
        return self._encode(texts)

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a search query. Returns (dim,) float32, L2-normalized."""
        if self._is_nomic:
            text = f"search_query: {text}"
        return self._encode([text])[0]
