"""Shared test fixtures for vault-librarian.

Deliberately imports nothing from vault_librarian: test collection must survive
a half-built package (builders run their own test files while sibling modules
don't exist yet).
"""

import hashlib
import re
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

# uv re-flags .venv files UF_HIDDEN on every install, and Python 3.12 site.py
# skips hidden .pth files — silently disabling the editable install (recurred
# three times during the 2026-06-12 build). This fallback makes the test suite
# immune regardless of the .pth's flag state.
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


class FakeEmbedder:
    """Deterministic, dependency-free stand-in for Embedder.

    Bag-of-words hashing: texts sharing tokens get similar vectors, so ranking
    assertions (query 'memory startup' ranks the engramme doc first) hold
    without loading a real model. Matches the Embedder interface in CONTRACTS.md.
    """

    def __init__(self, dimensions: int = 64):
        self.dimensions = dimensions
        self.calls: list[tuple[str, object]] = []

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dimensions, dtype=np.float32)
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dimensions] += 1.0
        norm = float(np.linalg.norm(v))
        if norm == 0.0:
            v[0] = 1.0
            norm = 1.0
        return (v / norm).astype(np.float32)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        self.calls.append(("documents", list(texts)))
        if not texts:
            return np.zeros((0,), dtype=np.float32)  # mirror the real Embedder's shape
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        self.calls.append(("query", text))
        return self._vec(text)


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


def make_vault_tree(root) -> None:
    """Build a minimal personal-vault tree under `root` (a pathlib.Path).

    Two wiki pages, one source, plus _index.md files that discovery must skip.
    """
    for d in ["sources", "wiki/tools", "wiki/concepts", "journal", "inbox", "_maintenance"]:
        (root / d).mkdir(parents=True, exist_ok=True)

    (root / "wiki" / "_index.md").write_text("# Wiki index\n")
    (root / "sources" / "_index.md").write_text("# Sources index\n")

    (root / "wiki" / "tools" / "engramme.md").write_text(
        textwrap.dedent(
            """\
            ---
            id: wiki-engramme
            title: Engramme
            created: 2026-06-01
            tags: [needs-synthesis]
            sources: [src-2026-06-01-engramme-site]
            last_synthesized: null
            ---

            # Engramme

            Engramme is a memory augmentation startup. Their product records
            everything and makes personal memory searchable.
            """
        )
    )
    (root / "wiki" / "concepts" / "spaced-repetition.md").write_text(
        textwrap.dedent(
            """\
            ---
            id: wiki-spaced-repetition
            title: Spaced repetition
            created: 2026-06-02
            tags: []
            sources: []
            last_synthesized: null
            ---

            # Spaced repetition

            Reviewing flashcards at increasing intervals strengthens recall.
            Anki schedules card reviews with an exponential backoff curve.
            """
        )
    )
    (root / "sources" / "2026-06-01-engramme-site.md").write_text(
        textwrap.dedent(
            """\
            ---
            id: src-2026-06-01-engramme-site
            title: Engramme — Remember Everything
            type: website
            ingested: 2026-06-01
            url: https://www.engramme.example/
            ---

            Engramme is a memory augmentation startup building searchable
            personal memory. The founding team previously built search
            infrastructure at a large company.
            """
        )
    )


@pytest.fixture
def vault_root(tmp_path):
    """A populated temp vault tree; returns its root path."""
    root = tmp_path / "vault-personal"
    make_vault_tree(root)
    return root


def write_config_yaml(path, vault_root, db_path, dimensions: int = 64) -> None:
    """Write a minimal valid config YAML for tests (path: pathlib.Path)."""
    path.write_text(
        textwrap.dedent(
            f"""\
            vault:
              name: personal
              root: {vault_root}
            embedding:
              model: fake-test-model
              dimensions: {dimensions}
              device: cpu
            store:
              backend: sqlite-vec
              path: {db_path}
            """
        )
    )


@pytest.fixture
def config_path(tmp_path, vault_root):
    """A config YAML pointing at the populated temp vault; returns its path."""
    p = tmp_path / "config.test.yaml"
    write_config_yaml(p, vault_root, tmp_path / "index.sqlite")
    return p
