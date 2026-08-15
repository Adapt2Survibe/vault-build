# Ground operations

[Why this exists](README.md#the-physics) · [Flight rules](README.md#flight-rules) · [Session rules](AGENTS.md)

**Countdown:** [T-0](#t-0) · [T-1](#t-1) · [T-2](#t-2) · [T-3](#t-3) · [T-4](#t-4) · [T-5](#t-5) · [T-6](#t-6) · [T-7](#t-7) · [T-8](#t-8) · [Abort](#abort-modes)

Clone this repo wherever you want **except iCloud Drive**. On a Mac, that means not under `~/Documents`.

File-provider sync deletes symlinks and sets `UF_HIDDEN` on `.pth` files. The failure looks like this: `~/.venvs/vault-librarian/bin/vault-librarian` exists, MCP starts, then dies with `No module named 'vault_librarian'`. That is not a missing package. That is Python skipping a hidden `.pth`. The repair is [T-3](#t-3). Do not rebuild the venv as the first move.

`VAULT_ROOT` is this checkout — the directory that contains `AGENTS.md` and `bin/vault-capture`. Every command below assumes you have exported it, or that you are standing in that directory.

```bash
export VAULT_ROOT="$PWD"   # after cd into the clone
```

Each step has a **Verify**. If the verify fails, stop. The next step will not fix it.

## Prerequisites

| Need | Why | Check |
|---|---|---|
| Python 3.12 | librarian runtime | `python3.12 --version` |
| [uv](https://github.com/astral-sh/uv) | venv + install | `uv --version` |
| git, gh optional | clone / later publish | — |
| An agent host that can spawn subagents + MCP | the ingest loop (Scribe / Surgeon / Scout + librarian) | without that: capture + CLI search only. We ship wiring for Claude Code and Grok Build; they are not the only possible hosts. |
| Network, once | first search downloads nomic-embed | ~a few hundred MB |

macOS is the beaten path (`chflags`, TCC, iCloud landmines). Linux works if you skip the Mac-only lines. Windows is unproven.

---

<a id="t-0"></a>
## T-0  Data trees

The empty skeletons are already in the clone. Leave them empty. Do not copy someone else's `sources/` or `wiki/` into them.

**Verify:** `ls vault-personal/sources vault-personal/inbox vault-company/sources` — directories exist, no `.md` files yet.

---

<a id="t-1"></a>
## T-1  Config

```bash
cp vault-librarian/config.example.yaml vault-librarian/config.personal.yaml
```

`config.personal.yaml` is gitignored. The `./vault-personal` defaults are relative to **process cwd** when the server starts. If your MCP host does not start with `VAULT_ROOT` as cwd, make both paths **absolute**. Schema: [config.example.yaml](vault-librarian/config.example.yaml) · [librarian config](vault-librarian/README.md#config).

**Verify:** `test -f vault-librarian/config.personal.yaml && grep -E 'root:|path:' vault-librarian/config.personal.yaml`

---

<a id="t-2"></a>
## T-2  Venv — outside the repo, outside iCloud

```bash
uv venv --python 3.12 ~/.venvs/vault-librarian
ln -sfn ~/.venvs/vault-librarian "$VAULT_ROOT/vault-librarian/.venv"
```

The real venv is `~/.venvs/vault-librarian`. `.venv` in the repo is a cosmetic symlink so local commands look normal. **MCP registration uses the real path.** iCloud has deleted the symlink before.

**Verify:** `test -x ~/.venvs/vault-librarian/bin/python` and `readlink "$VAULT_ROOT/vault-librarian/.venv"` points at `~/.venvs/vault-librarian`.

---

<a id="t-3"></a>
## T-3  Install the package, then make the import unkillable

```bash
cd "$VAULT_ROOT/vault-librarian"
VIRTUAL_ENV="$HOME/.venvs/vault-librarian" uv pip install -e '.[dev]'
chflags -R nohidden ~/.venvs/vault-librarian    # macOS; skip on Linux
"$VAULT_ROOT/bin/vault-relink-venv"
```

`uv pip` re-flags files `UF_HIDDEN`. Python 3.12 `site.py` then skips the editable `.pth`. `vault-relink-venv` writes a `sitecustomize.py` that inserts `src/` onto `sys.path` as a normal import — the hidden-file skip cannot disable it.

Re-run relink after every `uv pip`, every venv rebuild, and every time you move this checkout.

**Verify:**

```bash
~/.venvs/vault-librarian/bin/python -c "import vault_librarian; print(vault_librarian.__file__)"
```

Must print a path **under this checkout**. If it prints a deleted tree or fails to import: relink, do not `pip install` again yet. Same class: [abort modes](#abort-modes).

---

<a id="t-4"></a>
## T-4  Prove the machine

```bash
"$VAULT_ROOT/bin/vault-doctor"
cd "$VAULT_ROOT/vault-librarian" && .venv/bin/python -m pytest -q -m 'not slow'
```

**Verify:** doctor is not FAIL on `venv_src_paths`. Pytest is green. Tests must not download the embedding model (`-m 'not slow'`).

`vault-doctor --session-start` is the one-liner a SessionStart hook can run. `--json` is for machines.

---

<a id="t-5"></a>
## T-5  Register MCP — the real binary, not the symlink

Any stdio MCP host (Claude Code: `~/.claude.json`):

```json
"vault-librarian": {
  "type": "stdio",
  "command": "/Users/YOU/.venvs/vault-librarian/bin/vault-librarian",
  "args": ["serve", "--config", "/ABS/PATH/TO/CHECKOUT/vault-librarian/config.personal.yaml"]
}
```

Restart the agent session. Handshake happens at session start, not on the next message.

**Verify:** the host lists tools [`search_wiki`, `search_sources`, `get_page`](vault-librarian/README.md#tools). If the server process dies on import, you skipped [T-3](#t-3)'s verify.

First live search downloads `nomic-ai/nomic-embed-text-v1.5` (~10s). After that, warm search is tens of milliseconds. Pin `embedding.revision` in config when you care about supply chain — the model repo uses `trust_remote_code`.

---

<a id="t-6"></a>
## T-6  Wire the staff

**Claude Code** — commands and agents are discovered from `~/.claude/`. Symlink, do not copy; edits in the repo take effect immediately.

```bash
mkdir -p ~/.claude/commands ~/.claude/agents
for f in recall ingest journal synthesize audit sources vault; do
  ln -sfn "$VAULT_ROOT/slash-commands/personal/${f}.md" ~/.claude/commands/${f}.md
done
ln -sfn "$VAULT_ROOT/agents/scribe.md"  ~/.claude/agents/scribe.md
ln -sfn "$VAULT_ROOT/agents/surgeon.md" ~/.claude/agents/surgeon.md
ln -sfn "$VAULT_ROOT/agents/scout.md"   ~/.claude/agents/scout.md
```

**Grok Build** — open this checkout as the workspace. `.grok/` is already in the repo (agents, SessionStart hook, `vault-health` skill). No extra copy step.

The prompts resolve `VAULT_ROOT` as this checkout. They do not assume `~/Dev/vault`. Session rules: [AGENTS.md](AGENTS.md). Agent files: [Scribe](agents/scribe.md) · [Surgeon](agents/surgeon.md) · [Scout](agents/scout.md).

**Verify:** `ls -l ~/.claude/commands/recall.md ~/.claude/agents/scribe.md` are symlinks **into this clone**. On Grok, a new session in this workspace sees the vault-health skill.

---

<a id="t-7"></a>
## T-7  Flight test

```bash
"$VAULT_ROOT/bin/vault-capture" "install smoke test" --tags test
```

Stdout is one path under `vault-personal/inbox/`. Then, in the agent session: `/ingest`. Scribe claims the file, writes `sources/` + a wiki stub, reindexes, archives the inbox file to `inbox/_processed/`.

If Scribe's reindex is skipped or fails, run it yourself:

```bash
~/.venvs/vault-librarian/bin/vault-librarian reindex \
  --config "$VAULT_ROOT/vault-librarian/config.personal.yaml"
"$VAULT_ROOT/bin/vault-doctor"
```

Then `/recall "install smoke test"`.

**Verify:**

| Check | Pass |
|---|---|
| Inbox file gone from `inbox/` (not `_processed`) | Scribe finished |
| A `sources/20*.md` exists | catalogue landed |
| A `wiki/**/*.md` stub exists | stub landed |
| `/recall` returns the smoke test | index sees the files |

Empty recall after a green ingest = index miss. Read Scribe's report. Reindex. Do not write `sources/` by hand to "fix" it. ([flight rule 1](README.md#flight-rules))

---

<a id="t-8"></a>
## T-8  Optional — phone drop (Mac + iCloud)

Spec: [`docs/superpowers/specs/2026-06-12-phone-capture-channel-design.md`](docs/superpowers/specs/2026-06-12-phone-capture-channel-design.md).

Drop a `.txt` (text or one URL) into iCloud Drive `VaultDrop/`. `/ingest` already runs `bin/vault-phone-watcher` first. Launchd is optional.

If you load launchd: copy `launchd/vault-phone-watcher.plist.example`, put **your** absolute paths in, grant Full Disk Access to `/usr/bin/python3` if the job writes under `~/Documents`. Interpreter is system Python **on purpose**. Do not point the plist at `.venv/bin/python`.

**Verify:** drop a file, run the watcher (or `/ingest`), confirm `vault-personal/_maintenance/phone-channel-stamp.json` has a fresh `ran_at` and the file is in the inbox.

---

## Abort modes

| Telemetry | Meaning | Abort / fix |
|---|---|---|
| `No module named 'vault_librarian'` | hidden `.pth` or sitecustomize pointing at a dead tree | [T-3](#t-3): relink, restart MCP. Not `pip install` first. |
| Doctor FAIL `venv_src_paths` | same class | [T-3](#t-3) then [T-4](#t-4) |
| `uv pip` then import dies again | uv re-hid `.pth` | [T-3](#t-3): `chflags` + relink |
| `/ingest` writes to a tree you do not recognize | `VAULT_ROOT` not this checkout | [T-6](#t-6) — run the session from the clone |
| First search hangs ~10s then works | model download | [T-5](#t-5). Pin `revision` later |
| First search hangs then fails | no network / disk | [T-5](#t-5) — HuggingFace reachability |
| Recall empty, files on disk | index stale or MCP down | [T-7](#t-7): `status`, then `reindex` |
| Two `/ingest`s fighting | you skipped claim | [T-7](#t-7): `bin/vault-claim sweep --inbox vault-personal/inbox` |

When healthy, doctor is quiet. A noisy health check gets filtered. That is how silent failure starts.

---

[README](README.md) · [Session rules](AGENTS.md) · [Librarian](vault-librarian/README.md) · [Contracts](vault-librarian/CONTRACTS.md)
