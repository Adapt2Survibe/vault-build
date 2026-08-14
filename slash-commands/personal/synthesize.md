---
description: Synthesize a stub wiki page or rewrite a drifted one in the personal vault. Manual Surgeon trigger.
argument-hint: <wiki-page-path-or-id | --oldest N>
allowed-tools: [Read, Write, Edit, Glob, Grep, Agent]
---

# Synthesize

Manually invoke Surgeon on a single wiki page in the personal vault. This is the ONLY way pages get synthesized — Phase 1 has no automatic or scheduled synthesis (that's deferred to Phase 3+). Run it whenever a stub is ready to graduate or you're actively working on a topic.

## Target
$ARGUMENTS

## Instructions

Resolve `VAULT_ROOT` first: this checkout, the directory that contains `AGENTS.md` and `bin/vault-capture`. Every `$VAULT_ROOT` path below is under that directory. Do not assume `~/Dev/vault` or any other home path.

1. **Resolve the target:**
   - If `$ARGUMENTS` looks like a path (`wiki/tools/example-tool.md`) → use it directly.
   - If `$ARGUMENTS` looks like an ID (`wiki-example-tool`) → Glob for the matching page.
   - If `$ARGUMENTS` is just a slug (`example-tool`) → Glob `$VAULT_ROOT/vault-personal/wiki/**/example-tool.md`.
   - If `$ARGUMENTS` is `--oldest N` → batch mode: grep wiki for `needs-synthesis` stubs, sort by `created` ascending, take the N oldest, and run steps 2–6 for each in turn (one Surgeon dispatch per page — Surgeon's contract is one page per dispatch).
   - If no match, list nearby candidates and ask which one.

2. **Read the page and its sources:**
   - Read the wiki page in full.
   - Read every source listed in the page's `sources:` front matter.
   - If sources are missing from `$VAULT_ROOT/vault-personal/sources/`, abort and report — Surgeon will not synthesize from missing sources.

3. **Determine surgery type:**
   - Page has `needs-synthesis` tag → **graduation**.
   - Page lacks the tag but `last_synthesized` is older than 30 days → **drift check + possible rewrite**.
   - Page is current → ask the user what they want to revise.

4. **Invoke Surgeon:**
   - Use the Agent tool to delegate to the `surgeon` sub-agent.
   - Pass the page path and surgery type as the prompt.

5. **Verify the backup, then show the diff:**
   - Confirm the backup file Surgeon reported actually exists, is non-empty, and was created during THIS run (mtime after the dispatch). A missing or stale backup means the surgery is UNACCEPTED: surface it loudly and stop — do not present the new page as done. The backup is the only rollback for gitignored vault data.
   - Then display a unified diff of before/after (`diff -u` of the backup against the rewritten page).

6. **Report:**

```
Synthesized: wiki/tools/example-tool.md
  Surgery type: graduation (was stub, now full entry)
  Tags: removed [needs-synthesis], added [example-tag-a, example-tag-b]
  Sources used: 2
  Citations added: 6
  last_synthesized: 2026-01-20
  Backup: _maintenance/backups/2026-01-20-143055-example-tool.md

Review the diff above. Undo: copy the backup back over the page.
```

7. **Log the dispatch(es):** append an entry per Surgeon dispatch to `$VAULT_ROOT/agent-log.md`.

## Refuse to do

- Synthesize a page that has zero sources. The vault's invariant is "every claim cites a source." A page with no sources can't be synthesized — it can only be deleted or hand-written with sources added first.
- Accept surgery without a backup. If Surgeon's report shows no backup path, restore the situation and re-run — the backup is the only undo for gitignored vault data.
- Edit `/sources/`. Sources are immutable.
