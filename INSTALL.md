# Install

Clone this repo wherever you want. Do **not** put the checkout or the Python venv inside iCloud Drive (`~/Documents` on a Mac). File-provider sync deletes symlinks and hides `.pth` files; that is how the librarian dies with `No module named 'vault_librarian'` while the binary still exists.

`VAULT_ROOT` means this checkout — the directory that contains `AGENTS.md`.

## 1. Empty data trees

The skeletons are already here (`vault-personal/`, `vault-company/`). Leave them. Do not copy someone else's `sources/` or `wiki/` into them.

## 2. Librarian config

```bash
cp vault-librarian/config.example.yaml vault-librarian/config.personal.yaml
```

`config.personal.yaml` is gitignored. Point `vault.root` and `store.path` at **this** checkout's `vault-personal/` if the `./` defaults are not where you cloned.

## 3. Venv (outside the repo)

Needs Python 3.12 and [uv](https://github.com/astral-sh/uv).

```bash
cd vault-librarian
uv venv --python 3.12 ~/.venvs/vault-librarian
ln -s ~/.venvs/vault-librarian .venv
VIRTUAL_ENV="$HOME/.venvs/vault-librarian" uv pip install -e '.[dev]'
chflags -R nohidden ~/.venvs/vault-librarian   # macOS: uv marks .pth files hidden
../bin/vault-relink-venv
```

**Why `chflags` + relink:** uv sets `UF_HIDDEN` on venv files; Python 3.12's `site.py` then skips the editable `.pth`, so `import vault_librarian` fails silently. `vault-relink-venv` writes a `sitecustomize.py` that puts `src/` on `sys.path` without going through that skip. Re-run relink after any `uv pip` or after you move the checkout.

## 4. Prove the install

```bash
../bin/vault-doctor
cd vault-librarian && .venv/bin/python -m pytest -q -m 'not slow'
```

Doctor should not be FAIL on `venv_src_paths`. Tests should pass without downloading the embedding model.

## 5. Register MCP

Register the **real venv binary**, not the `.venv` symlink (iCloud has deleted that symlink before).

Claude Code (`~/.claude.json`) / any stdio MCP host:

```json
"vault-librarian": {
  "type": "stdio",
  "command": "/Users/YOU/.venvs/vault-librarian/bin/vault-librarian",
  "args": ["serve", "--config", "/ABS/PATH/TO/CHECKOUT/vault-librarian/config.personal.yaml"]
}
```

Restart the agent session so the handshake reruns.

First search downloads `nomic-ai/nomic-embed-text-v1.5` (~a few hundred MB, ~10s). After that, warm search is tens of milliseconds. Pin `embedding.revision` in config once you care about supply-chain; the model repo uses `trust_remote_code`.

## 6. Slash commands and agents

**Claude Code**

```bash
mkdir -p ~/.claude/commands ~/.claude/agents
for f in recall ingest journal synthesize audit sources vault; do
  ln -sf "$VAULT_ROOT/slash-commands/personal/${f}.md" ~/.claude/commands/${f}.md
done
ln -sf "$VAULT_ROOT/agents/scribe.md"   ~/.claude/agents/scribe.md
ln -sf "$VAULT_ROOT/agents/surgeon.md"  ~/.claude/agents/surgeon.md
ln -sf "$VAULT_ROOT/agents/scout.md"    ~/.claude/agents/scout.md
```

**Grok Build** already reads `.grok/` from the project. Open this checkout as the workspace.

The prompts resolve `$VAULT_ROOT` as this checkout. They do not assume `~/Dev/vault`.

## 7. First loop

```bash
bin/vault-capture "install smoke test" --tags test
```

Then `/ingest` (spawns Scribe). Then:

```bash
vault-librarian/.venv/bin/vault-librarian reindex --config vault-librarian/config.personal.yaml
bin/vault-doctor
```

Then `/recall "install smoke test"`. If recall is empty, the index did not see the new files — check Scribe's report and reindex.

You need an agent harness that can spawn subagents (Claude Code or Grok Build). Without that you still have capture + search CLI; you do not have the ingest loop.

## 8. Optional: phone drop folder

Mac + iCloud only. Spec: `docs/superpowers/specs/2026-06-12-phone-capture-channel-design.md`.

Copy `launchd/vault-phone-watcher.plist.example`, put **your** absolute paths in, then decide whether to load it. `/ingest` already drains `~/Library/Mobile Documents/com~apple~CloudDocs/VaultDrop/` without launchd. A launchd job writing under `~/Documents` needs Full Disk Access on `/usr/bin/python3`. Do not point the plist at `.venv/bin/python`.

## Landmines

| Symptom | Cause | Fix |
|---|---|---|
| `No module named 'vault_librarian'` | hidden `.pth` or stale sitecustomize after a move | `bin/vault-relink-venv` then restart MCP |
| Doctor FAIL `venv_src_paths` | venv still points at an old checkout | same |
| `/ingest` looks in the wrong tree | command did not resolve `VAULT_ROOT` | run the session from this checkout |
| First search hangs ~10s | model download | wait once; pin `revision` later |
| `uv pip install` and imports die again | uv re-hid `.pth` | `chflags -R nohidden` + relink |
