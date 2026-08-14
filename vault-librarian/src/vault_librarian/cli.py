"""argparse CLI entry point for vault-librarian: serve / reindex / status.

Per CONTRACTS.md § cli.py: ``reindex`` and ``status`` print exactly one JSON
line to stdout (machine-readable — Scribe and the stamp pipeline parse it);
human log lines go to stderr. ``status`` never constructs an embedder, so it
stays fast. Exit codes: 0 on success, 2 on error (missing config, missing
vault root, no subcommand).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

import yaml

from . import indexer, server
from .config import Config, load_config
from .embedder import Embedder
from .store import Store


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vault-librarian",
        description="Local MCP semantic-search server over the vault's wiki and sources.",
    )
    sub = parser.add_subparsers(dest="command")

    serve_p = sub.add_parser("serve", help="run the MCP server on stdio (blocks)")
    serve_p.add_argument("--config", required=True, help="path to the config YAML")

    reindex_p = sub.add_parser("reindex", help="incrementally reindex the vault")
    reindex_p.add_argument("--config", required=True, help="path to the config YAML")
    reindex_p.add_argument("--force", action="store_true", help="reindex even when hashes match")
    reindex_p.add_argument(
        "--only",
        nargs="+",
        action="extend",
        type=Path,
        default=None,
        metavar="PATH",
        help="restrict the reindex to these files (skips the deletion pass)",
    )

    status_p = sub.add_parser("status", help="print index status as one JSON line")
    status_p.add_argument("--config", required=True, help="path to the config YAML")
    return parser


def _cmd_reindex(
    config: Config,
    force: bool,
    only: list[Path] | None,
    embedder_factory: Callable[[], indexer.EmbedderLike] | None,
) -> int:
    if not config.vault.root.is_dir():
        print(f"vault-librarian: vault root not found: {config.vault.root}", file=sys.stderr)
        return 2

    def _default_embedder() -> Embedder:
        return Embedder(
            config.embedding.model,
            device=config.embedding.device,
            revision=config.embedding.revision,
        )

    make_embedder = embedder_factory if embedder_factory is not None else _default_embedder
    with Store(config.store.path, dimensions=config.embedding.dimensions) as store:
        stats = indexer.reindex(config, store, make_embedder(), force=force, only=only)
    d = stats.to_dict()
    print(
        "vault-librarian: reindex complete:"
        f" scanned={d['scanned']} indexed={d['indexed']} skipped={d['skipped']}"
        f" deleted={d['deleted']} chunks={d['chunks']} errors={d.get('errors', 0)}"
        f" in {d['seconds']}s",
        file=sys.stderr,
    )
    print(json.dumps(d))
    errors = d.get("errors", 0)
    if errors > 0 and d["indexed"] == 0 and errors == d["scanned"] - d["skipped"]:
        # Every document that needed indexing failed (e.g. model load dead) —
        # exit-code consumers must not read that as success.
        print("vault-librarian: reindex failed for every document it attempted", file=sys.stderr)
        return 1
    return 0


def _cmd_status(config: Config) -> int:
    with Store(config.store.path, dimensions=config.embedding.dimensions) as store:
        payload = {
            "db": str(config.store.path),
            "counts": store.counts(),
            "last_indexed_at": store.last_indexed_at(),
        }
    print(json.dumps(payload))
    return 0


def main(
    argv: list[str] | None = None,
    *,
    embedder_factory: Callable[[], indexer.EmbedderLike] | None = None,
) -> int:
    """CLI entry point. Returns the process exit code (0 success, 2 error).

    ``embedder_factory`` is a keyword-only test seam (sanctioned deviation from
    CONTRACTS.md): tests inject a FakeEmbedder so reindex never loads
    sentence-transformers. Production callers leave it None.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        return 2
    try:
        config = load_config(args.config)
    except FileNotFoundError as exc:
        print(f"vault-librarian: {exc}", file=sys.stderr)
        return 2
    except (yaml.YAMLError, ValueError) as exc:
        # ValueError covers pydantic ValidationError (its subclass) and the
        # empty-config guard — a broken config is as operational as a missing one.
        print(f"vault-librarian: invalid config {args.config}: {exc}", file=sys.stderr)
        return 2
    if args.command == "serve":
        server.serve(config)
        return 0
    if args.command == "reindex":
        return _cmd_reindex(
            config, force=args.force, only=args.only, embedder_factory=embedder_factory
        )
    return _cmd_status(config)
