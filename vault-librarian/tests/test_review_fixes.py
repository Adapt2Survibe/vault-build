"""Reproduction tests for the 2026-06-12 review-wall findings.

Each class pins one accepted finding from the /rev code review (run
20260612-vault-phase1). Written FIRST against the unfixed code — every test
here reproduced its bug before the fix landed (TDD gate for the review wall).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from vault_librarian import tools
from vault_librarian.chunker import parse_front_matter
from vault_librarian.config import load_config
from vault_librarian.embedder import Embedder
from vault_librarian.indexer import reindex
from vault_librarian.server import _LazyOnce
from vault_librarian.store import Store

from .conftest import FakeEmbedder, write_config_yaml

CAPTURE = Path(__file__).resolve().parents[2] / "bin" / "vault-capture"


def _cfg(config_path):
    return load_config(config_path)


def _store_for(cfg):
    return Store(cfg.store.path, dimensions=cfg.embedding.dimensions)


class TestEmptiedDocClearsIndex:
    """C1 (P1): emptying a previously-indexed doc must remove its chunks."""

    def test_emptied_wiki_page_chunks_removed_and_hash_updated(
        self, vault_root, config_path, fake_embedder
    ):
        cfg = _cfg(config_path)
        with _store_for(cfg) as store:
            reindex(cfg, store, fake_embedder)
            hits = store.search("wiki", fake_embedder.embed_query("spaced repetition anki"), 5)
            assert any(h.doc_id == "wiki/concepts/spaced-repetition" for h in hits)

            page = vault_root / "wiki" / "concepts" / "spaced-repetition.md"
            page.write_text("---\nid: wiki-spaced-repetition\n---\n")
            stats = reindex(cfg, store, fake_embedder)

            hits = store.search("wiki", fake_embedder.embed_query("spaced repetition anki"), 5)
            assert not any(h.doc_id == "wiki/concepts/spaced-repetition" for h in hits)
            import hashlib

            assert (
                store.indexed_state()["wiki/concepts/spaced-repetition"]
                == hashlib.sha256(page.read_bytes()).hexdigest()
            )
            assert stats.skipped >= 1  # still reported as skipped per contract


class TestSourceIdSafety:
    """A2 (P1): hostile/colliding front-matter ids must not evict other docs."""

    def test_source_id_not_matching_src_prefix_falls_back_to_stem(
        self, vault_root, config_path, fake_embedder, capsys
    ):
        evil = vault_root / "sources" / "2026-06-12-evil.md"
        evil.write_text(
            "---\nid: wiki/tools/engramme\n---\n\nhostile content words here\n"
        )
        cfg = _cfg(config_path)
        with _store_for(cfg) as store:
            reindex(cfg, store, fake_embedder)
            state = store.indexed_state()
            # wiki page survives; hostile source lands under the stem fallback
            assert "wiki/tools/engramme" in state
            assert store.get_document("wiki/tools/engramme")["kind"] == "wiki"
            assert "src-2026-06-12-evil" in state
            assert "non-conforming front-matter id" in capsys.readouterr().err

    def test_duplicate_source_ids_keep_both_docs_searchable(
        self, vault_root, config_path, fake_embedder, capsys
    ):
        a = vault_root / "sources" / "2026-06-10-alpha.md"
        b = vault_root / "sources" / "2026-06-11-beta.md"
        a.write_text("---\nid: src-shared\n---\n\nalpha aardvark text\n")
        b.write_text("---\nid: src-shared\n---\n\nbeta bumblebee text\n")
        cfg = _cfg(config_path)
        with _store_for(cfg) as store:
            reindex(cfg, store, fake_embedder)
            state = store.indexed_state()
            assert "src-shared" in state
            assert "src-2026-06-11-beta" in state  # later file demoted to stem id
            err = capsys.readouterr().err
            assert "duplicate" in err and "src-shared" in err

    def test_unreadable_source_with_custom_id_is_not_garbage_collected(
        self, vault_root, config_path, fake_embedder
    ):
        src = vault_root / "sources" / "2026-06-09-custom.md"
        src.write_text("---\nid: src-my-custom-name\n---\n\ncustom content words\n")
        cfg = _cfg(config_path)
        with _store_for(cfg) as store:
            reindex(cfg, store, fake_embedder)
            assert "src-my-custom-name" in store.indexed_state()
            src.chmod(0o000)
            try:
                stats = reindex(cfg, store, fake_embedder)
            finally:
                src.chmod(0o644)
            # the still-existing file's document must survive the deletion pass
            assert "src-my-custom-name" in store.indexed_state()
            assert stats.errors >= 1
            assert stats.deleted == 0


class TestExcerptCharCeiling:
    """A1 (P1): word-cap alone leaks whole documents of whitespace-poor text."""

    def test_no_space_chunk_excerpt_is_char_bounded(self, config_path, fake_embedder):
        cfg = _cfg(config_path)
        blob = "x" * 5000
        with _store_for(cfg) as store:
            from collections import namedtuple

            chunk = namedtuple("Chunk", "text heading pos")(blob, "", 0)
            store.upsert_document(
                "source",
                "src-blob",
                "sources/blob.md",
                "h",
                [chunk],
                fake_embedder.embed_documents([blob]),
            )
            out = tools.search_sources(cfg, store, fake_embedder, "xxxx xxxx")
            excerpt = out["results"][0]["excerpt"]
            assert len(excerpt) <= cfg.search.max_excerpt_words * 40 + 2
            assert excerpt.endswith("…")


class TestNanGuards:
    """A5 (P2, corroborated): non-finite embeddings must fail loudly, not persist."""

    def test_upsert_rejects_nan_embeddings(self, tmp_path):
        from collections import namedtuple

        chunk = namedtuple("Chunk", "text heading pos")("hello", "", 0)
        vec = np.full((1, 64), np.nan, dtype=np.float32)
        with Store(tmp_path / "i.sqlite", dimensions=64) as store:
            with pytest.raises(ValueError, match="finite"):
                store.upsert_document("wiki", "w/a", "w/a.md", "h", [chunk], vec)

    def test_search_rejects_nan_query(self, tmp_path, fake_embedder):
        from collections import namedtuple

        chunk = namedtuple("Chunk", "text heading pos")("hello", "", 0)
        with Store(tmp_path / "i.sqlite", dimensions=64) as store:
            store.upsert_document(
                "wiki", "w/a", "w/a.md", "h", [chunk], fake_embedder.embed_documents(["hello"])
            )
            with pytest.raises(ValueError, match="finite"):
                store.search("wiki", np.full(64, np.nan, dtype=np.float32), 3)


class TestStoreHasChunks:
    """M5/P2: cheap per-kind emptiness check for the search hot path."""

    def test_has_chunks(self, tmp_path, fake_embedder):
        from collections import namedtuple

        chunk = namedtuple("Chunk", "text heading pos")("hello", "", 0)
        with Store(tmp_path / "i.sqlite", dimensions=64) as store:
            assert store.has_chunks("wiki") is False
            store.upsert_document(
                "wiki", "w/a", "w/a.md", "h", [chunk], fake_embedder.embed_documents(["hello"])
            )
            assert store.has_chunks("wiki") is True
            assert store.has_chunks("source") is False
            with pytest.raises(ValueError):
                store.has_chunks("nope")


class TestOperationalErrorGrace:
    """R6 (P2): SQLITE_BUSY during a search surfaces as a note, not a ToolError."""

    def test_locked_db_returns_retry_note(self, config_path, fake_embedder, monkeypatch):
        cfg = _cfg(config_path)
        with _store_for(cfg) as store:
            from collections import namedtuple

            chunk = namedtuple("Chunk", "text heading pos")("hello world", "", 0)
            store.upsert_document(
                "wiki", "w/a", "w/a.md", "h", [chunk], fake_embedder.embed_documents(["hello"])
            )

            def _boom(*a, **k):
                raise sqlite3.OperationalError("database is locked")

            monkeypatch.setattr(store, "search", _boom)
            out = tools.search_wiki(cfg, store, fake_embedder, "hello")
            assert out["results"] == []
            assert "unavailable" in out["note"]


class TestGetPageGrace:
    """SF10 + AN2: unreadable pages return an error dict; misses carry a hint."""

    def test_unreadable_page_returns_error_dict(self, vault_root, config_path, fake_embedder):
        cfg = _cfg(config_path)
        page = vault_root / "wiki" / "tools" / "engramme.md"
        page.chmod(0o000)
        try:
            with _store_for(cfg) as store:
                out = tools.get_page(cfg, store, "wiki/tools/engramme")
        finally:
            page.chmod(0o644)
        assert "error" in out and "unreadable" in out["error"]

    def test_not_found_includes_hint(self, config_path):
        cfg = _cfg(config_path)
        with _store_for(cfg) as store:
            out = tools.get_page(cfg, store, "wiki/nope/never")
            assert out["error"] == "not found"
            assert "hint" in out


class TestLazyOnce:
    """M2 (P2): a factory legitimately returning None must still be called once."""

    def test_none_value_cached(self):
        calls = []

        def factory():
            calls.append(1)
            return None

        holder = _LazyOnce(factory)
        assert holder.get() is None
        assert holder.get() is None
        assert len(calls) == 1


class TestEmbedderHardening:
    """S1 + R2 + SF5: revision pin, load-failure caching, stdout discipline."""

    class _RecordingLoader:
        def __init__(self):
            self.calls = []

        def __call__(self, name, **kwargs):
            self.calls.append((name, kwargs))

            class _M:
                def encode(self, texts, **kw):
                    return np.zeros((len(texts), 8), dtype=np.float32)

            return _M()

    def test_revision_passed_to_loader(self):
        loader = self._RecordingLoader()
        emb = Embedder("some-model", revision="abc123", _loader=loader)
        emb.embed_query("hi")
        assert loader.calls[0][1].get("revision") == "abc123"

    def test_no_revision_kwarg_when_unpinned(self):
        loader = self._RecordingLoader()
        emb = Embedder("some-model", _loader=loader)
        emb.embed_query("hi")
        assert "revision" not in loader.calls[0][1]

    def test_load_failure_cached_not_retried(self):
        calls = []

        def loader(name, **kwargs):
            calls.append(1)
            raise RuntimeError("no network")

        emb = Embedder("some-model", _loader=loader)
        with pytest.raises(RuntimeError):
            emb.embed_query("hi")
        with pytest.raises(RuntimeError, match="previous attempt"):
            emb.embed_query("hi")
        assert len(calls) == 1

    def test_loader_stdout_redirected_to_stderr(self, capsys):
        def loader(name, **kwargs):
            print("loading banner pollution")

            class _M:
                def encode(self, texts, **kw):
                    print("encode pollution")
                    return np.zeros((len(texts), 8), dtype=np.float32)

            return _M()

        emb = Embedder("some-model", _loader=loader)
        emb.embed_query("hi")
        captured = capsys.readouterr()
        assert captured.out == ""  # stdout is the MCP protocol channel
        assert "pollution" in captured.err


class TestConfigHardening:
    """SF13 + S1: empty config fails with the path; revision field exists."""

    def test_empty_config_file_raises_with_path(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("")
        with pytest.raises(ValueError, match="empty.yaml"):
            load_config(p)

    def test_revision_field_default_none_and_settable(self, tmp_path, vault_root):
        p = tmp_path / "c.yaml"
        write_config_yaml(p, vault_root, tmp_path / "i.sqlite")
        assert load_config(p).embedding.revision is None
        p.write_text(p.read_text().replace("  device: cpu", "  device: cpu\n  revision: abc123"))
        assert load_config(p).embedding.revision == "abc123"


class TestCrlfFrontMatter:
    """SF8: CRLF files must still have front matter parsed, not indexed as body."""

    def test_crlf_front_matter_parsed(self):
        text = "---\r\nid: src-x\r\ntitle: T\r\n---\r\n\r\nbody words\r\n"
        meta, body = parse_front_matter(text)
        assert meta.get("id") == "src-x"
        assert "id: src-x" not in body


class TestOrphanVecWarning:
    """SF9: orphan vec rows are index corruption — must be observable."""

    def test_orphan_row_warns_on_stderr(self, tmp_path, fake_embedder, capsys):
        from collections import namedtuple

        chunk = namedtuple("Chunk", "text heading pos")("hello world", "", 0)
        db = tmp_path / "i.sqlite"
        with Store(db, dimensions=64) as store:
            store.upsert_document(
                "wiki", "w/a", "w/a.md", "h", [chunk], fake_embedder.embed_documents(["hello"])
            )
            raw = sqlite3.connect(str(db))
            raw.execute("DELETE FROM chunks")
            raw.commit()
            raw.close()
            hits = store.search("wiki", fake_embedder.embed_query("hello"), 3)
            assert hits == []
            assert "orphan" in capsys.readouterr().err


class TestIndexerErrorMessages:
    """SF12: stderr error lines carry the exception type for forensics."""

    def test_unreadable_file_error_names_exception_type(
        self, vault_root, config_path, fake_embedder, capsys
    ):
        cfg = _cfg(config_path)
        page = vault_root / "wiki" / "tools" / "engramme.md"
        page.chmod(0o000)
        try:
            with _store_for(cfg) as store:
                reindex(cfg, store, fake_embedder)
        finally:
            page.chmod(0o644)
        assert "PermissionError" in capsys.readouterr().err


class TestDeletionPassGuard:
    """R5 (P2): an empty discovery must never wipe a non-empty index."""

    def test_vanished_vault_root_skips_deletion(self, vault_root, config_path, fake_embedder):
        import shutil

        cfg = _cfg(config_path)
        with _store_for(cfg) as store:
            reindex(cfg, store, fake_embedder)
            assert store.indexed_state()
            shutil.rmtree(vault_root / "wiki")
            shutil.rmtree(vault_root / "sources")
            stats = reindex(cfg, store, fake_embedder)
            assert stats.deleted == 0
            assert store.indexed_state()  # index intact, deletion skipped + warned


class TestCliHardening:
    """C2/R3 + A3: config errors exit 2 cleanly; total-failure reindex exits 1."""

    def test_malformed_yaml_exits_2(self, tmp_path, capsys):
        from vault_librarian.cli import main

        p = tmp_path / "bad.yaml"
        p.write_text("vault: [unclosed")
        rc = main(["status", "--config", str(p)])
        captured = capsys.readouterr()
        assert rc == 2
        assert "invalid config" in captured.err
        assert captured.out == ""

    def test_schema_invalid_config_exits_2(self, tmp_path, capsys):
        from vault_librarian.cli import main

        p = tmp_path / "bad.yaml"
        p.write_text("vault:\n  name: personal\n")  # missing root/embedding/store
        rc = main(["status", "--config", str(p)])
        assert rc == 2
        assert "invalid config" in capsys.readouterr().err

    def test_total_failure_reindex_exits_1(self, vault_root, tmp_path, capsys):
        from vault_librarian.cli import main

        p = tmp_path / "c.yaml"
        write_config_yaml(p, vault_root, tmp_path / "i.sqlite")

        class _Broken:
            def embed_documents(self, texts):
                raise RuntimeError("model load failed")

        rc = main(["reindex", "--config", str(p)], embedder_factory=_Broken)
        captured = capsys.readouterr()
        stats = json.loads(captured.out.strip().splitlines()[-1])
        assert stats["errors"] > 0 and stats["indexed"] == 0
        assert rc == 1

    def test_partial_failure_reindex_exits_0(self, vault_root, tmp_path, capsys):
        from vault_librarian.cli import main

        p = tmp_path / "c.yaml"
        write_config_yaml(p, vault_root, tmp_path / "i.sqlite")
        bad = vault_root / "wiki" / "tools" / "engramme.md"
        bad.chmod(0o000)
        try:
            rc = main(["reindex", "--config", str(p)], embedder_factory=FakeEmbedder)
        finally:
            bad.chmod(0o644)
        captured = capsys.readouterr()
        stats = json.loads(captured.out.strip().splitlines()[-1])
        assert stats["errors"] >= 1 and stats["indexed"] >= 1
        assert rc == 0


class TestCaptureHardening:
    """SF6 + T1: typo'd file paths fail loudly; argparse usage errors pin exit 2."""

    def _run(self, args, root, stdin=None):
        import os

        env = dict(os.environ, VAULT_ROOT=str(root))
        return subprocess.run(
            [sys.executable, str(CAPTURE), *args],
            capture_output=True,
            text=True,
            env=env,
            input=stdin,
        )

    @pytest.fixture
    def capture_root(self, tmp_path):
        (tmp_path / "vault-personal" / "inbox").mkdir(parents=True)
        return tmp_path

    def test_pathlike_missing_file_fails_loudly(self, capture_root):
        r = self._run(["./missing/report.pdf"], capture_root)
        assert r.returncode == 1
        assert "file not found" in r.stderr
        assert list((capture_root / "vault-personal" / "inbox").iterdir()) == []

    def test_tilde_missing_file_fails_loudly(self, capture_root):
        r = self._run(["~/definitely-not-a-real-file-xyz.pdf"], capture_root)
        assert r.returncode == 1
        assert "file not found" in r.stderr

    def test_single_word_note_still_works(self, capture_root):
        r = self._run(["hello"], capture_root)
        assert r.returncode == 0

    def test_quoted_prose_containing_slashes_is_a_note_not_a_path(self, capture_root):
        # A quoted multi-word note with a slash or tilde inside must not trip the
        # path guard — real paths don't contain spaces (found by live capture).
        text = "TCC blocks writes to ~/Documents from daemons; use ~/.vault/ instead."
        r = self._run([text], capture_root)
        assert r.returncode == 0, r.stderr
        created = list((capture_root / "vault-personal" / "inbox").iterdir())
        assert len(created) == 1
        assert text in created[0].read_text()

    def test_argparse_usage_error_exits_2(self, capture_root):
        r = self._run(["--vault", "shared", "note"], capture_root)
        assert r.returncode == 2


class TestStoreDimensionOnOpen:
    """Phase-1 residual #1: dim mismatch on an existing db must fail at open,
    not as a confusing per-document upsert error later.
    """

    def test_open_raises_when_existing_vec_dim_differs(self, tmp_path):
        db = tmp_path / "index.sqlite"
        Store(db, dimensions=64).close()
        with pytest.raises(ValueError, match="dimensions changed"):
            Store(db, dimensions=32)

    def test_open_ok_when_dim_matches(self, tmp_path):
        db = tmp_path / "index.sqlite"
        Store(db, dimensions=64).close()
        store = Store(db, dimensions=64)
        store.close()
