---
description: Show personal vault status — counts, recent activity, pending work, health
argument-hint: (none)
allowed-tools: [Read, Glob, Bash]
---

# Vault Status

`git status` for the personal vault. Run this when you want a one-screen health check.

## Instructions

Resolve `VAULT_ROOT` first: this checkout, the directory that contains `AGENTS.md` and `bin/vault-capture`. Every `$VAULT_ROOT` path below is under that directory. Do not assume `~/Dev/vault` or any other home path.

0. **Run the deterministic doctor first** (do not re-derive counts by hand):
   ```bash
   $VAULT_ROOT/bin/vault-doctor
   ```
   `--session-start` is the one-liner. `--json` if you need the machine report. Wiki backlinks / orphans / broken links: `$VAULT_ROOT/bin/vault-graph`. Then fill only the narrative the scripts don't cover (recent activity, suggested next actions).

1. **Counts:**
   - Wiki pages: glob `$VAULT_ROOT/vault-personal/wiki/**/*.md` excluding `_index.md`
   - Sources: glob `$VAULT_ROOT/vault-personal/sources/*.md` excluding `_index.md`
   - Journal entries: glob `$VAULT_ROOT/vault-personal/journal/*.md` excluding `_index.md`
   - Inbox pending: glob `$VAULT_ROOT/vault-personal/inbox/*` excluding `_processed/` and `README.md`

2. **Pending work:**
   - Stubs with `needs-synthesis` tag: grep across `$VAULT_ROOT/vault-personal/wiki/`
   - Stubs older than 7 days: same grep + parse front matter for creation date
   - Pending merge proposals: glob `$VAULT_ROOT/vault-personal/_maintenance/proposals/*.md` with status: pending

3. **Recent activity (last 7 days):**
   - Newest sources
   - Newest wiki pages
   - Newest journal entries
   - Last Scout run (most recent `_maintenance/*-audit.md`)
   - Last Surgeon run (newest `last_synthesized` date across wiki front matter — Surgeon writes no report file)

4. **Health flags:**
   - If last Scout run > 3 days ago → flag "Scout overdue"
   - If `inbox/` has files older than 24 hours → flag "Scribe behind"
   - If `_maintenance/queue.json` exists with unprocessed items → flag "Surgeon work pending" AND inline the top 2 findings (highest severity first) directly in the display — the queue file is a source, not an alarm; this status view is the surface the operator actually reads
   - **Phone channel** (drop folder: `~/Library/Mobile Documents/com~apple~CloudDocs/VaultDrop/`). Captures land here from the phone; they reach the vault when drained (auto by the launchd watcher IF Full Disk Access is granted, otherwise at the next `/ingest`, which drains it). Flags:
     - Any non-hidden file OR `.icloud` placeholder in the drop folder → flag "N phone captures pending — run /ingest to drain" (this is the signal that matters: undrained captures). Empty drop folder = nothing pending, healthy.
     - Read `_maintenance/phone-channel-stamp.json`. `ok: false` → flag with the stamp's `reason`. A stamp whose `next_run_by` is in the past only matters when the drop folder also has files (otherwise the daemon being idle/TCC-blocked is harmless — `/ingest` covers draining).
     - `VaultDrop/.failed/` non-empty → flag "quarantined phone captures awaiting review"

5. **Display:**

```
Vault status — 2026-04-28T14:22

Counts
  Wiki pages:        47
  Sources:           23
  Journal entries:   89
  Inbox pending:      2

Recent activity (7 days)
  New sources:       3
  New wiki pages:    5
  Journal entries:   6
  Last Scout:        2026-04-28 (today, 03:00)
  Last Surgeon:      2026-04-26 (last manual /synthesize)

Pending work
  needs-synthesis stubs:        4
    └─ over 7 days old:          1   ⚠
  Merge proposals (review):     1
  Surgeon queue:                 0

Health
  ✓ Scout ran recently
  ⚠ Scribe behind (2 inbox files > 24h old)
  ✓ No drift detected last run

Suggested next actions:
  • Run /ingest to clear inbox
  • Review merge proposal: _maintenance/proposals/2026-01-18-example-merge.md
  • Synthesize old stub: wiki/people/example-person.md (13 days as stub)
```

## Refuse to do

- Modify anything. This is a read-only summary.
- Run Scout/Surgeon as a side effect. Use `/audit` and `/synthesize` for that.
