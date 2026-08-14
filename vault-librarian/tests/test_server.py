"""Tests for vault_librarian.server — written first, per CONTRACTS.md § server.py.

build_server is exercised through the real FastMCP call path (call_tool), with
FakeEmbedder + a pre-indexed Store injected through the factory seams. The
stdio rule (nothing on stdout — it belongs to the MCP protocol) and the lazy
construction contract (no embedder until a tool needs one) get explicit tests:
this server runs unattended under the second brain.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

import vault_librarian.server as server_module
from vault_librarian.config import Config, SearchSection, load_config
from vault_librarian.indexer import reindex
from vault_librarian.server import build_server, serve
from vault_librarian.store import Store

from .conftest import FakeEmbedder

DIM = 64

CONTRACT_TOOLS = {"get_page", "search_sources", "search_wiki"}
ENGRAMME_PAGE = "wiki/tools/engramme"
ENGRAMME_SOURCE = "src-2026-06-01-engramme-site"


class CountingFactory:
    """Zero-arg factory wrapper that counts how many times it is invoked."""

    def __init__(self, factory: Callable[[], object]) -> None:
        self.calls = 0
        self._factory = factory

    def __call__(self) -> object:
        self.calls += 1
        return self._factory()


def payload_of(result: object) -> dict:
    """Unwrap a FastMCP call_tool result into the tool's dict payload."""
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    return json.loads(result[0].text)


def with_search(config: Config, **overrides: int) -> Config:
    """Copy of config with the search section replaced by the given overrides."""
    return config.model_copy(update={"search": SearchSection(**overrides)})


def server_with(config: Config, store: Store, embedder: FakeEmbedder | None = None) -> FastMCP:
    """build_server wired to an existing store and a FakeEmbedder (or the given one)."""
    shared = embedder if embedder is not None else FakeEmbedder()

    def embedder_factory() -> FakeEmbedder:
        return shared

    def store_factory() -> Store:
        return store

    return build_server(config, embedder_factory=embedder_factory, store_factory=store_factory)


@pytest.fixture
def config(config_path) -> Config:
    return load_config(config_path)


@pytest.fixture
def store(config):
    with Store(config.store.path, dimensions=DIM) as s:
        yield s


@pytest.fixture
def indexed_store(config, store, fake_embedder):
    """The conftest vault (2 wiki pages, 1 source) indexed into `store`."""
    reindex(config, store, fake_embedder)
    return store


class TestBuildServer:
    def test_returns_fastmcp_named_vault_librarian(self, config):
        srv = build_server(config)
        assert isinstance(srv, FastMCP)
        assert srv.name == "vault-librarian"

    async def test_registers_exactly_the_three_contract_tools(self, config):
        srv = build_server(config)
        names = [t.name for t in await srv.list_tools()]
        assert len(names) == 3
        assert set(names) == CONTRACT_TOOLS

    async def test_every_tool_has_a_description(self, config):
        srv = build_server(config)
        for tool in await srv.list_tools():
            assert tool.description and tool.description.strip(), f"{tool.name} lacks description"

    async def test_top_k_schema_defaults_mirror_config(self, config):
        srv = build_server(with_search(config, default_top_k_wiki=7, default_top_k_sources=2))
        schemas = {t.name: t.inputSchema for t in await srv.list_tools()}
        assert schemas["search_wiki"]["properties"]["top_k"]["default"] == 7
        assert schemas["search_sources"]["properties"]["top_k"]["default"] == 2

    def test_build_writes_nothing_to_stdout(self, config, capsys):
        build_server(config)
        assert capsys.readouterr().out == ""


class TestLaziness:
    def test_factories_not_called_at_build_time(self, config):
        embedder_factory = CountingFactory(FakeEmbedder)
        store_factory = CountingFactory(lambda: None)
        build_server(config, embedder_factory=embedder_factory, store_factory=store_factory)
        assert embedder_factory.calls == 0
        assert store_factory.calls == 0

    async def test_embedder_factory_called_once_across_searches(self, config, indexed_store):
        embedder_factory = CountingFactory(FakeEmbedder)

        def store_factory() -> Store:
            return indexed_store

        srv = build_server(config, embedder_factory=embedder_factory, store_factory=store_factory)
        await srv.call_tool("search_wiki", {"query": "memory"})
        await srv.call_tool("search_sources", {"query": "memory"})
        assert embedder_factory.calls == 1

    async def test_get_page_never_constructs_an_embedder(self, config, indexed_store):
        embedder_factory = CountingFactory(FakeEmbedder)

        def store_factory() -> Store:
            return indexed_store

        srv = build_server(config, embedder_factory=embedder_factory, store_factory=store_factory)
        await srv.call_tool("get_page", {"page_id": ENGRAMME_PAGE})
        assert embedder_factory.calls == 0

    async def test_store_factory_called_once_across_tools(self, config, indexed_store):
        store_factory = CountingFactory(lambda: indexed_store)
        srv = build_server(config, embedder_factory=FakeEmbedder, store_factory=store_factory)
        await srv.call_tool("get_page", {"page_id": ENGRAMME_PAGE})
        await srv.call_tool("search_wiki", {"query": "memory"})
        await srv.call_tool("search_sources", {"query": "memory"})
        assert store_factory.calls == 1


