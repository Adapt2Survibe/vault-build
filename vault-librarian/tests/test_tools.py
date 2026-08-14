"""Tests for vault_librarian.tools (CONTRACTS.md § tools.py).

Written before the implementation (TDD). Chunks are duck-typed via a local
namedtuple (mirrors test_store.py). FakeEmbedder keeps ranking deterministic
and lets the no-embedder-call short-circuits be asserted via `.calls`.
"""

from collections import namedtuple
from pathlib import Path

import pytest

from vault_librarian.config import Config
from vault_librarian.store import Store
from vault_librarian.tools import get_page, search_sources, search_wiki

from .conftest import FakeEmbedder

Chunk = namedtuple("Chunk", ["text", "heading", "pos"])

DIM = 64

EMPTY_QUERY_NOTE = "empty query"
EMPTY_INDEX_NOTE = "index is empty — run: vault-librarian reindex"


def words(n: int) -> str:
    """n distinct space-separated words: 'w0 w1 ... w<n-1>'."""
    return " ".join(f"w{i}" for i in range(n))


def make_config(vault_root: Path, db_path: Path, **search_overrides: int) -> Config:
    return Config.model_validate(
        {
            "vault": {"name": "personal", "root": str(vault_root)},
            "embedding": {"model": "fake-test-model", "dimensions": DIM, "device": "cpu"},
            "store": {"backend": "sqlite-vec", "path": str(db_path)},
            "search": dict(search_overrides),
        }
    )


