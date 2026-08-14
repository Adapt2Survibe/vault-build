"""Tests for vault_librarian.store (CONTRACTS.md § store.py).

Written before the implementation (TDD). Chunk is duck-typed via a local
namedtuple: the store must accept any object with .text/.heading/.pos and
never import chunker.py.
"""

import sqlite3
from collections import namedtuple
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import sqlite_vec

from vault_librarian.store import Hit, Store

from .conftest import FakeEmbedder

Chunk = namedtuple("Chunk", ["text", "heading", "pos"])

DIM = 64

ENGRAMME_TEXTS = [
    "Engramme is a memory augmentation startup.",
    "Their product records everything and makes personal memory searchable.",
]
SPACED_REP_TEXTS = [
    "Reviewing flashcards at increasing intervals strengthens recall.",
    "Anki schedules card reviews with an exponential backoff curve.",
]


def make_chunks(texts: list[str], heading: str = "") -> list[Chunk]:
    return [Chunk(text=t, heading=heading, pos=i) for i, t in enumerate(texts)]


def add_doc(
    store: Store,
    embedder: FakeEmbedder,
    kind: str,
    doc_id: str,
    texts: list[str],
    heading: str = "",
    content_hash: str = "hash-1",
) -> int:
    chunks = make_chunks(texts, heading=heading)
    embeddings = embedder.embed_documents([c.text for c in chunks])
    return store.upsert_document(
        kind=kind,
        doc_id=doc_id,
        file_path=f"{doc_id}.md",
        content_hash=content_hash,
        chunks=chunks,
        embeddings=embeddings,
    )


def raw_counts(db_path: Path) -> dict[str, int]:
    """Row counts straight from the db file, bypassing the Store API."""
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    out = {
        "vec_wiki": conn.execute("SELECT count(*) FROM vec_wiki").fetchone()[0],
        "vec_source": conn.execute("SELECT count(*) FROM vec_source").fetchone()[0],
        "chunks": conn.execute("SELECT count(*) FROM chunks").fetchone()[0],
        "documents": conn.execute("SELECT count(*) FROM documents").fetchone()[0],
    }
    conn.close()
    return out


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "index.sqlite"


@pytest.fixture
def store(db_path: Path):
    s = Store(db_path, dimensions=DIM)
    yield s
    s.close()


# --- construction -----------------------------------------------------------


