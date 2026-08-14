"""Tests for vault_librarian.config — written first, per CONTRACTS.md `config.py`."""

from pathlib import Path

import pydantic
import pytest
import yaml

from vault_librarian.config import (
    Config,
    EmbeddingSection,
    IngestSection,
    SearchSection,
    ServerSection,
    StoreSection,
    VaultSection,
    load_config,
)

from .conftest import write_config_yaml


class TestLoadConfigHappyPath:
    def test_returns_config_instance(self, config_path):
        cfg = load_config(config_path)
        assert isinstance(cfg, Config)

    def test_vault_section_values(self, config_path, vault_root):
        cfg = load_config(config_path)
        assert cfg.vault.name == "personal"
        assert cfg.vault.root == vault_root.resolve()

    def test_embedding_section_values(self, config_path):
        cfg = load_config(config_path)
        assert cfg.embedding.model == "fake-test-model"
        assert cfg.embedding.dimensions == 64
        assert cfg.embedding.device == "cpu"

    def test_store_section_values(self, config_path, tmp_path):
        cfg = load_config(config_path)
        assert cfg.store.backend == "sqlite-vec"
        assert cfg.store.path == (tmp_path / "index.sqlite").resolve()

    def test_accepts_str_path(self, config_path):
        cfg = load_config(str(config_path))
        assert isinstance(cfg, Config)

    def test_paths_are_absolute(self, config_path):
        cfg = load_config(config_path)
        assert cfg.vault.root.is_absolute()
        assert cfg.store.path.is_absolute()

    def test_paths_are_path_objects(self, config_path):
        cfg = load_config(config_path)
        assert isinstance(cfg.vault.root, Path)
        assert isinstance(cfg.store.path, Path)


class TestOptionalSectionDefaults:
    """server/search/ingest are optional in YAML; the test config omits all three."""

    def test_server_defaults(self, config_path):
        cfg = load_config(config_path)
        assert cfg.server.host == "127.0.0.1"
        assert cfg.server.port == 8001

    def test_search_defaults(self, config_path):
        cfg = load_config(config_path)
        assert cfg.search.default_top_k_wiki == 5
        assert cfg.search.default_top_k_sources == 3
        assert cfg.search.max_excerpt_words == 15

    def test_ingest_defaults(self, config_path):
        cfg = load_config(config_path)
        assert cfg.ingest.chunk_size_tokens == 512
        assert cfg.ingest.chunk_overlap_tokens == 64

    def test_explicit_optional_sections_override_defaults(self, tmp_path, vault_root):
        p = tmp_path / "config.full.yaml"
        p.write_text(
            f"vault:\n  name: company\n  root: {vault_root}\n"
            f"embedding:\n  model: m\n  dimensions: 32\n  device: mps\n"
            f"store:\n  backend: sqlite-vec\n  path: {tmp_path / 'db.sqlite'}\n"
            "server:\n  host: 0.0.0.0\n  port: 8002\n"
            "search:\n  default_top_k_wiki: 9\n  default_top_k_sources: 7\n"
            "  max_excerpt_words: 20\n"
            "ingest:\n  chunk_size_tokens: 256\n  chunk_overlap_tokens: 32\n"
        )
        cfg = load_config(p)
        assert cfg.server.port == 8002
        assert cfg.search.default_top_k_wiki == 9
        assert cfg.search.default_top_k_sources == 7
        assert cfg.search.max_excerpt_words == 20
        assert cfg.ingest.chunk_size_tokens == 256
        assert cfg.ingest.chunk_overlap_tokens == 32


class TestTildeExpansion:
    def test_vault_root_tilde_expanded(self, tmp_path):
        p = tmp_path / "config.tilde.yaml"
        p.write_text(
            "vault:\n  name: personal\n  root: ~/some-vault-that-need-not-exist\n"
            "embedding:\n  model: m\n  dimensions: 8\n  device: cpu\n"
            f"store:\n  backend: sqlite-vec\n  path: {tmp_path / 'db.sqlite'}\n"
        )
        cfg = load_config(p)
        assert cfg.vault.root == (Path.home() / "some-vault-that-need-not-exist").resolve()
        assert "~" not in str(cfg.vault.root)

    def test_store_path_tilde_expanded(self, tmp_path, vault_root):
        p = tmp_path / "config.tilde2.yaml"
        p.write_text(
            f"vault:\n  name: personal\n  root: {vault_root}\n"
            "embedding:\n  model: m\n  dimensions: 8\n  device: cpu\n"
            "store:\n  backend: sqlite-vec\n  path: ~/idx/index.sqlite\n"
        )
        cfg = load_config(p)
        assert cfg.store.path == (Path.home() / "idx" / "index.sqlite").resolve()
        assert "~" not in str(cfg.store.path)


