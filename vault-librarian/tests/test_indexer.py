"""Tests for vault_librarian.indexer — written first, per CONTRACTS.md `indexer.py`.

Uses the real config/chunker/store modules plus FakeEmbedder from conftest:
the indexer's job is orchestration, so these tests exercise the real seams.
The fixture vault (conftest.make_vault_tree) has 2 wiki pages + 1 source,
plus _index.md files and journal/inbox dirs that discovery must skip.
"""

import hashlib
import os
import textwrap
from pathlib import Path

import pytest

from vault_librarian.config import Config, load_config
from vault_librarian.indexer import DocRef, IndexStats, discover, reindex
from vault_librarian.store import Store

from .conftest import FakeEmbedder

ENGRAMME_ID = "wiki/tools/engramme"
SPACED_ID = "wiki/concepts/spaced-repetition"
SOURCE_ID = "src-2026-06-01-engramme-site"
ALL_IDS = {ENGRAMME_ID, SPACED_ID, SOURCE_ID}

STATS_KEYS = {"scanned", "indexed", "skipped", "deleted", "chunks", "seconds", "errors"}


@pytest.fixture
def config(config_path: Path) -> Config:
    return load_config(config_path)


@pytest.fixture
def store(config: Config):
    with Store(config.store.path, dimensions=config.embedding.dimensions) as s:
        yield s


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestDiscover:
    def test_finds_all_documents(self, config: Config) -> None:
        refs = discover(config)
        assert {r.doc_id for r in refs} == ALL_IDS

    def test_kinds_assigned_correctly(self, config: Config) -> None:
        kinds = {r.doc_id: r.kind for r in discover(config)}
        assert kinds[ENGRAMME_ID] == "wiki"
        assert kinds[SPACED_ID] == "wiki"
        assert kinds[SOURCE_ID] == "source"

    def test_excludes_index_files(self, config: Config) -> None:
        refs = discover(config)
        assert all(r.path.name != "_index.md" for r in refs)

    def test_wiki_doc_id_is_posix_relpath_without_md_suffix(
        self, config: Config, vault_root: Path
    ) -> None:
        ref = next(r for r in discover(config) if r.doc_id == ENGRAMME_ID)
        assert ref.path == vault_root / "wiki" / "tools" / "engramme.md"
        assert "\\" not in ref.doc_id
        assert not ref.doc_id.endswith(".md")

    def test_source_doc_id_from_front_matter(self, config: Config) -> None:
        assert SOURCE_ID in {r.doc_id for r in discover(config)}

    def test_source_doc_id_falls_back_to_stem(self, config: Config, vault_root: Path) -> None:
        (vault_root / "sources" / "2026-06-10-no-id.md").write_text(
            "---\ntitle: No id here\n---\n\nBody without an id field.\n"
        )
        assert "src-2026-06-10-no-id" in {r.doc_id for r in discover(config)}

    def test_skips_journal_and_inbox(self, config: Config, vault_root: Path) -> None:
        (vault_root / "journal" / "2026-06-12.md").write_text("# Journal entry\n\nSecret.\n")
        (vault_root / "inbox" / "capture.md").write_text("# Inbox capture\n\nRaw.\n")
        paths = {r.path for r in discover(config)}
        assert vault_root / "journal" / "2026-06-12.md" not in paths
        assert vault_root / "inbox" / "capture.md" not in paths

    def test_wiki_discovery_is_recursive(self, config: Config, vault_root: Path) -> None:
        deep = vault_root / "wiki" / "a" / "b" / "deep.md"
        deep.parent.mkdir(parents=True)
        deep.write_text("# Deep\n\nNested page body.\n")
        assert "wiki/a/b/deep" in {r.doc_id for r in discover(config)}

    def test_nested_index_files_excluded(self, config: Config, vault_root: Path) -> None:
        (vault_root / "wiki" / "tools" / "_index.md").write_text("# Tools index\n")
        assert all(r.path.name != "_index.md" for r in discover(config))

    def test_sources_discovery_is_flat(self, config: Config, vault_root: Path) -> None:
        sub = vault_root / "sources" / "nested"
        sub.mkdir()
        (sub / "hidden.md").write_text("Nested source body.\n")
        assert "src-hidden" not in {r.doc_id for r in discover(config)}

    def test_returns_docref_dataclass(self, config: Config) -> None:
        ref = discover(config)[0]
        assert isinstance(ref, DocRef)
        assert isinstance(ref.path, Path)

    def test_missing_dirs_return_empty(self, tmp_path: Path) -> None:
        empty_root = tmp_path / "empty-vault"
        empty_root.mkdir()
        cfg = Config.model_validate(
            {
                "vault": {"name": "personal", "root": str(empty_root)},
                "embedding": {"model": "fake", "dimensions": 64},
                "store": {"path": str(tmp_path / "x.sqlite")},
            }
        )
        assert discover(cfg) == []