def test_parent_dir_auto_created(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "deeper" / "index.sqlite"
    s = Store(db, dimensions=DIM)
    assert db.parent.is_dir()
    assert s.counts()["wiki_docs"] == 0  # store is usable
    s.close()


def test_accepts_str_db_path(tmp_path: Path, fake_embedder: FakeEmbedder) -> None:
    db = tmp_path / "index.sqlite"
    s = Store(str(db), dimensions=DIM)
    add_doc(s, fake_embedder, "wiki", "wiki/a", ["alpha beta"])
    s.close()
    assert db.exists()


def test_journal_mode_is_wal(db_path: Path, store: Store) -> None:
    conn = sqlite3.connect(str(db_path))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_busy_timeout_is_set(store: Store) -> None:
    # contract: PRAGMA busy_timeout=5000 (reindex may run while the server reads)
    assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_context_manager_closes(db_path: Path, fake_embedder: FakeEmbedder) -> None:
    with Store(db_path, dimensions=DIM) as s:
        assert isinstance(s, Store)
        add_doc(s, fake_embedder, "wiki", "wiki/a", ["alpha beta"])
    with pytest.raises(sqlite3.ProgrammingError):
        s.counts()


def test_hit_is_dataclass_with_contract_fields() -> None:
    assert is_dataclass(Hit)
    assert [f.name for f in fields(Hit)] == ["doc_id", "heading", "text", "pos", "score"]


# --- upsert -----------------------------------------------------------------


def test_upsert_returns_chunk_count(store: Store, fake_embedder: FakeEmbedder) -> None:
    n = add_doc(store, fake_embedder, "wiki", "wiki/tools/engramme", ENGRAMME_TEXTS)
    assert n == 2


def test_upsert_dim_mismatch_raises_value_error(store: Store) -> None:
    wrong_dim = FakeEmbedder(dimensions=32)
    chunks = make_chunks(["alpha beta"])
    embeddings = wrong_dim.embed_documents(["alpha beta"])
    with pytest.raises(ValueError):
        store.upsert_document("wiki", "wiki/a", "wiki/a.md", "h", chunks, embeddings)


def test_upsert_length_mismatch_raises_value_error(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    chunks = make_chunks(["alpha", "beta", "gamma"])
    embeddings = fake_embedder.embed_documents(["alpha", "beta"])  # one short
    with pytest.raises(ValueError):
        store.upsert_document("wiki", "wiki/a", "wiki/a.md", "h", chunks, embeddings)


def test_upsert_unknown_kind_raises_value_error(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    with pytest.raises(ValueError):
        add_doc(store, fake_embedder, "journal", "journal/today", ["alpha"])


def test_failed_upsert_preserves_existing_data(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["memory augmentation startup"])
    bad = FakeEmbedder(dimensions=32)
    chunks = make_chunks(["replacement text"])
    with pytest.raises(ValueError):
        store.upsert_document(
            "wiki", "wiki/a", "wiki/a.md", "h2", chunks, bad.embed_documents(["replacement text"])
        )
    hits = store.search("wiki", fake_embedder.embed_query("memory augmentation startup"), 5)
    assert hits and hits[0].text == "memory augmentation startup"
    assert store.indexed_state() == {"wiki/a": "hash-1"}


def test_upsert_zero_chunks_registers_document(store: Store, fake_embedder: FakeEmbedder) -> None:
    n = store.upsert_document(
        "wiki", "wiki/empty", "wiki/empty.md", "h", [], fake_embedder.embed_documents([])
    )
    assert n == 0
    assert store.get_document("wiki/empty") is not None
    assert store.counts()["wiki_chunks"] == 0


def test_upsert_accepts_float64_embeddings(store: Store, fake_embedder: FakeEmbedder) -> None:
    chunks = make_chunks(["alpha beta gamma"])
    embeddings = fake_embedder.embed_documents(["alpha beta gamma"]).astype(np.float64)
    n = store.upsert_document("wiki", "wiki/a", "wiki/a.md", "h", chunks, embeddings)
    assert n == 1
    hits = store.search("wiki", fake_embedder.embed_query("alpha beta gamma"), 5)
    assert hits[0].doc_id == "wiki/a"


def test_upsert_normalizes_unnormalized_embeddings(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    chunks = make_chunks(["alpha beta gamma"])
    embeddings = fake_embedder.embed_documents(["alpha beta gamma"]) * 5.0  # break the norm
    store.upsert_document("wiki", "wiki/a", "wiki/a.md", "h", chunks, embeddings)
    hits = store.search("wiki", fake_embedder.embed_query("alpha beta gamma"), 5)
    assert hits[0].score == pytest.approx(1.0)


# --- re-upsert (replace semantics) ------------------------------------------


def test_reupsert_replaces_chunks_no_orphan_vec_rows(
    db_path: Path, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["one", "two", "three"])
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["four", "five"], content_hash="hash-2")
    raw = raw_counts(db_path)
    assert raw["chunks"] == 2
    assert raw["vec_wiki"] == raw["chunks"]  # no orphan vec rows
    assert raw["vec_source"] == 0
    assert raw["documents"] == 1


def test_reupsert_old_chunks_not_searchable(store: Store, fake_embedder: FakeEmbedder) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["zebra quagga okapi"])
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["totally different words"], content_hash="h2")
    hits = store.search("wiki", fake_embedder.embed_query("zebra quagga okapi"), 10)
    assert all(h.text != "zebra quagga okapi" for h in hits)


def test_reupsert_updates_content_hash(store: Store, fake_embedder: FakeEmbedder) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["alpha"], content_hash="hash-1")
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["beta"], content_hash="hash-2")
    assert store.indexed_state() == {"wiki/a": "hash-2"}


def test_reupsert_with_changed_kind_moves_vectors(
    db_path: Path, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "doc-x", ["alpha beta"])
    add_doc(store, fake_embedder, "source", "doc-x", ["alpha beta"], content_hash="h2")
    raw = raw_counts(db_path)
    assert raw["vec_wiki"] == 0
    assert raw["vec_source"] == 1
    assert store.get_document("doc-x")["kind"] == "source"


def test_reupsert_to_zero_chunks_removes_old_rows(
    db_path: Path, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["one", "two"])
    store.upsert_document(
        "wiki", "wiki/a", "wiki/a.md", "h2", [], fake_embedder.embed_documents([])
    )
    raw = raw_counts(db_path)
    assert raw["chunks"] == 0
    assert raw["vec_wiki"] == 0
    assert raw["documents"] == 1