class TestLoadConfigErrors:
    def test_missing_file_raises_filenotfounderror(self, tmp_path):
        missing = tmp_path / "nope" / "config.yaml"
        with pytest.raises(FileNotFoundError):
            load_config(missing)

    def test_missing_file_message_contains_path(self, tmp_path):
        missing = tmp_path / "absent.yaml"
        with pytest.raises(FileNotFoundError, match="absent.yaml"):
            load_config(missing)

    def test_missing_required_section_raises_validation_error(self, tmp_path, vault_root):
        p = tmp_path / "config.nostore.yaml"
        p.write_text(
            f"vault:\n  name: personal\n  root: {vault_root}\n"
            "embedding:\n  model: m\n  dimensions: 8\n  device: cpu\n"
        )
        with pytest.raises(pydantic.ValidationError):
            load_config(p)

    def test_missing_required_field_raises_validation_error(self, tmp_path):
        p = tmp_path / "config.noroot.yaml"
        p.write_text(
            "vault:\n  name: personal\n"  # root missing
            "embedding:\n  model: m\n  dimensions: 8\n  device: cpu\n"
            f"store:\n  backend: sqlite-vec\n  path: {tmp_path / 'db.sqlite'}\n"
        )
        with pytest.raises(pydantic.ValidationError):
            load_config(p)

    def test_wrong_field_type_raises_validation_error(self, tmp_path, vault_root):
        p = tmp_path / "config.badtype.yaml"
        p.write_text(
            f"vault:\n  name: personal\n  root: {vault_root}\n"
            "embedding:\n  model: m\n  dimensions: not-a-number\n  device: cpu\n"
            f"store:\n  backend: sqlite-vec\n  path: {tmp_path / 'db.sqlite'}\n"
        )
        with pytest.raises(pydantic.ValidationError):
            load_config(p)

    def test_invalid_yaml_propagates(self, tmp_path):
        p = tmp_path / "config.broken.yaml"
        p.write_text("vault: [unclosed\n  embedding: {\n")
        with pytest.raises(yaml.YAMLError):
            load_config(p)


class TestSectionModels:
    """The section models are imported by sibling modules — pin names and defaults."""

    def test_embedding_defaults(self):
        s = EmbeddingSection()
        assert s.model == "nomic-ai/nomic-embed-text-v1.5"
        assert s.dimensions == 768
        assert s.device == "cpu"

    def test_server_defaults(self):
        s = ServerSection()
        assert s.host == "127.0.0.1"
        assert s.port == 8001

    def test_search_defaults(self):
        s = SearchSection()
        assert s.default_top_k_wiki == 5
        assert s.default_top_k_sources == 3
        assert s.max_excerpt_words == 15

    def test_ingest_defaults(self):
        s = IngestSection()
        assert s.chunk_size_tokens == 512
        assert s.chunk_overlap_tokens == 64

    def test_store_section_defaults_backend(self, tmp_path):
        s = StoreSection(path=tmp_path / "x.sqlite")
        assert s.backend == "sqlite-vec"

    def test_vault_section_requires_name_and_root(self):
        with pytest.raises(pydantic.ValidationError):
            VaultSection()  # type: ignore[call-arg]

    def test_config_uses_write_config_yaml_helper(self, tmp_path, vault_root):
        # Guard: helper output stays loadable (other builders rely on it too).
        p = tmp_path / "config.helper.yaml"
        write_config_yaml(p, vault_root, tmp_path / "db.sqlite", dimensions=128)
        cfg = load_config(p)
        assert cfg.embedding.dimensions == 128
