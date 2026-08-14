"""YAML config loading and validation for vault-librarian.

Schema per CONTRACTS.md: `vault`, `embedding`, `store` sections are required;
`server`, `search`, `ingest` are optional with defaults. Path fields expand
`~` and resolve to absolute paths.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator


class VaultSection(BaseModel):
    name: str
    root: Path

    @field_validator("root")
    @classmethod
    def _expand_root(cls, v: Path) -> Path:
        return v.expanduser().resolve()


class EmbeddingSection(BaseModel):
    model: str = "nomic-ai/nomic-embed-text-v1.5"
    dimensions: int = 768
    device: str = "cpu"
    # Pin to a model repo commit SHA: trust_remote_code executes the repo's code,
    # so an unpinned revision re-fetches and runs whatever the repo serves next.
    revision: str | None = None


class StoreSection(BaseModel):
    backend: str = "sqlite-vec"
    path: Path

    @field_validator("path")
    @classmethod
    def _expand_path(cls, v: Path) -> Path:
        return v.expanduser().resolve()


class ServerSection(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8001


class SearchSection(BaseModel):
    default_top_k_wiki: int = 5
    default_top_k_sources: int = 3
    max_excerpt_words: int = 15


class IngestSection(BaseModel):
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 64


class Config(BaseModel):
    vault: VaultSection
    embedding: EmbeddingSection
    store: StoreSection
    server: ServerSection = ServerSection()
    search: SearchSection = SearchSection()
    ingest: IngestSection = IngestSection()


def load_config(path: str | Path) -> Config:
    """Load and validate a vault-librarian config YAML.

    Raises FileNotFoundError (path in message) when the file is missing.
    Invalid YAML / failed validation propagate from yaml/pydantic.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"config file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if data is None:
        raise ValueError(f"config file is empty: {p}")
    return Config.model_validate(data)