# --- delete -----------------------------------------------------------------


def test_delete_document_removes_doc_chunks_and_vectors(
    db_path: Path, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["alpha", "beta"])
    add_doc(store, fake_embedder, "wiki", "wiki/b", ["gamma"], content_hash="h2")
    store.delete_document("wiki/a")
    assert store.get_document("wiki/a") is None
    assert "wiki/a" not in store.indexed_state()
    raw = raw_counts(db_path)
    assert raw["chunks"] == 1
    assert raw["vec_wiki"] == 1
    assert raw["documents"] == 1


def test_delete_document_missing_is_noop(store: Store) -> None:
    store.delete_document("wiki/never-existed")  # must not raise


def test_deleted_document_not_searchable(store: Store, fake_embedder: FakeEmbedder) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["zebra quagga okapi"])
    store.delete_document("wiki/a")
    assert store.search("wiki", fake_embedder.embed_query("zebra quagga okapi"), 5) == []


# --- search -----------------------------------------------------------------


def test_search_round_trip_ranks_by_similarity(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/tools/engramme", ENGRAMME_TEXTS)
    add_doc(
        store,
        fake_embedder,
        "wiki",
        "wiki/concepts/spaced-repetition",
        SPACED_REP_TEXTS,
        content_hash="h2",
    )
    hits = store.search("wiki", fake_embedder.embed_query("memory augmentation startup"), 5)
    assert hits[0].doc_id == "wiki/tools/engramme"
    assert hits[0].text == ENGRAMME_TEXTS[0]


def test_search_returns_hit_metadata(store: Store, fake_embedder: FakeEmbedder) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["alpha beta gamma"], heading="Install > Setup")
    hits = store.search("wiki", fake_embedder.embed_query("alpha beta gamma"), 5)
    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, Hit)
    assert hit.doc_id == "wiki/a"
    assert hit.heading == "Install > Setup"
    assert hit.text == "alpha beta gamma"
    assert hit.pos == 0
    assert isinstance(hit.score, float)


def test_search_exact_match_scores_one(store: Store, fake_embedder: FakeEmbedder) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["alpha beta gamma"])
    hits = store.search("wiki", fake_embedder.embed_query("alpha beta gamma"), 5)
    assert hits[0].score == pytest.approx(1.0)


def test_search_scores_in_unit_interval_and_descending(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["memory augmentation startup"])
    add_doc(store, fake_embedder, "wiki", "wiki/b", ["memory and other things"], content_hash="h2")
    add_doc(
        store, fake_embedder, "wiki", "wiki/c", ["completely unrelated topic"], content_hash="h3"
    )
    hits = store.search("wiki", fake_embedder.embed_query("memory augmentation startup"), 10)
    assert len(hits) == 3
    scores = [h.score for h in hits]
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores == sorted(scores, reverse=True)


def test_search_scores_rounded_to_4dp(store: Store, fake_embedder: FakeEmbedder) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["memory augmentation startup"])
    add_doc(
        store, fake_embedder, "wiki", "wiki/b", ["memory searchable product"], content_hash="h2"
    )
    hits = store.search("wiki", fake_embedder.embed_query("memory startup"), 10)
    assert all(h.score == round(h.score, 4) for h in hits)


def test_search_empty_index_returns_empty_list(store: Store, fake_embedder: FakeEmbedder) -> None:
    assert store.search("wiki", fake_embedder.embed_query("anything"), 5) == []
    assert store.search("source", fake_embedder.embed_query("anything"), 5) == []


