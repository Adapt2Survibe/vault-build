"""Real-model integration test (CONTRACTS.md cross-cutting rules).

Marked slow: loads the actual nomic embedding model (cached locally after the
first run; downloads on a fresh machine). Excluded by default; run with:

    .venv/bin/python -m pytest -m slow -q
"""

from __future__ import annotations

import time

import pytest

from vault_librarian import tools
from vault_librarian.config import load_config
from vault_librarian.embedder import Embedder
from vault_librarian.indexer import reindex
from vault_librarian.store import Store

from .conftest import write_config_yaml

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def real_cfg(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("realmodel")
    root = tmp / "vault-personal"
    from .conftest import make_vault_tree

    make_vault_tree(root)
    cfg_path = tmp / "config.yaml"
    write_config_yaml(cfg_path, root, tmp / "index.sqlite", dimensions=768)
    cfg = load_config(cfg_path)
    cfg = cfg.model_copy(
        update={
            "embedding": cfg.embedding.model_copy(
                update={"model": "nomic-ai/nomic-embed-text-v1.5", "dimensions": 768}
            )
        }
    )
    return cfg


def test_real_model_end_to_end_ranking_and_latency(real_cfg):
    emb = Embedder(real_cfg.embedding.model, device=real_cfg.embedding.device)
    with Store(real_cfg.store.path, dimensions=768) as store:
        stats = reindex(real_cfg, store, emb)
        assert stats.indexed == 3 and stats.errors == 0

        # Semantic ranking with the real model: a paraphrase, not keywords.
        out = tools.search_wiki(real_cfg, store, emb, "software that records what you experience")
        assert out["results"], out
        assert out["results"][0]["page_id"] == "wiki/tools/engramme"

        # The plan's success criterion: warm /recall under 1 second. Best-of-5
        # (min, not mean) so concurrent machine load can't flake the assertion.
        timings = []
        for _ in range(5):
            t0 = time.perf_counter()
            tools.search_sources(real_cfg, store, emb, "who founded the memory startup?")
            timings.append(time.perf_counter() - t0)
        assert min(timings) < 1.0, f"warm search best-of-5 {min(timings):.2f}s — over the 1s bar"