class TestSearchTools:
    async def test_search_wiki_ranks_engramme_first_for_memory_query(self, config, indexed_store):
        srv = server_with(config, indexed_store)
        result = payload_of(
            await srv.call_tool("search_wiki", {"query": "memory augmentation startup"})
        )
        assert result["results"], result
        top = result["results"][0]
        assert top["page_id"] == ENGRAMME_PAGE
        assert set(top) == {"page_id", "heading", "excerpt", "score"}

    async def test_search_sources_returns_source_id_keyed_results(self, config, indexed_store):
        srv = server_with(config, indexed_store)
        result = payload_of(
            await srv.call_tool("search_sources", {"query": "memory augmentation startup"})
        )
        assert result["results"], result
        top = result["results"][0]
        assert top["source_id"] == ENGRAMME_SOURCE
        assert set(top) == {"source_id", "heading", "excerpt", "score"}

    async def test_search_sources_excerpt_capped_through_mcp_path(self, config, indexed_store):
        """The 15-word source cap must hold end-to-end, not only at the tools layer."""
        srv = server_with(config, indexed_store)
        result = payload_of(
            await srv.call_tool("search_sources", {"query": "memory augmentation startup"})
        )
        excerpt = result["results"][0]["excerpt"]
        assert excerpt.endswith("…")
        words = excerpt.split()
        assert len(words) == 16  # 15 content words + the appended ellipsis token

    async def test_explicit_top_k_overrides_default(self, config, indexed_store):
        srv = server_with(config, indexed_store)
        result = payload_of(
            await srv.call_tool("search_wiki", {"query": "memory recall", "top_k": 1})
        )
        assert len(result["results"]) == 1

    async def test_default_top_k_comes_from_config(self, config, indexed_store):
        # Two wiki chunks are indexed; with a config default of 1 only one may return.
        srv = server_with(with_search(config, default_top_k_wiki=1), indexed_store)
        result = payload_of(await srv.call_tool("search_wiki", {"query": "memory recall"}))
        assert len(result["results"]) == 1

    async def test_empty_query_returns_note_without_embedding(self, config, indexed_store):
        shared = FakeEmbedder()
        srv = server_with(config, indexed_store, embedder=shared)
        result = payload_of(await srv.call_tool("search_wiki", {"query": "   "}))
        assert result == {"results": [], "note": "empty query"}
        assert shared.calls == []  # the model is never touched for an empty query

    async def test_empty_index_returns_reindex_note(self, config, store):
        srv = server_with(config, store)  # store exists but nothing indexed
        result = payload_of(await srv.call_tool("search_wiki", {"query": "memory"}))
        assert result["results"] == []
        assert "reindex" in result["note"]


class TestGetPage:
    async def test_returns_page_content_by_doc_id(self, config, indexed_store):
        srv = server_with(config, indexed_store)
        payload = payload_of(await srv.call_tool("get_page", {"page_id": ENGRAMME_PAGE}))
        assert payload["page_id"] == ENGRAMME_PAGE
        assert payload["file_path"] == "wiki/tools/engramme.md"
        assert "memory augmentation startup" in payload["content"]

    async def test_missing_page_returns_error_dict_not_exception(self, config, indexed_store):
        srv = server_with(config, indexed_store)
        payload = payload_of(await srv.call_tool("get_page", {"page_id": "wiki/no-such-page"}))
        assert payload["error"] == "not found"
        assert payload["page_id"] == "wiki/no-such-page"
        assert "hint" in payload

    async def test_relative_path_traversal_is_rejected(self, config, indexed_store):
        srv = server_with(config, indexed_store)
        with pytest.raises(ToolError, match="escapes vault root"):
            await srv.call_tool("get_page", {"page_id": "../../../etc/passwd"})

    async def test_absolute_path_outside_vault_is_rejected(self, config, indexed_store):
        srv = server_with(config, indexed_store)
        with pytest.raises(ToolError, match="escapes vault root"):
            await srv.call_tool("get_page", {"page_id": "/etc/passwd"})


class TestDefaultFactories:
    async def test_get_page_works_with_default_factories(self, config):
        """Default store factory builds a real Store; no embedder is ever constructed."""
        srv = build_server(config)
        payload = payload_of(await srv.call_tool("get_page", {"page_id": ENGRAMME_PAGE}))
        assert "Engramme" in payload["content"]


class TestServe:
    def test_serve_builds_from_config_and_runs_stdio(self, config, monkeypatch):
        recorded = {}

        class FakeServer:
            def run(self, *args, **kwargs):
                recorded["run_args"] = args
                recorded["run_kwargs"] = kwargs

        def fake_build(cfg: Config, **kwargs) -> FakeServer:
            recorded["config"] = cfg
            return FakeServer()

        monkeypatch.setattr(server_module, "build_server", fake_build)
        serve(config)
        assert recorded["config"] is config
        args, kwargs = recorded["run_args"], recorded["run_kwargs"]
        transport = kwargs.get("transport", args[0] if args else "stdio")
        assert transport == "stdio"

    def test_serve_writes_nothing_to_stdout(self, config, monkeypatch, capsys):
        class FakeServer:
            def run(self, *args, **kwargs):
                pass

        monkeypatch.setattr(server_module, "build_server", lambda cfg, **kw: FakeServer())
        serve(config)
        assert capsys.readouterr().out == ""
