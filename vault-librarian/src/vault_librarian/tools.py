"""MCP tool logic: search_wiki, search_sources, get_page (CONTRACTS.md § tools.py).

Source excerpts are hard-capped at config.search.max_excerpt_words — the vault
contract: verbatim source quotes beyond that cap must be impossible through
this layer, regardless of what callers render. Wiki excerpts (our own
synthesis layer) cap at 60 words. get_page guards against path traversal:
every resolved path must stay inside the vault root.

The embedder is duck-typed via a Protocol (mirrors store.ChunkLike): this
module must not import embedder.py, whose heavyweight deps stay out of the
tool path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

import numpy as np

from .config import Config
from .store import Store

_EMPTY_QUERY_NOTE = "empty query"
_EMPTY_INDEX_NOTE = "index is empty — run: vault-librarian reindex"
_WIKI_EXCERPT_WORDS = 60
_MAX_PAGE_CHARS = 100_000
_EXCERPT_CHARS_PER_WORD = 40  # char ceiling: word caps alone leak whitespace-poor text
_NOT_FOUND_HINT = (
    "if recently ingested, try the vault-relative path (e.g. 'wiki/tools/page')"
    " or run: vault-librarian reindex"
)


class EmbedderLike(Protocol):
    """Structural stand-in for embedder.Embedder (only embed_query is needed here)."""

    def embed_query(self, text: str) -> np.ndarray: ...


def _excerpt(text: str, max_words: int) -> str:
    """First max_words words of text, char-bounded, with ' …' appended when truncated.

    The char ceiling is part of the vault quote contract: a single 5,000-char
    "word" (base64, CJK, minified text) must not ride through the word cap.
    """
    parts = text.split()
    if len(parts) > max_words:
        text = " ".join(parts[:max_words]) + " …"
    limit = max_words * _EXCERPT_CHARS_PER_WORD
    if len(text) > limit:
        text = text[:limit] + " …"
    return text


def _search(
    kind: str,
    id_key: str,
    max_words: int,
    store: Store,
    embedder: EmbedderLike,
    query: str,
    top_k: int,
) -> dict:
    if not query.strip():
        return {"results": [], "note": _EMPTY_QUERY_NOTE}
    try:
        if not store.has_chunks(kind):
            # Checked before embedding: searching nothing must not pay the model load.
            return {"results": [], "note": _EMPTY_INDEX_NOTE}
        hits = store.search(kind, embedder.embed_query(query), top_k)
    except sqlite3.OperationalError as exc:
        # SQLITE_BUSY past the busy_timeout (e.g. concurrent reindex) — degrade
        # gracefully instead of surfacing a raw ToolError to the session.
        return {
            "results": [],
            "note": f"index temporarily unavailable ({exc}) — retry shortly",
        }
    hits.sort(key=lambda h: h.score, reverse=True)  # store orders desc; enforce regardless
    return {
        "results": [
            {
                id_key: h.doc_id,
                "heading": h.heading,
                "excerpt": _excerpt(h.text, max_words),
                "score": h.score,
            }
            for h in hits
        ]
    }


def search_wiki(
    config: Config,
    store: Store,
    embedder: EmbedderLike,
    query: str,
    top_k: int | None = None,
) -> dict:
    """Semantic search over wiki chunks; excerpts capped at 60 words."""
    k = top_k if top_k is not None else config.search.default_top_k_wiki
    return _search("wiki", "page_id", _WIKI_EXCERPT_WORDS, store, embedder, query, k)


def search_sources(
    config: Config,
    store: Store,
    embedder: EmbedderLike,
    query: str,
    top_k: int | None = None,
) -> dict:
    """Semantic search over source chunks; excerpts hard-capped at max_excerpt_words."""
    k = top_k if top_k is not None else config.search.default_top_k_sources
    max_words = config.search.max_excerpt_words
    return _search("source", "source_id", max_words, store, embedder, query, k)


def get_page(config: Config, store: Store, page_id: str) -> dict:
    """Full markdown content of a vault file, by doc_id or vault-relative path.

    The path fallback deliberately serves ANY file inside the vault root —
    including journal/, inbox/, and _maintenance/ (e.g. Surgeon backups) — for
    a single-user vault that breadth is a feature, and it is documented here so
    the surface is honest. Raises ValueError when the resolved path escapes the
    vault root (security, mandatory). A missing page returns
    {"error": "not found", ...}, never raises; an unreadable one returns
    {"error": "unreadable: ...", ...}.
    """
    root = config.vault.root.resolve()

    candidates: list[Path] = []
    doc = store.get_document(page_id)
    if doc is not None:
        fp = Path(doc["file_path"])
        candidates.append(fp if fp.is_absolute() else root / fp)
    candidates.append(root / page_id)
    if not page_id.endswith(".md"):
        candidates.append(root / f"{page_id}.md")

    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("page_id escapes vault root")
        if resolved.is_file():
            try:
                content = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                return {"error": f"unreadable: {exc}", "page_id": page_id}
            result = {
                "page_id": page_id,
                "file_path": resolved.relative_to(root).as_posix(),
                "content": content[:_MAX_PAGE_CHARS],
            }
            if len(content) > _MAX_PAGE_CHARS:
                result["truncated"] = True
            return result
    return {"error": "not found", "page_id": page_id, "hint": _NOT_FOUND_HINT}
