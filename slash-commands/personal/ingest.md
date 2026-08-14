---
description: Ingest a file, URL, or pasted text into the personal vault as a new source
argument-hint: <path|url|->
allowed-tools: [Read, Write, Bash, WebFetch, Agent]
---

# Ingest

Pull something into the personal vault as a new source. Triggers Scribe synchronously.

## Input
$ARGUMENTS

## Instructions

Resolve `VAULT_ROOT` first: this checkout, the directory that contains `AGENTS.md` and `bin/vault-capture`. Every `$VAULT_ROOT` path below is under that directory. Do not assume `~/Dev/vault` or any other home path.

0. **Drain the phone channel first (always, before anything else).** Phone captures land in the iCloud drop folder `~/Library/Mobile Documents/com~apple~CloudDocs/VaultDrop/`. A launchd watcher *tries* to auto-drain them, but under launchd it is TCC-blocked from `~/Documents` until Full Disk Access is granted (see `vault-librarian/README.md` § Phone channel). You (Claude, in an interactive session) DO have access, so drain it here every time:
   ```bash
   /usr/bin/python3 $VAULT_ROOT/bin/vault-phone-watcher
   ```
   This moves any phone captures into the inbox (front matter `via: phone`) and updates `_maintenance/phone-channel-stamp.json`. Parse nothing from it; just run it, then continue — the drained captures are now ordinary inbox files the steps below will sweep. If it exits non-zero, report the stderr line but continue (a stuck phone file must not block ingesting everything else).

1. **Determine input type:**
   - If `$ARGUMENTS` is a path that exists → stage the file.
   - If `$ARGUMENTS` starts with `http://` or `https://` → stage the URL.
   - If `$ARGUMENTS` is `-` → ask the user to paste content directly.
   - If `$ARGUMENTS` is empty → **sweep mode:** glob `$VAULT_ROOT/vault-personal/inbox/*` (excluding `_processed/`). If pending files exist, list them and dispatch Scribe on the whole pending set. If the inbox is empty, ask the user what to ingest.
   - If `$ARGUMENTS` is text that doesn't match the above → treat as the source content itself.

2. **Stage in inbox** (skip in sweep mode — items are already staged):
   - Preferred staging path for everything: `$VAULT_ROOT/bin/vault-capture <input>` via Bash — it handles files, URLs, text, and stdin with consistent naming and front matter, and prints the inbox path it created.
   - Fallback (if the script is unavailable): copy/save to `$VAULT_ROOT/vault-personal/inbox/{YYYY-MM-DD-HHMMSS}-{slug}.md`, preserving a URL as a `url:` front-matter field.

2.5 **Lesson gate (deterministic — before Scribe).** If any staged/pending inbox file has front-matter `via: lesson-capture`, run the form linter on exactly those files via Bash:
   ```bash
   $VAULT_ROOT/bin/lesson-lint <those inbox files>
   ```
   - Exit 0 → proceed. Relay any `FLAG` lines verbatim (advisory — never blocks).
   - Nonzero → the `FAIL`ing files do NOT go to Scribe this pass: show the FAIL lines, keep those files in the inbox, and continue ingesting the rest. Offer to fix the failing note (inbox notes are pre-ingest staging, so editing them WITH the user's ok is fine — the immutability contract starts at `sources/`); the format spec is `$VAULT_ROOT/docs/lesson-schema.md`. Never silently edit a note to make it pass.
   - **If `lesson-lint` itself is missing or unrunnable** (broken symlink, iCloud-evicted file, Python error instead of per-file FAIL lines) → treat ALL lesson-capture files as failed this pass: hold them in the inbox, report the error verbatim, and continue with the non-lesson files only. A broken gate must not become a silent bypass. Cross-check the linter's closing counts line: the number linted must equal the number of lesson files you passed it.

2.6 **Claim each file before dispatching Scribe** (makes concurrent sweeps safe):
   ```bash
   $VAULT_ROOT/bin/vault-claim claim <abs-path>
   ```
   Success (exit 0) → stdout is the `.claimed-<pid>-<name>` path; pass THAT path to Scribe, not the original. Nonzero → another session already claimed it; skip that file and say so. After a crashed Scribe, run `$VAULT_ROOT/bin/vault-claim sweep --inbox $VAULT_ROOT/vault-personal/inbox` to restore stranded claims whose pid is dead.

3. **Invoke Scribe:**
   - Use the Agent tool to delegate to the `scribe` sub-agent.
   - Pass the staged inbox file path(s). Single ingest: exactly one path. Sweep mode: the full pending list, and say "sweep" in the prompt.
   - Pass through any `--slug <name>` or `--section <section>` the user gave — Scribe honors operator overrides over its own derivation.
   - Concurrent sweeps are now mechanically safe via step 2.6 (claim-by-rename). Still don't dispatch two Scribes on the same claimed file from this session.

4. **Report:**

```
Ingested:
  Source:  src-{YYYY-MM-DD}-{slug} → /sources/{YYYY-MM-DD}-{slug}.md
  Stub:    /wiki/{section}/{slug}.md (tagged needs-synthesis)

Open the stub to refine, or wait for Surgeon to graduate it.
```

(Sweep mode: one block per file plus a totals line, mirroring Scribe's report.)

5. **If anything fails** (file unreadable, URL fetch fails, slug collision Scribe couldn't resolve), report clearly and leave `/inbox` in a clean state.

6. **Log the dispatch:** append an entry for the Scribe dispatch to `$VAULT_ROOT/agent-log.md` if you keep a dispatch log.

## Refuse to do

- Skip Scribe and write directly to `/sources` or `/wiki`. The whole point of the pipeline is consistency — stubs only at this stage; `/synthesize` is the synthesis command.
