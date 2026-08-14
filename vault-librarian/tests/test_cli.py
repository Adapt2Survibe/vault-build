"""Tests for vault_librarian.cli — written first, per CONTRACTS.md § cli.py.

main(argv) is exercised end-to-end against the conftest vault (2 wiki pages,
1 source) with a real Store; FakeEmbedder is injected through main's
keyword-only `embedder_factory` test seam so no test ever loads
sentence-transformers. Stdout discipline is asserted hard: reindex/status
print exactly one JSON line, serve prints nothing (stdout is the MCP channel).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import vault_librarian.server as server_module
from vault_librarian.cli import main
from vault_librarian.store import Store

from .conftest import FakeEmbedder, write_config_yaml

STATS_KEYS = {"scanned", "indexed", "skipped", "deleted", "chunks", "seconds", "errors"}
ENGRAMME_REL = Path("wiki/tools/engramme.md")
SPACED_REL = Path("wiki/concepts/spaced-repetition.md")


def reindex_argv(config_path, *extra: str) -> list[str]:
    return ["reindex", "--config", str(config_path), *extra]


def single_json_line(out: str) -> dict:
    """Assert stdout holds exactly one line and parse it as JSON."""
    lines = out.splitlines()
    assert len(lines) == 1, f"expected exactly one stdout line, got {out!r}"
    return json.loads(lines[0])


class TestNoSubcommand:
    def test_returns_2_and_prints_usage_to_stderr(self, capsys):
        rc = main([])
        captured = capsys.readouterr()
        assert rc == 2
        assert "usage" in captured.err.lower()
        assert captured.out == ""

    def test_argv_none_falls_back_to_sys_argv(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["vault-librarian"])
        assert main() == 2


class TestReindex:
    def test_prints_exactly_one_json_line_with_stats_keys(self, config_path, capsys):
        rc = main(reindex_argv(config_path), embedder_factory=FakeEmbedder)
        stats = single_json_line(capsys.readouterr().out)
        assert rc == 0
        assert set(stats) == STATS_KEYS

    def test_fresh_vault_stats_values(self, config_path, capsys):
        main(reindex_argv(config_path), embedder_factory=FakeEmbedder)
        stats = single_json_line(capsys.readouterr().out)
        assert stats["scanned"] == 3  # 2 wiki + 1 source; _index.md and journal/inbox skipped
        assert stats["indexed"] == 3
        assert stats["skipped"] == 0
        assert stats["deleted"] == 0
        assert stats["chunks"] >= 3
        assert stats["seconds"] >= 0
        assert stats.get("errors", 0) == 0

    def test_human_log_lines_go_to_stderr_not_stdout(self, config_path, capsys):
        main(reindex_argv(config_path), embedder_factory=FakeEmbedder)
        captured = capsys.readouterr()
        assert len(captured.out.splitlines()) == 1  # stdout is machine-readable only
        assert "reindex" in captured.err.lower()

    def test_second_run_skips_unchanged_documents(self, config_path, capsys):
        main(reindex_argv(config_path), embedder_factory=FakeEmbedder)
        capsys.readouterr()
        main(reindex_argv(config_path), embedder_factory=FakeEmbedder)
        stats = single_json_line(capsys.readouterr().out)
        assert stats["indexed"] == 0
        assert stats["skipped"] == 3

    def test_force_reindexes_unchanged_documents(self, config_path, capsys):
        main(reindex_argv(config_path), embedder_factory=FakeEmbedder)
        capsys.readouterr()
        main(reindex_argv(config_path, "--force"), embedder_factory=FakeEmbedder)
        stats = single_json_line(capsys.readouterr().out)
        assert stats["indexed"] == 3
        assert stats["skipped"] == 0

    def test_only_restricts_to_the_given_path(self, config_path, vault_root, capsys):
        argv = reindex_argv(config_path, "--only", str(vault_root / ENGRAMME_REL))
        rc = main(argv, embedder_factory=FakeEmbedder)
        stats = single_json_line(capsys.readouterr().out)
        assert rc == 0
        assert stats["scanned"] == 1
        assert stats["indexed"] == 1

    def test_only_accepts_multiple_paths(self, config_path, vault_root, capsys):
        argv = reindex_argv(
            config_path,
            "--only",
            str(vault_root / ENGRAMME_REL),
            str(vault_root / SPACED_REL),
        )
        main(argv, embedder_factory=FakeEmbedder)
        stats = single_json_line(capsys.readouterr().out)
        assert stats["scanned"] == 2

    def test_only_skips_the_deletion_pass(self, config_path, vault_root, tmp_path, capsys):
        """A single-file reindex must never garbage-collect the rest of the index."""
        main(reindex_argv(config_path), embedder_factory=FakeEmbedder)
        capsys.readouterr()
        (vault_root / SPACED_REL).unlink()
        argv = reindex_argv(config_path, "--only", str(vault_root / ENGRAMME_REL))
        main(argv, embedder_factory=FakeEmbedder)
        stats = single_json_line(capsys.readouterr().out)
        assert stats["deleted"] == 0
        with Store(tmp_path / "index.sqlite", dimensions=64) as store:
            assert "wiki/concepts/spaced-repetition" in store.indexed_state()

    def test_full_reindex_deletes_documents_missing_on_disk(
        self, config_path, vault_root, tmp_path, capsys
    ):
        """Without --only the CLI must run the deletion pass (only=None, not [])."""
        main(reindex_argv(config_path), embedder_factory=FakeEmbedder)
        capsys.readouterr()
        (vault_root / SPACED_REL).unlink()
        main(reindex_argv(config_path), embedder_factory=FakeEmbedder)
        stats = single_json_line(capsys.readouterr().out)
        assert stats["deleted"] == 1
        with Store(tmp_path / "index.sqlite", dimensions=64) as store:
            assert "wiki/concepts/spaced-repetition" not in store.indexed_state()

    def test_missing_config_exits_2_with_stderr_message(self, tmp_path, capsys):
        rc = main(reindex_argv(tmp_path / "no-such-config.yaml"), embedder_factory=FakeEmbedder)
        captured = capsys.readouterr()
        assert rc == 2
        assert captured.out == ""
        assert "no-such-config.yaml" in captured.err

    def test_missing_vault_root_exits_2_with_stderr_message(self, tmp_path, capsys):
        cfg = tmp_path / "config.yaml"
        write_config_yaml(cfg, tmp_path / "no-such-vault", tmp_path / "index.sqlite")
        rc = main(reindex_argv(cfg), embedder_factory=FakeEmbedder)
        captured = capsys.readouterr()
        assert rc == 2
        assert captured.out == ""
        assert "vault root" in captured.err.lower()


class TestStatus:
    def test_reports_db_counts_and_last_indexed_at(self, config_path, tmp_path, capsys):
        main(reindex_argv(config_path), embedder_factory=FakeEmbedder)
        capsys.readouterr()
        rc = main(["status", "--config", str(config_path)])
        payload = single_json_line(capsys.readouterr().out)
        assert rc == 0
        assert set(payload) == {"db", "counts", "last_indexed_at"}
        assert Path(payload["db"]) == (tmp_path / "index.sqlite").resolve()
        assert payload["counts"]["wiki_docs"] == 2
        assert payload["counts"]["source_docs"] == 1
        assert payload["counts"]["wiki_chunks"] >= 2
        assert payload["counts"]["source_chunks"] >= 1
        assert isinstance(payload["last_indexed_at"], str) and payload["last_indexed_at"]

    def test_fresh_db_reports_zero_counts_and_null_last_indexed(self, config_path, capsys):
        rc = main(["status", "--config", str(config_path)])
        payload = single_json_line(capsys.readouterr().out)
        assert rc == 0
        assert payload["counts"] == {
            "wiki_docs": 0,
            "source_docs": 0,
            "wiki_chunks": 0,
            "source_chunks": 0,
        }
        assert payload["last_indexed_at"] is None

    def test_never_constructs_an_embedder(self, config_path, capsys):
        def forbidden() -> FakeEmbedder:
            raise AssertionError("status must not construct an embedder")

        rc = main(["status", "--config", str(config_path)], embedder_factory=forbidden)
        assert rc == 0

    def test_missing_config_exits_2_with_stderr_message(self, tmp_path, capsys):
        rc = main(["status", "--config", str(tmp_path / "ghost.yaml")])
        captured = capsys.readouterr()
        assert rc == 2
        assert captured.out == ""
        assert "ghost.yaml" in captured.err


class TestServeCommand:
    def test_calls_server_serve_with_the_loaded_config(
        self, config_path, vault_root, monkeypatch
    ):
        seen = {}

        def fake_serve(config) -> None:
            seen["config"] = config

        monkeypatch.setattr(server_module, "serve", fake_serve)
        rc = main(["serve", "--config", str(config_path)])
        assert rc == 0
        assert seen["config"].vault.root == vault_root.resolve()

    def test_serve_prints_nothing_to_stdout(self, config_path, monkeypatch, capsys):
        monkeypatch.setattr(server_module, "serve", lambda config: None)
        rc = main(["serve", "--config", str(config_path)])
        assert rc == 0
        assert capsys.readouterr().out == ""

    def test_missing_config_exits_2_and_never_serves(self, tmp_path, monkeypatch, capsys):
        calls: list[object] = []
        monkeypatch.setattr(server_module, "serve", calls.append)
        rc = main(["serve", "--config", str(tmp_path / "ghost.yaml")])
        captured = capsys.readouterr()
        assert rc == 2
        assert calls == []
        assert captured.out == ""
        assert captured.err != ""
