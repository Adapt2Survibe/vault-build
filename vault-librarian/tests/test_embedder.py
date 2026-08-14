"""Tests for vault_librarian.embedder — written first, per CONTRACTS.md `embedder.py`.

The real SentenceTransformer is never loaded here (no network, no 500MB model):
a recording fake is injected via the `_loader` hook. The fake mimics the real
`encode` contract — honors `normalize_embeddings`, returns float64 vectors that
are NOT pre-normalized — so assertions on the Embedder's float32 cast and
normalized output are meaningful, not vacuous.
"""

import hashlib

import numpy as np
import pytest

from vault_librarian.embedder import Embedder

NOMIC_MODEL = "nomic-ai/nomic-embed-text-v1.5"
PLAIN_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 8


class RecordingModel:
    """Mimics SentenceTransformer.encode enough for unit tests."""

    def __init__(self, dim: int = DIM):
        self.dim = dim
        self.encode_calls: list[tuple[object, dict]] = []

    def _vec(self, text: str) -> np.ndarray:
        seed = int.from_bytes(hashlib.md5(text.encode()).digest()[:4], "big")
        rng = np.random.default_rng(seed)
        return rng.uniform(1.0, 2.0, self.dim)  # float64, norm >> 1: not normalized

    def encode(self, texts: object, **kwargs: object) -> np.ndarray:
        self.encode_calls.append((texts, dict(kwargs)))
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)  # type: ignore[arg-type]
        if items:
            out = np.stack([self._vec(t) for t in items])
        else:
            out = np.zeros((0, self.dim), dtype=np.float64)
        if kwargs.get("normalize_embeddings"):
            norms = np.linalg.norm(out, axis=1, keepdims=True)
            norms[norms == 0.0] = 1.0
            out = out / norms
        return out[0] if single else out


class RecordingLoader:
    """Stands in for the SentenceTransformer constructor; records call args."""

    def __init__(self, dim: int = DIM):
        self.model = RecordingModel(dim)
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args: object, **kwargs: object) -> RecordingModel:
        self.calls.append((args, dict(kwargs)))
        return self.model


@pytest.fixture
def loader() -> RecordingLoader:
    return RecordingLoader()


def texts_passed(call: tuple[object, dict]) -> list[str]:
    """Normalize the texts argument of a recorded encode call to a list."""
    texts = call[0]
    return [texts] if isinstance(texts, str) else list(texts)  # type: ignore[list-item]


class TestLazyLoading:
    def test_constructor_does_not_load(self, loader: RecordingLoader) -> None:
        Embedder(NOMIC_MODEL, _loader=loader)
        assert loader.calls == []

    def test_first_embed_documents_loads(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        emb.embed_documents(["hello"])
        assert len(loader.calls) == 1

    def test_embed_query_triggers_load(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        emb.embed_query("hello")
        assert len(loader.calls) == 1

    def test_model_loaded_once_and_cached(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        emb.embed_documents(["a"])
        emb.embed_query("b")
        emb.embed_documents(["c", "d"])
        assert len(loader.calls) == 1


class TestLoaderArgs:
    def test_loader_receives_model_name_device_and_trust_remote_code(
        self, loader: RecordingLoader
    ) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        emb.embed_documents(["hello"])
        args, kwargs = loader.calls[0]
        assert args == (NOMIC_MODEL,)
        assert kwargs == {"device": "cpu", "trust_remote_code": True}

    def test_custom_device_forwarded(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, device="mps", _loader=loader)
        emb.embed_documents(["hello"])
        _, kwargs = loader.calls[0]
        assert kwargs["device"] == "mps"
        assert kwargs["trust_remote_code"] is True


class TestNomicPrefixes:
    def test_document_prefix_for_nomic(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        emb.embed_documents(["alpha text", "beta text"])
        assert texts_passed(loader.model.encode_calls[-1]) == [
            "search_document: alpha text",
            "search_document: beta text",
        ]

    def test_query_prefix_for_nomic(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        emb.embed_query("what is engramme")
        assert texts_passed(loader.model.encode_calls[-1]) == ["search_query: what is engramme"]

    def test_nomic_match_is_case_insensitive(self, loader: RecordingLoader) -> None:
        emb = Embedder("Acme/NOMIC-Embed-Custom", _loader=loader)
        emb.embed_documents(["alpha"])
        emb.embed_query("beta")
        assert texts_passed(loader.model.encode_calls[0]) == ["search_document: alpha"]
        assert texts_passed(loader.model.encode_calls[1]) == ["search_query: beta"]

    def test_no_document_prefix_for_other_models(self, loader: RecordingLoader) -> None:
        emb = Embedder(PLAIN_MODEL, _loader=loader)
        emb.embed_documents(["alpha text"])
        assert texts_passed(loader.model.encode_calls[-1]) == ["alpha text"]

    def test_no_query_prefix_for_other_models(self, loader: RecordingLoader) -> None:
        emb = Embedder(PLAIN_MODEL, _loader=loader)
        emb.embed_query("what is engramme")
        assert texts_passed(loader.model.encode_calls[-1]) == ["what is engramme"]


class TestOutputShape:
    def test_documents_shape_and_dtype(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        out = emb.embed_documents(["a", "b", "c"])
        assert out.shape == (3, DIM)
        assert out.dtype == np.float32

    def test_documents_rows_are_l2_normalized(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        out = emb.embed_documents(["alpha text", "beta text"])
        assert np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)

    def test_query_shape_dtype_normalized(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        out = emb.embed_query("what is engramme")
        assert out.shape == (DIM,)
        assert out.dtype == np.float32
        assert np.isclose(float(np.linalg.norm(out)), 1.0, atol=1e-5)


class TestEncodeKwargs:
    def test_document_encode_kwargs(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        emb.embed_documents(["hello"])
        _, kwargs = loader.model.encode_calls[-1]
        assert kwargs["normalize_embeddings"] is True
        assert kwargs["batch_size"] == 32
        assert kwargs["show_progress_bar"] is False

    def test_query_encode_kwargs(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        emb.embed_query("hello")
        _, kwargs = loader.model.encode_calls[-1]
        assert kwargs["normalize_embeddings"] is True
        assert kwargs["batch_size"] == 32
        assert kwargs["show_progress_bar"] is False


class TestEmptyInput:
    def test_empty_documents_returns_empty_float32(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        out = emb.embed_documents([])
        assert out.shape == (0,)
        assert out.dtype == np.float32

    def test_empty_documents_does_not_load_model(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        emb.embed_documents([])
        assert loader.calls == []
        assert loader.model.encode_calls == []

    def test_empty_documents_after_load_skips_encode(self, loader: RecordingLoader) -> None:
        emb = Embedder(NOMIC_MODEL, _loader=loader)
        emb.embed_documents(["warm up"])
        n_calls = len(loader.model.encode_calls)
        out = emb.embed_documents([])
        assert out.shape == (0,)
        assert len(loader.model.encode_calls) == n_calls