def test_search_unknown_kind_raises_value_error(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    with pytest.raises(ValueError):
        store.search("journal", fake_embedder.embed_query("anything"), 5)


def test_search_kind_isolation_wiki_never_returns_source(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/engramme", ["memory augmentation startup"])
    add_doc(
        store,
        fake_embedder,
        "source",
        "src-engramme",
        ["memory augmentation startup"],
        content_hash="h2",
    )
    hits = store.search("wiki", fake_embedder.embed_query("memory augmentation startup"), 10)
    assert {h.doc_id for h in hits} == {"wiki/engramme"}


def test_search_kind_isolation_source_never_returns_wiki(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/engramme", ["memory augmentation startup"])
    add_doc(
        store,
        fake_embedder,
        "source",
        "src-engramme",
        ["memory augmentation startup"],
        content_hash="h2",
    )
    hits = store.search("source", fake_embedder.embed_query("memory augmentation startup"), 10)
    assert {h.doc_id for h in hits} == {"src-engramme"}


def test_search_top_k_caps_results(store: Store, fake_embedder: FakeEmbedder) -> None:
    for i in range(5):
        add_doc(store, fake_embedder, "wiki", f"wiki/doc{i}", [f"memory note number {i}"])
    hits = store.search("wiki", fake_embedder.embed_query("memory note"), 2)
    assert len(hits) == 2


def test_search_top_k_larger_than_index_returns_all(
    store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["alpha"])
    add_doc(store, fake_embedder, "wiki", "wiki/b", ["beta"], content_hash="h2")
    hits = store.search("wiki", fake_embedder.embed_query("alpha"), 50)
    assert len(hits) == 2


def test_search_accepts_float64_query(store: Store, fake_embedder: FakeEmbedder) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["alpha beta gamma"])
    query = fake_embedder.embed_query("alpha beta gamma").astype(np.float64)
    hits = store.search("wiki", query, 5)
    assert hits[0].doc_id == "wiki/a"


# --- metadata accessors ------------------------------------------------------


def test_indexed_state_maps_doc_id_to_hash(store: Store, fake_embedder: FakeEmbedder) -> None:
    assert store.indexed_state() == {}
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["alpha"], content_hash="aaa")
    add_doc(store, fake_embedder, "source", "src-b", ["beta"], content_hash="bbb")
    assert store.indexed_state() == {"wiki/a": "aaa", "src-b": "bbb"}


def test_get_document_returns_metadata(store: Store, fake_embedder: FakeEmbedder) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["alpha"], content_hash="aaa")
    doc = store.get_document("wiki/a")
    assert doc is not None
    assert set(doc) == {"doc_id", "kind", "file_path", "content_hash", "indexed_at"}
    assert doc["doc_id"] == "wiki/a"
    assert doc["kind"] == "wiki"
    assert doc["file_path"] == "wiki/a.md"
    assert doc["content_hash"] == "aaa"
    datetime.fromisoformat(doc["indexed_at"])  # ISO-parseable, must not raise


def test_get_document_missing_returns_none(store: Store) -> None:
    assert store.get_document("wiki/never-existed") is None


def test_counts_empty_store(store: Store) -> None:
    assert store.counts() == {
        "wiki_docs": 0,
        "source_docs": 0,
        "wiki_chunks": 0,
        "source_chunks": 0,
    }


def test_counts_after_upserts(store: Store, fake_embedder: FakeEmbedder) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["one", "two"])
    add_doc(store, fake_embedder, "wiki", "wiki/b", ["three"], content_hash="h2")
    add_doc(store, fake_embedder, "source", "src-c", ["four", "five", "six"], content_hash="h3")
    assert store.counts() == {
        "wiki_docs": 2,
        "source_docs": 1,
        "wiki_chunks": 3,
        "source_chunks": 3,
    }


def test_last_indexed_at_none_when_empty(store: Store) -> None:
    assert store.last_indexed_at() is None


def test_last_indexed_at_returns_iso_string(store: Store, fake_embedder: FakeEmbedder) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["alpha"])
    stamp = store.last_indexed_at()
    assert isinstance(stamp, str)
    datetime.fromisoformat(stamp)  # must not raise


# --- persistence -------------------------------------------------------------


def test_persistence_across_close_reopen(db_path: Path, fake_embedder: FakeEmbedder) -> None:
    s1 = Store(db_path, dimensions=DIM)
    add_doc(s1, fake_embedder, "wiki", "wiki/tools/engramme", ENGRAMME_TEXTS, content_hash="aaa")
    s1.close()

    s2 = Store(db_path, dimensions=DIM)
    try:
        assert s2.indexed_state() == {"wiki/tools/engramme": "aaa"}
        hits = s2.search("wiki", fake_embedder.embed_query("memory augmentation startup"), 5)
        assert hits and hits[0].doc_id == "wiki/tools/engramme"
    finally:
        s2.close()


# --- hygiene ------------------------------------------------------------------


def test_store_writes_nothing_to_stdout(
    db_path: Path, fake_embedder: FakeEmbedder, capsys: pytest.CaptureFixture[str]
) -> None:
    s = Store(db_path, dimensions=DIM)
    add_doc(s, fake_embedder, "wiki", "wiki/a", ["alpha beta"])
    s.search("wiki", fake_embedder.embed_query("alpha"), 5)
    s.counts()
    s.delete_document("wiki/a")
    s.close()
    assert capsys.readouterr().out == ""
