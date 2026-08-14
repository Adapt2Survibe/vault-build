"""Vault file discovery and incremental reindex (CONTRACTS.md § indexer.py).

Discovery covers `wiki/**/*.md` and `sources/*.md` (never journal/ or inbox/
in Phase 1), skipping `_index.md` files. Reindex is hash-driven: unchanged
documents are skipped, missing ones are deleted (unless `only` is given —
a single-file reindex must never garbage-collect the rest of the index).
Each document embeds independently: one bad file is logged to stderr and
counted in `errors`, never aborting the sweep.
"""

from __future__ import annotations

import hashlib
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from .chunker import chunk_markdown, parse_front_matter
from .config import Config
from .store import Store


class EmbedderLike(Protocol):
    """Structural stand-in for embedder.Embedder (tests inject FakeEmbedder)."""

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...


@dataclass
class DocRef:
    kind: str  # "wiki" | "source"
    doc_id: str
    path: Path


@dataclass
class IndexStats:
    scanned: int
    indexed: int
    skipped: int
    deleted: int
    chunks: int
    seconds: float
    errors: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _source_doc_id(path: Path) -> str:
    """Front-matter `id` when present and `src-`-prefixed, else `src-<filename stem>`.

    The prefix rule is load-bearing: an arbitrary front-matter id (say,
    `wiki/tools/engramme`) would collide with another document's doc_id and
    silently evict it from the index.
    """
    try:
        meta, _ = parse_front_matter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        meta = {}  # unreadable here surfaces as a per-doc error during reindex
    doc_id = meta.get("id")
    if isinstance(doc_id, str) and doc_id.strip():
        did = doc_id.strip()
        if did.startswith("src-"):
            return did
        print(
            f"vault-librarian: warning: non-conforming front-matter id {did!r} in {path}"
            f" — using src-{path.stem}",
            file=sys.stderr,
        )
    return f"src-{path.stem}"


def discover(config: Config) -> list[DocRef]:
    """Find indexable documents under the vault root (wiki recursive, sources flat)."""
    root = config.vault.root
    refs: list[DocRef] = []
    wiki_dir = root / "wiki"
    if wiki_dir.is_dir():
        for path in sorted(wiki_dir.rglob("*.md")):
            if path.name == "_index.md":
                continue
            doc_id = path.relative_to(root).with_suffix("").as_posix()
            refs.append(DocRef(kind="wiki", doc_id=doc_id, path=path))
    sources_dir = root / "sources"
    if sources_dir.is_dir():
        seen = {r.doc_id for r in refs}
        for path in sorted(sources_dir.glob("*.md")):
            if path.name == "_index.md":
                continue
            doc_id = _source_doc_id(path)
            if doc_id in seen:
                fallback = f"src-{path.stem}"
                print(
                    f"vault-librarian: warning: duplicate source id {doc_id!r} in {path}"
                    f" — using {fallback}",
                    file=sys.stderr,
                )
                doc_id = fallback
            seen.add(doc_id)
            refs.append(DocRef(kind="source", doc_id=doc_id, path=path))
    return refs


def reindex(
    config: Config,
    store: Store,
    embedder: EmbedderLike,
    force: bool = False,
    only: list[Path] | None = None,
) -> IndexStats:
    """Incrementally reindex the vault. Returns counts for the run.

    `only` restricts processing to the given files AND skips the deletion
    pass entirely. `force` reindexes regardless of stored content hashes.
    """
    start = time.monotonic()
    refs = discover(config)
    if only is not None:
        wanted = {Path(p).resolve() for p in only}
        refs = [r for r in refs if r.path.resolve() in wanted]
    state = store.indexed_state()

    scanned = indexed = skipped = deleted = chunks = errors = 0
    for ref in refs:
        scanned += 1
        try:
            raw = ref.path.read_bytes()
        except OSError as exc:
            print(
                f"vault-librarian: error reading {ref.path}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            errors += 1
            continue
        content_hash = hashlib.sha256(raw).hexdigest()
        if not force and state.get(ref.doc_id) == content_hash:
            skipped += 1
            continue
        try:
            _, body = parse_front_matter(raw.decode("utf-8"))
            doc_chunks = chunk_markdown(
                body,
                chunk_size_tokens=config.ingest.chunk_size_tokens,
                overlap_tokens=config.ingest.chunk_overlap_tokens,
            )
            if not doc_chunks:
                # Empty body: still upsert (zero chunks) so previously-indexed
                # chunks are cleared and the stored hash advances — an emptied
                # document must stop being searchable.
                store.upsert_document(
                    kind=ref.kind,
                    doc_id=ref.doc_id,
                    file_path=str(ref.path),
                    content_hash=content_hash,
                    chunks=[],
                    embeddings=embedder.embed_documents([]),
                )
                skipped += 1
                continue
            embeddings = embedder.embed_documents([c.text for c in doc_chunks])
            n = store.upsert_document(
                kind=ref.kind,
                doc_id=ref.doc_id,
                file_path=str(ref.path),
                content_hash=content_hash,
                chunks=doc_chunks,
                embeddings=embeddings,
            )
        except Exception as exc:  # one bad file must not abort the sweep
            print(
                f"vault-librarian: error indexing {ref.path} ({ref.doc_id}):"
                f" {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            errors += 1
            continue
        indexed += 1
        chunks += n

    if only is None:
        discovered_ids = {r.doc_id for r in refs}
        if not refs and state:
            # A transient discovery failure must never wipe a non-empty index.
            print(
                "vault-librarian: warning: discovery found no documents but the index"
                " is non-empty — skipping the deletion pass",
                file=sys.stderr,
            )
        else:
            for doc_id in state:
                if doc_id in discovered_ids:
                    continue
                doc = store.get_document(doc_id)
                if doc is not None and Path(doc["file_path"]).exists():
                    # The file still exists — its doc_id drifted (unreadable file,
                    # changed front matter). Deleting here would evict live data.
                    print(
                        f"vault-librarian: warning: {doc_id!r} not discoverable but its"
                        f" file still exists ({doc['file_path']}) — keeping",
                        file=sys.stderr,
                    )
                    continue
                store.delete_document(doc_id)
                deleted += 1

    return IndexStats(
        scanned=scanned,
        indexed=indexed,
        skipped=skipped,
        deleted=deleted,
        chunks=chunks,
        seconds=round(time.monotonic() - start, 3),
        errors=errors,
    )
