# vault-librarian

MCP server providing semantic search over the vault's wiki and sources.

## Status

**Implemented (Phase 1, 2026-06-12).** All modules live, 322 tests, stdio MCP transport.

Interface contracts: `CONTRACTS.md` (including the 2026-06-12 review-wall amendments).

## Architecture (planned)

- **Embedding model:** nomic-embed-text-v1.5 via sentence-transformers (768-dim)
- **Vector store:** sqlite-vec for Phase 1; Qdrant evaluated at the 500-source mark in Phase 3
- **Tools exposed via MCP:**
  - `search_wiki(query, top_k=5)` returns wiki chunks with citations
  - `search_sources(query, top_k=3)` returns source passages
  - `get_page(page_id)` returns full page content

## Configuration

One config file per vault. See `config.example.yaml` for the schema.

Start with `config.personal.yaml` only. Add `config.company.yaml` when a second vault is real — register it under a **different** MCP server name.

Both config files are `.gitignore`d to prevent leaking paths or future secrets.

## Install

The venv lives OUTSIDE iCloud-synced space (`~/Documents` is iCloud-synced; fileproviderd churn inside a venv mints "name 2" conflict artifacts and flips UF_HIDDEN flags). `.venv` here is a symlink:

```
cd vault-librarian
uv venv --python 3.12 ~/.venvs/vault-librarian
ln -s ~/.venvs/vault-librarian .venv
VIRTUAL_ENV="$HOME/.venvs/vault-librarian" uv pip install -e '.[dev]'
chflags -R nohidden ~/.venvs/vault-librarian   # belt; sitecustomize below is the suspenders
```

**Why the chflags line:** uv marks `.venv` files with macOS `UF_HIDDEN`, and Python 3.12's `site.py` skips hidden `.pth` files — so without it the editable install is silently inert and every import of `vault_librarian` fails. uv re-flags on each install; re-run the chflags after any `uv pip` operation.

**Durable guard (recreate after a venv rebuild OR a repo move):** `sitecustomize.py` in the real venv (`~/.venvs/vault-librarian/.../site-packages/`) inserts `src/` onto `sys.path` as a regular module import, which the hidden-`.pth` skip cannot disable. After a venv rebuild or a repo move, run `../bin/vault-relink-venv`. `bin/vault-doctor` flags the failure class (pointer exists but the directory does not). Full install: `../INSTALL.md`.

**macOS TCC note (future daemonization):** as a stdio subprocess of Claude Code, the server inherits Claude Code's disk access. If vault-librarian is ever promoted to a launchd daemon, the launcher needs Full Disk Access for writes under `~/Documents/`, or the vault store must move to a non-TCC-gated path.

## Run

```
.venv/bin/vault-librarian serve --config config.personal.yaml     # MCP server (stdio, blocks)
.venv/bin/vault-librarian reindex --config config.personal.yaml   # incremental reindex (JSON stats line)
.venv/bin/vault-librarian status --config config.personal.yaml    # index status (JSON line, no model load)
```

First search after a server start lazy-loads the embedding model (~10s); warm searches run in tens of milliseconds.

**MCP registration runs the real venv path, not the `.venv` symlink** — the symlink lives in iCloud-synced space and iCloud deleted it on 2026-06-14, breaking the server. Registered command:
`~/.venvs/vault-librarian/bin/vault-librarian serve --config <abs config>`.

## Phone channel

Capture from the phone by dropping a `.txt` (text or a lone URL) into iCloud Drive `VaultDrop/` (from the Files app, any Share → Save to Files, or the "Vault It" Shortcut once you build it). `bin/vault-phone-watcher` (stdlib-only) classifies each file and feeds it to `vault-capture --via phone`; spec: `../docs/superpowers/specs/2026-06-12-phone-capture-channel-design.md`.

**Two drain paths:**

1. **At `/ingest` (works always, no setup):** the `/ingest` command runs the watcher first, draining VaultDrop into the inbox before sweeping. Claude's session has the file access this needs.
2. **Auto-delivery via launchd (optional):** copy `launchd/vault-phone-watcher.plist.example`, fill in **your** absolute paths, then load it. It must run `/usr/bin/python3` (stdlib-only watcher). A launchd job writing under `~/Documents` needs Full Disk Access on that interpreter. See `../INSTALL.md` § 8.

Health: `_maintenance/phone-channel-stamp.json` (every drain stamps it); `/vault` and Scout flag pending/quarantined captures. **Do NOT repoint the plist interpreter at `.venv/bin/python`** — see the comment in the plist for why.

## File map

```
vault-librarian/
├── src/
│   ├── __init__.py
│   ├── server.py        # MCP server entry point
│   ├── embedder.py      # nomic-embed wrapper
│   ├── store.py         # sqlite-vec interface
│   └── tools.py         # search_wiki, search_sources, get_page
├── tests/
├── pyproject.toml
├── config.example.yaml
└── README.md            # you are here
```
