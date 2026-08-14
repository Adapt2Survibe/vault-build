"""FastMCP wiring for vault-librarian (CONTRACTS.md § server.py).

stdio transport: stdout belongs to the MCP protocol, so nothing in this module
may print to it — diagnostics go to stderr. ``build_server`` only registers
tools; the Embedder and Store are created lazily, at most once, on first tool
use, keeping the stdio handshake fast and the build side-effect free.
"""

from __future__ import annotations

import atexit
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from mcp.server.fastmcp import FastMCP

from . import tools
from .config import Config
from .embedder import Embedder
from .store import Store

_T = TypeVar("_T")
_UNSET: Any = object()  # None is a legitimate factory value; a sentinel guards caching


class _LazyOnce(Generic[_T]):
    """Calls the factory on first use and reuses the value after (per-server holder).

    Not thread-safe — fine for stdio MCP (single client); revisit before any
    Phase 3 HTTP transport reuses it.
    """

    def __init__(self, factory: Callable[[], _T]) -> None:
        self._factory = factory
        self._value: Any = _UNSET

    def get(self) -> _T:
        if self._value is _UNSET:
            self._value = self._factory()
        return self._value


def build_server(
    config: Config,
    embedder_factory: Callable[[], tools.EmbedderLike] | None = None,
    store_factory: Callable[[], Store] | None = None,
) -> FastMCP:
    """Build the vault-librarian FastMCP server with the three vault tools registered.

    Factories are injectable for tests; the defaults construct the real
    Embedder and Store from ``config``. Neither factory is called until a tool
    first needs its product.
    """

    def _default_embedder() -> Embedder:
        return Embedder(
            config.embedding.model,
            device=config.embedding.device,
            revision=config.embedding.revision,
        )

    def _default_store() -> Store:
        return Store(config.store.path, dimensions=config.embedding.dimensions)

    lazy_embedder: _LazyOnce[tools.EmbedderLike] = _LazyOnce(
        embedder_factory if embedder_factory is not None else _default_embedder
    )
    lazy_store: _LazyOnce[Store] = _LazyOnce(
        store_factory if store_factory is not None else _default_store
    )

    mcp = FastMCP(name="vault-librarian")

    @mcp.tool()
    def search_wiki(query: str, top_k: int = config.search.default_top_k_wiki) -> dict:
        """Search SYNTHESIZED wiki entries — curated conclusions, not raw material.

        Use when you want what the vault has concluded about a topic. Returns
        page_id (feed it to get_page for the full entry), heading, excerpt
        (up to 60 words), score. For raw captured material use search_sources.
        """
        return tools.search_wiki(config, lazy_store.get(), lazy_embedder.get(), query, top_k)

    @mcp.tool()
    def search_sources(query: str, top_k: int = config.search.default_top_k_sources) -> dict:
        """Search RAW ingested sources — websites, articles, transcripts, notes.

        Use when you want the original captured material rather than synthesized
        conclusions (those live in search_wiki). Returns source_id (feed it to
        get_page), heading, excerpt (hard-capped at 15 words per the vault quote
        contract), score.
        """
        return tools.search_sources(config, lazy_store.get(), lazy_embedder.get(), query, top_k)

    @mcp.tool()
    def get_page(page_id: str) -> dict:
        """Fetch the full markdown of a vault file after a search.

        page_id: a doc id from search results ('wiki/tools/engramme',
        'src-2026-06-01-engramme-site') or any vault-relative path. Content is
        capped at 100k chars (truncated flag set when capped).
        """
        return tools.get_page(config, lazy_store.get(), page_id)

    return mcp


def serve(config: Config) -> None:
    """Build the server from config and run it on stdio (blocks until disconnect)."""
    opened: list[Store] = []

    def _tracked_store() -> Store:
        store = Store(config.store.path, dimensions=config.embedding.dimensions)
        opened.append(store)
        return store

    server = build_server(config, store_factory=_tracked_store)
    atexit.register(lambda: [s.close() for s in opened])
    server.run(transport="stdio")