class TestFullReindex:
    def test_first_run_stats(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder
    ) -> None:
        stats = reindex(config, store, fake_embedder)
        assert stats.scanned == 3
        assert stats.indexed == 3
        assert stats.skipped == 0
        assert stats.deleted == 0
        assert stats.errors == 0
        assert stats.chunks >= 3
        assert stats.seconds >= 0.0

    def test_first_run_populates_store(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder
    ) -> None:
        reindex(config, store, fake_embedder)
        counts = store.counts()
        assert counts["wiki_docs"] == 2
        assert counts["source_docs"] == 1
        assert counts["wiki_chunks"] >= 2
        assert counts["source_chunks"] >= 1

    def test_content_hash_recorded(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        reindex(config, store, fake_embedder)
        state = store.indexed_state()
        assert state[ENGRAMME_ID] == sha256_of(vault_root / "wiki" / "tools" / "engramme.md")

    def test_indexed_content_is_searchable(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder
    ) -> None:
        reindex(config, store, fake_embedder)
        hits = store.search("wiki", fake_embedder.embed_query("memory augmentation startup"), 2)
        assert hits and hits[0].doc_id == ENGRAMME_ID

    def test_second_run_skips_everything(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder
    ) -> None:
        reindex(config, store, fake_embedder)
        stats = reindex(config, store, fake_embedder)
        assert stats.scanned == 3
        assert stats.indexed == 0
        assert stats.skipped == 3
        assert stats.deleted == 0
        assert stats.chunks == 0

    def test_stats_to_dict_keys(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder
    ) -> None:
        d = reindex(config, store, fake_embedder).to_dict()
        assert set(d.keys()) == STATS_KEYS

    def test_index_stats_is_dataclass_with_contract_fields(self) -> None:
        stats = IndexStats(
            scanned=1, indexed=1, skipped=0, deleted=0, chunks=2, seconds=0.1, errors=0
        )
        assert stats.to_dict()["chunks"] == 2


class TestIncrementalReindex:
    def test_edited_file_reindexes_only_that_doc(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        reindex(config, store, fake_embedder)
        page = vault_root / "wiki" / "tools" / "engramme.md"
        page.write_text("---\nid: wiki-engramme\n---\n\n# Engramme\n\nCompletely new body.\n")
        stats = reindex(config, store, fake_embedder)
        assert stats.indexed == 1
        assert stats.skipped == 2
        assert stats.deleted == 0

    def test_edited_file_updates_content_hash(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        reindex(config, store, fake_embedder)
        page = vault_root / "wiki" / "tools" / "engramme.md"
        page.write_text("---\nid: wiki-engramme\n---\n\n# Engramme\n\nCompletely new body.\n")
        reindex(config, store, fake_embedder)
        assert store.indexed_state()[ENGRAMME_ID] == sha256_of(page)

    def test_edited_file_replaces_chunk_text(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        reindex(config, store, fake_embedder)
        page = vault_root / "wiki" / "tools" / "engramme.md"
        page.write_text("---\nid: wiki-engramme\n---\n\n# Engramme\n\nZanzibar quokka facts.\n")
        reindex(config, store, fake_embedder)
        hits = store.search("wiki", fake_embedder.embed_query("zanzibar quokka"), 1)
        assert hits and hits[0].doc_id == ENGRAMME_ID
        assert "Zanzibar" in hits[0].text

    def test_deleted_file_removes_document(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        reindex(config, store, fake_embedder)
        (vault_root / "wiki" / "concepts" / "spaced-repetition.md").unlink()
        stats = reindex(config, store, fake_embedder)
        assert stats.deleted == 1
        assert store.get_document(SPACED_ID) is None

    def test_deleted_file_removes_chunks(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        reindex(config, store, fake_embedder)
        (vault_root / "sources" / "2026-06-01-engramme-site.md").unlink()
        reindex(config, store, fake_embedder)
        counts = store.counts()
        assert counts["source_docs"] == 0
        assert counts["source_chunks"] == 0


class TestOnly:
    def test_only_indexes_just_that_file(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        target = vault_root / "wiki" / "tools" / "engramme.md"
        stats = reindex(config, store, fake_embedder, only=[target])
        assert stats.indexed == 1
        assert store.get_document(ENGRAMME_ID) is not None
        assert store.get_document(SPACED_ID) is None
        assert store.get_document(SOURCE_ID) is None

    def test_only_skips_deletion_pass(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        reindex(config, store, fake_embedder)
        (vault_root / "wiki" / "concepts" / "spaced-repetition.md").unlink()
        target = vault_root / "wiki" / "tools" / "engramme.md"
        stats = reindex(config, store, fake_embedder, only=[target])
        assert stats.deleted == 0
        assert store.get_document(SPACED_ID) is not None

    def test_only_unchanged_target_is_skipped(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        reindex(config, store, fake_embedder)
        target = vault_root / "wiki" / "tools" / "engramme.md"
        stats = reindex(config, store, fake_embedder, only=[target])
        assert stats.indexed == 0
        assert stats.skipped == 1

    def test_only_resolves_paths_before_comparing(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        crooked = vault_root / "wiki" / ".." / "wiki" / "tools" / "engramme.md"
        stats = reindex(config, store, fake_embedder, only=[crooked])
        assert stats.indexed == 1
        assert store.get_document(ENGRAMME_ID) is not None


class TestForce:
    def test_force_reindexes_all(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder
    ) -> None:
        reindex(config, store, fake_embedder)
        stats = reindex(config, store, fake_embedder, force=True)
        assert stats.indexed == 3
        assert stats.skipped == 0

    def test_force_does_not_duplicate_chunks(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder
    ) -> None:
        reindex(config, store, fake_embedder)
        before = store.counts()
        reindex(config, store, fake_embedder, force=True)
        assert store.counts() == before


class TestEdgeCases:
    def test_empty_body_doc_skipped_not_indexed(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        (vault_root / "wiki" / "tools" / "stub.md").write_text(
            "---\nid: wiki-stub\ntitle: Stub\n---\n\n   \n"
        )
        stats = reindex(config, store, fake_embedder)
        assert stats.scanned == 4
        assert stats.indexed == 3
        assert stats.skipped == 1
        doc = store.get_document("wiki/tools/stub")
        assert doc is not None  # registered with zero chunks so the hash advances
        hits = store.search("wiki", fake_embedder.embed_query("stub"), 10)
        assert not any(h.doc_id == "wiki/tools/stub" for h in hits)

    def test_unreadable_file_counted_as_error(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        page = vault_root / "wiki" / "tools" / "engramme.md"
        os.chmod(page, 0o000)
        try:
            stats = reindex(config, store, fake_embedder)
            assert stats.errors == 1
            assert stats.indexed == 2
        finally:
            os.chmod(page, 0o644)

    def test_unreadable_file_does_not_abort_sweep(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        page = vault_root / "wiki" / "tools" / "engramme.md"
        os.chmod(page, 0o000)
        try:
            reindex(config, store, fake_embedder)
            assert store.get_document(SPACED_ID) is not None
            assert store.get_document(SOURCE_ID) is not None
        finally:
            os.chmod(page, 0o644)

    def test_error_is_logged_to_stderr(
        self,
        config: Config,
        store: Store,
        fake_embedder: FakeEmbedder,
        vault_root: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        page = vault_root / "wiki" / "tools" / "engramme.md"
        os.chmod(page, 0o000)
        try:
            reindex(config, store, fake_embedder)
        finally:
            os.chmod(page, 0o644)
        captured = capsys.readouterr()
        assert "engramme" in captured.err
        assert captured.out == ""

    def test_embed_failure_counted_as_error_without_abort(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder
    ) -> None:
        class ExplodingEmbedder(FakeEmbedder):
            def embed_documents(self, texts: list[str]):
                if any("Anki" in t for t in texts):
                    raise RuntimeError("simulated embed failure")
                return super().embed_documents(texts)

        stats = reindex(config, store, ExplodingEmbedder())
        assert stats.errors == 1
        assert stats.indexed == 2
        assert store.get_document(SPACED_ID) is None
        assert store.get_document(ENGRAMME_ID) is not None

    def test_unreadable_file_is_not_garbage_collected(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        reindex(config, store, fake_embedder)
        page = vault_root / "wiki" / "tools" / "engramme.md"
        os.chmod(page, 0o000)
        try:
            stats = reindex(config, store, fake_embedder)
            assert stats.deleted == 0
            assert store.get_document(ENGRAMME_ID) is not None
        finally:
            os.chmod(page, 0o644)

    def test_failed_doc_retried_next_run(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        page = vault_root / "wiki" / "tools" / "engramme.md"
        os.chmod(page, 0o000)
        try:
            reindex(config, store, fake_embedder)
        finally:
            os.chmod(page, 0o644)
        stats = reindex(config, store, fake_embedder)
        assert stats.indexed == 1
        assert store.get_document(ENGRAMME_ID) is not None

    def test_malformed_front_matter_still_indexed(
        self, config: Config, store: Store, fake_embedder: FakeEmbedder, vault_root: Path
    ) -> None:
        (vault_root / "wiki" / "tools" / "broken.md").write_text(
            textwrap.dedent(
                """\
                ---
                title: [unclosed
                ---

                # Broken

                Body that must still get indexed despite bad front matter.
                """
            )
        )
        stats = reindex(config, store, fake_embedder)
        assert stats.errors == 0
        assert store.get_document("wiki/tools/broken") is not None
