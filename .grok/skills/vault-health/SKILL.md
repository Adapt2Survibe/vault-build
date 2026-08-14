---
name: vault-health
description: >
  Diagnose and repair the personal vault's operating surface — MCP import
  death, stale ~/Documents/Dev paths, inbox/claim leftovers, volatile-lesson
  staleness, wiki graph (backlinks/orphans/broken links). Use when vault
  search is down, /recall returns nothing, librarian handshake fails, the operator
  asks "is the vault healthy", or a session starts against this repo.
---

# Vault health

Read-only diagnosis first. Mutators are two explicit commands.
`VAULT_ROOT` is this checkout (the directory that contains `AGENTS.md`).

## 1. Diagnose (always)

```
bin/vault-doctor
```

`--json` for a machine report. `--session-start` for the one-liner the SessionStart hook uses.

If `venv_src_paths` is FAIL and the detail is a missing `Documents/Dev/vault` path, that is the 2026-08-10 move outage. Go to step 2.

Also useful:

```
bin/vault-graph                  # human summary
bin/vault-graph --page <slug>    # what links here
bin/vault-graph --json           # full graph
```

## 2. Repair MCP (only when venv_src_paths failed)

```
bin/vault-relink-venv --dry-run
bin/vault-relink-venv
bin/vault-doctor
```

Then restart the Grok/Claude session so the MCP handshake reruns. Do not rebuild the venv unless relink + import still fail.

## 3. Inbox / claims

Pending files older than 24h → `/ingest` (or name the file). Leftover `.claimed-*`:

```
bin/vault-claim sweep --inbox vault-personal/inbox
```

## 4. Do not

- `pip install` / `uv pip` against the librarian venv as a first move (uv re-hides `.pth` files).
- Reindex as a health check (it loads the model). `vault-librarian status` is the cheap probe; doctor already runs it when the bin exists.
- Write `sources/` to "fix" anything.

## 5. Report shape

Lead with FAIL/ok. Then the one action that closes the highest-severity check. No tour of green checks.
