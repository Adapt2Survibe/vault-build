# vault-librarian

Local MCP search over a vault's wiki and sources. stdio. No remote API.

The files are the source of truth. This process is the index. If it dies, the vault is still there — rebuild the index.

**Install the factory:** [../INSTALL.md](../INSTALL.md)  
**Module boundaries:** [CONTRACTS.md](CONTRACTS.md)

## Tools

| Tool | Returns |
|---|---|
| `search_wiki(query, top_k=5)` | synthesized wiki chunks, with page ids |
| `search_sources(query, top_k=3)` | raw passages, excerpts hard-capped at 15 words |
| `get_page(page_id)` | full markdown of a wiki page or source |

The 15-word source excerpt is a vault contract, not a UI preference. User-facing answers do not quote sources longer than that.

## Stack

| Piece | Choice | Why |
|---|---|---|
| Embeddings | `nomic-ai/nomic-embed-text-v1.5`, 768-d, local | no vendor lock; `trust_remote_code` — pin `embedding.revision` |
| Store | sqlite-vec | one file next to the vault; delete it and reindex |
| Transport | FastMCP stdio | child of the agent host; inherits that process's disk access |
| Device | `cpu` default; `mps` / `cuda` optional | first search lazy-loads (~10s); warm is tens of ms |

A dimension change on an existing `index.sqlite` fails **at open**, not as a confusing per-document upsert. Delete the db and reindex.

## Run

Register the **real venv binary**, not `vault-librarian/.venv` (that symlink is disposable and has been deleted by iCloud).

```
~/.venvs/vault-librarian/bin/vault-librarian serve  --config config.personal.yaml
~/.venvs/vault-librarian/bin/vault-librarian reindex --config config.personal.yaml
~/.venvs/vault-librarian/bin/vault-librarian status  --config config.personal.yaml
```

`status` does not load the model. Do not reindex to "check health" — that is what `status` and `../bin/vault-doctor` are for.

Two vaults (personal + company) are two configs and **two MCP registration names**. Reusing `vault-librarian` for both collapses the isolation the directory split exists to enforce.

## Config

Copy `config.example.yaml` → `config.personal.yaml` (gitignored). Required sections: `vault`, `embedding`, `store`. Paths accept `~` and, if the server's cwd is the checkout, `./vault-personal`.

## Modules

```
src/vault_librarian/
  config.py     YAML + pydantic
  chunker.py    front matter, 512-token chunks / 64 overlap
  embedder.py   nomic wrapper, lazy load
  store.py      sqlite-vec, one vector table per kind
  indexer.py    incremental reindex
  tools.py      the three MCP tools
  server.py     FastMCP stdio
  cli.py        serve / reindex / status
```

Tests live 1:1 under `tests/`. `conftest.py` imports nothing from the package so collection survives a half-built tree. Never load the real model in unit tests (`-m 'not slow'`).

## Phone drain

Not this process's job. `../bin/vault-phone-watcher` is stdlib. `/ingest` runs it. Launchd is optional — see INSTALL T-8. Interpreter is `/usr/bin/python3`, never `.venv/bin/python`.