def add_doc(
    store: Store,
    embedder: FakeEmbedder,
    kind: str,
    doc_id: str,
    texts: list[str],
    heading: str = "",
    file_path: str | None = None,
) -> None:
    chunks = [Chunk(text=t, heading=heading, pos=i) for i, t in enumerate(texts)]
    embeddings = embedder.embed_documents([c.text for c in chunks])
    store.upsert_document(
        kind=kind,
        doc_id=doc_id,
        file_path=file_path if file_path is not None else f"{doc_id}.md",
        content_hash="h",
        chunks=chunks,
        embeddings=embeddings,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "index.sqlite"


@pytest.fixture
def store(db_path: Path):
    s = Store(db_path, dimensions=DIM)
    yield s
    s.close()


@pytest.fixture
def config(vault_root: Path, db_path: Path) -> Config:
    return make_config(vault_root, db_path)


# --- shared search behavior (both tools) -------------------------------------


@pytest.mark.parametrize("search_fn", [search_wiki, search_sources], ids=["wiki", "sources"])
def test_empty_query_returns_note_without_embedder_call(
    search_fn, config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    result = search_fn(config, store, fake_embedder, "")
    assert result == {"results": [], "note": EMPTY_QUERY_NOTE}
    assert fake_embedder.calls == []


@pytest.mark.parametrize("search_fn", [search_wiki, search_sources], ids=["wiki", "sources"])
def test_whitespace_query_returns_note_without_embedder_call(
    search_fn, config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    result = search_fn(config, store, fake_embedder, "  \n\t  ")
    assert result == {"results": [], "note": EMPTY_QUERY_NOTE}
    assert fake_embedder.calls == []


@pytest.mark.parametrize("search_fn", [search_wiki, search_sources], ids=["wiki", "sources"])
def test_empty_index_returns_reindex_note(
    search_fn, config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    result = search_fn(config, store, fake_embedder, "memory augmentation")
    assert result == {"results": [], "note": EMPTY_INDEX_NOTE}


@pytest.mark.parametrize("search_fn", [search_wiki, search_sources], ids=["wiki", "sources"])
def test_empty_index_does_not_call_embedder(
    search_fn, config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    # Searching nothing must not pay the (real-world: 500MB model load) embed cost.
    search_fn(config, store, fake_embedder, "memory augmentation")
    assert fake_embedder.calls == []


# --- search_wiki --------------------------------------------------------------


def test_search_wiki_result_shape(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(
        store,
        fake_embedder,
        "wiki",
        "wiki/tools/engramme",
        ["memory augmentation startup"],
        heading="Tools > Engramme",
    )
    result = search_wiki(config, store, fake_embedder, "memory augmentation startup")
    assert set(result) == {"results"}  # no note on success
    assert len(result["results"]) == 1
    entry = result["results"][0]
    assert set(entry) == {"page_id", "heading", "excerpt", "score"}
    assert entry["page_id"] == "wiki/tools/engramme"
    assert entry["heading"] == "Tools > Engramme"
    assert entry["excerpt"] == "memory augmentation startup"  # short text passes through intact
    assert isinstance(entry["score"], float)


def test_search_wiki_orders_results_by_score_desc(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/exact", ["memory augmentation startup"])
    add_doc(store, fake_embedder, "wiki", "wiki/partial", ["memory augmentation product"])
    add_doc(store, fake_embedder, "wiki", "wiki/unrelated", ["cooking dinner recipes"])
    result = search_wiki(config, store, fake_embedder, "memory augmentation startup")
    scores = [r["score"] for r in result["results"]]
    assert len(scores) == 3
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert result["results"][0]["page_id"] == "wiki/exact"


def test_search_wiki_default_top_k_from_config(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    for i in range(7):
        add_doc(store, fake_embedder, "wiki", f"wiki/doc{i}", [f"memory note number {i}"])
    result = search_wiki(config, store, fake_embedder, "memory note")
    assert len(result["results"]) == 5  # SearchSection.default_top_k_wiki


def test_search_wiki_explicit_top_k_overrides_default(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    for i in range(7):
        add_doc(store, fake_embedder, "wiki", f"wiki/doc{i}", [f"memory note number {i}"])
    result = search_wiki(config, store, fake_embedder, "memory note", top_k=2)
    assert len(result["results"]) == 2


def test_search_wiki_excerpt_capped_at_60_words_with_ellipsis(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/long", [words(80)])
    excerpt = search_wiki(config, store, fake_embedder, "w0 w1 w2")["results"][0]["excerpt"]
    assert excerpt == words(60) + " …"


def test_search_wiki_excerpt_exactly_60_words_not_truncated(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/sixty", [words(60)])
    excerpt = search_wiki(config, store, fake_embedder, "w0 w1 w2")["results"][0]["excerpt"]
    assert excerpt == words(60)
    assert "…" not in excerpt


def test_search_wiki_cap_independent_of_source_excerpt_config(
    vault_root: Path, db_path: Path, store: Store, fake_embedder: FakeEmbedder
) -> None:
    # max_excerpt_words governs SOURCES only; the wiki cap stays at 60.
    cfg = make_config(vault_root, db_path, max_excerpt_words=5)
    add_doc(store, fake_embedder, "wiki", "wiki/long", [words(80)])
    excerpt = search_wiki(cfg, store, fake_embedder, "w0 w1 w2")["results"][0]["excerpt"]
    assert excerpt == words(60) + " …"


def test_search_wiki_never_returns_source_docs(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/engramme", ["memory augmentation startup"])
    add_doc(store, fake_embedder, "source", "src-engramme", ["memory augmentation startup"])
    result = search_wiki(config, store, fake_embedder, "memory augmentation startup")
    assert {r["page_id"] for r in result["results"]} == {"wiki/engramme"}


# --- search_sources -----------------------------------------------------------


def test_search_sources_result_shape(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(
        store,
        fake_embedder,
        "source",
        "src-2026-06-01-engramme-site",
        ["memory augmentation startup"],
        heading="Engramme",
    )
    result = search_sources(config, store, fake_embedder, "memory augmentation startup")
    assert set(result) == {"results"}
    entry = result["results"][0]
    assert set(entry) == {"source_id", "heading", "excerpt", "score"}
    assert entry["source_id"] == "src-2026-06-01-engramme-site"
    assert entry["heading"] == "Engramme"
    assert isinstance(entry["score"], float)


def test_search_sources_excerpt_capped_at_15_words_with_ellipsis(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "source", "src-long", [words(40)])
    excerpt = search_sources(config, store, fake_embedder, "w0 w1 w2")["results"][0]["excerpt"]
    assert excerpt == words(15) + " …"


def test_search_sources_verbatim_quote_beyond_cap_impossible(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    # The vault contract: no source quote longer than max_excerpt_words can
    # escape through this layer.
    add_doc(store, fake_embedder, "source", "src-long", [words(40)])
    excerpt = search_sources(config, store, fake_embedder, "w0 w1 w2")["results"][0]["excerpt"]
    sixteen_word_prefix = words(16)
    assert sixteen_word_prefix not in excerpt
    assert len(excerpt.removesuffix(" …").split()) == 15


def test_search_sources_excerpt_exactly_15_words_not_truncated(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "source", "src-fifteen", [words(15)])
    excerpt = search_sources(config, store, fake_embedder, "w0 w1 w2")["results"][0]["excerpt"]
    assert excerpt == words(15)
    assert "…" not in excerpt


def test_search_sources_cap_follows_config_max_excerpt_words(
    vault_root: Path, db_path: Path, store: Store, fake_embedder: FakeEmbedder
) -> None:
    cfg = make_config(vault_root, db_path, max_excerpt_words=5)
    add_doc(store, fake_embedder, "source", "src-long", [words(20)])
    excerpt = search_sources(cfg, store, fake_embedder, "w0 w1 w2")["results"][0]["excerpt"]
    assert excerpt == "w0 w1 w2 w3 w4 …"


def test_search_sources_default_top_k_from_config(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    for i in range(5):
        add_doc(store, fake_embedder, "source", f"src-doc{i}", [f"memory note number {i}"])
    result = search_sources(config, store, fake_embedder, "memory note")
    assert len(result["results"]) == 3  # SearchSection.default_top_k_sources


def test_search_sources_explicit_top_k_overrides_default(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    for i in range(5):
        add_doc(store, fake_embedder, "source", f"src-doc{i}", [f"memory note number {i}"])
    result = search_sources(config, store, fake_embedder, "memory note", top_k=1)
    assert len(result["results"]) == 1


def test_search_sources_never_returns_wiki_docs(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "wiki", "wiki/engramme", ["memory augmentation startup"])
    add_doc(store, fake_embedder, "source", "src-engramme", ["memory augmentation startup"])
    result = search_sources(config, store, fake_embedder, "memory augmentation startup")
    assert {r["source_id"] for r in result["results"]} == {"src-engramme"}


def test_search_sources_empty_index_note_when_only_wiki_populated(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    # Per-kind emptiness: a populated wiki must not mask an empty source index.
    add_doc(store, fake_embedder, "wiki", "wiki/engramme", ["memory augmentation startup"])
    result = search_sources(config, store, fake_embedder, "memory augmentation startup")
    assert result == {"results": [], "note": EMPTY_INDEX_NOTE}


# --- get_page: resolution -----------------------------------------------------


def test_get_page_via_store_doc_id(
    config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
) -> None:
    # The doc_id is NOT a valid vault path; only the store lookup can resolve it.
    add_doc(
        store,
        fake_embedder,
        "source",
        "src-2026-06-01-engramme-site",
        ["alpha beta"],
        file_path="sources/2026-06-01-engramme-site.md",
    )
    result = get_page(config, store, "src-2026-06-01-engramme-site")
    assert "error" not in result
    assert result["page_id"] == "src-2026-06-01-engramme-site"
    assert result["file_path"] == "sources/2026-06-01-engramme-site.md"
    expected = (vault_root / "sources" / "2026-06-01-engramme-site.md").read_text()
    assert result["content"] == expected


def test_get_page_via_store_doc_with_absolute_file_path(
    config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
) -> None:
    # Indexer may store absolute paths; file_path in the result stays vault-relative.
    add_doc(
        store,
        fake_embedder,
        "source",
        "src-abs",
        ["alpha beta"],
        file_path=str(vault_root / "sources" / "2026-06-01-engramme-site.md"),
    )
    result = get_page(config, store, "src-abs")
    assert "error" not in result
    assert result["file_path"] == "sources/2026-06-01-engramme-site.md"


def test_get_page_store_lookup_wins_over_path_fallback(
    config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
) -> None:
    # Store maps the id to a DIFFERENT file than the literal path interpretation.
    add_doc(
        store,
        fake_embedder,
        "wiki",
        "wiki/tools/engramme",
        ["alpha beta"],
        file_path="wiki/concepts/spaced-repetition.md",
    )
    result = get_page(config, store, "wiki/tools/engramme")
    assert result["file_path"] == "wiki/concepts/spaced-repetition.md"


def test_get_page_via_relative_path_without_md(config: Config, store: Store) -> None:
    result = get_page(config, store, "wiki/tools/engramme")
    assert "error" not in result
    assert result["page_id"] == "wiki/tools/engramme"
    assert result["file_path"] == "wiki/tools/engramme.md"
    assert "# Engramme" in result["content"]


def test_get_page_via_relative_path_with_md(config: Config, store: Store) -> None:
    result = get_page(config, store, "wiki/tools/engramme.md")
    assert "error" not in result
    assert result["file_path"] == "wiki/tools/engramme.md"
    assert "# Engramme" in result["content"]


def test_get_page_content_matches_file_exactly(
    config: Config, store: Store, vault_root: Path
) -> None:
    result = get_page(config, store, "wiki/concepts/spaced-repetition")
    expected = (vault_root / "wiki" / "concepts" / "spaced-repetition.md").read_text()
    assert result["content"] == expected
    assert "truncated" not in result


# --- get_page: path traversal guard (security, mandatory) ----------------------


def test_get_page_dotdot_traversal_raises_value_error(
    config: Config, store: Store, tmp_path: Path
) -> None:
    # The file EXISTS outside the vault — the guard must fire before any read.
    (tmp_path / "outside.md").write_text("secret outside the vault\n")
    with pytest.raises(ValueError, match="escapes vault root"):
        get_page(config, store, "../outside.md")


def test_get_page_dotdot_traversal_without_md_raises_value_error(
    config: Config, store: Store, tmp_path: Path
) -> None:
    (tmp_path / "outside.md").write_text("secret outside the vault\n")
    with pytest.raises(ValueError, match="escapes vault root"):
        get_page(config, store, "../outside")


def test_get_page_absolute_path_raises_value_error(
    config: Config, store: Store, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("secret outside the vault\n")
    with pytest.raises(ValueError, match="escapes vault root"):
        get_page(config, store, str(outside))


def test_get_page_store_file_path_outside_vault_raises_value_error(
    config: Config, store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    # Defense in depth: a poisoned index row must not read outside the vault.
    (tmp_path / "outside.md").write_text("secret outside the vault\n")
    add_doc(
        store,
        fake_embedder,
        "source",
        "src-evil",
        ["alpha beta"],
        file_path=str(tmp_path / "outside.md"),
    )
    with pytest.raises(ValueError, match="escapes vault root"):
        get_page(config, store, "src-evil")


# --- get_page: misses ----------------------------------------------------------


def test_get_page_missing_returns_not_found_dict(config: Config, store: Store) -> None:
    result = get_page(config, store, "wiki/tools/does-not-exist")
    assert result["error"] == "not found"
    assert result["page_id"] == "wiki/tools/does-not-exist"
    assert "hint" in result


def test_get_page_store_doc_with_deleted_file_returns_not_found(
    config: Config, store: Store, fake_embedder: FakeEmbedder
) -> None:
    add_doc(store, fake_embedder, "source", "src-ghost", ["alpha"], file_path="sources/ghost.md")
    result = get_page(config, store, "src-ghost")
    assert result["error"] == "not found"
    assert result["page_id"] == "src-ghost"


def test_get_page_empty_page_id_returns_not_found(config: Config, store: Store) -> None:
    result = get_page(config, store, "")
    assert result["error"] == "not found"
    assert result["page_id"] == ""


def test_get_page_directory_returns_not_found(config: Config, store: Store) -> None:
    result = get_page(config, store, "wiki/tools")
    assert result["error"] == "not found"
    assert result["page_id"] == "wiki/tools"


# --- get_page: content cap ------------------------------------------------------


def test_get_page_content_capped_at_100k_chars_with_truncated_flag(
    config: Config, store: Store, vault_root: Path
) -> None:
    (vault_root / "wiki" / "big.md").write_text("x" * 150_000)
    result = get_page(config, store, "wiki/big")
    assert len(result["content"]) == 100_000
    assert result["truncated"] is True


def test_get_page_exactly_100k_chars_not_truncated(
    config: Config, store: Store, vault_root: Path
) -> None:
    (vault_root / "wiki" / "exact.md").write_text("x" * 100_000)
    result = get_page(config, store, "wiki/exact")
    assert len(result["content"]) == 100_000
    assert "truncated" not in result


# --- hygiene --------------------------------------------------------------------


def test_tools_write_nothing_to_stdout(
    config: Config,
    store: Store,
    fake_embedder: FakeEmbedder,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # stdio MCP transport: stdout belongs to the protocol.
    add_doc(store, fake_embedder, "wiki", "wiki/a", ["alpha beta"])
    search_wiki(config, store, fake_embedder, "alpha")
    search_sources(config, store, fake_embedder, "alpha")  # empty-index note path
    search_wiki(config, store, fake_embedder, "")
    get_page(config, store, "wiki/tools/engramme")
    get_page(config, store, "wiki/never-there")
    assert capsys.readouterr().out == ""
