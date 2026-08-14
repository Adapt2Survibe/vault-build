---
description: Run Scout on demand against the personal vault. Walks the vault and writes the audit queue.
argument-hint: [--quick | --full]
allowed-tools: [Read, Glob, Grep, Bash, Agent]
---

# Audit

Trigger Scout on demand against the personal vault. Useful when you want to see vault health right now, or before running /synthesize. (Phase 1 is manual-only — there is no nightly cron yet; scheduled sweeps are Phase 3+, not built.)

## Mode
$ARGUMENTS

## Instructions

Resolve `VAULT_ROOT` first: this checkout, the directory that contains `AGENTS.md` and `bin/vault-capture`. Every `$VAULT_ROOT` path below is under that directory. Do not assume `~/Dev/vault` or any other home path.

1. **Determine mode:**
   - `--quick` (default if no argument) → broken citations + stub queue + infrastructure health. Fast, ~1 min.
   - `--full` → all six Scout checks including drift detection and dedup. Slower, ~5–10 min.

2. **Invoke Scout:**
   - Use the Agent tool to delegate to the `scout` sub-agent.
   - Pass the mode flag in the prompt.
   - Scout writes `$VAULT_ROOT/vault-personal/_maintenance/queue.json` (merge semantics — quick runs carry forward full-run findings for checks they skipped) and `$VAULT_ROOT/vault-personal/_maintenance/{YYYY-MM-DD}-audit.md`.

3. **Read the audit report Scout produced.**

4. **Show the user a condensed summary:**

```
Audit complete (quick mode). Full report at _maintenance/2026-04-28-audit.md

  Wiki pages: 47
  Sources: 23
  Issues: 4

  Broken citations:    1
  Stubs over 7 days:   3
  (skipped in quick mode: drift, orphans, dedup)

Surgeon queue: 4 items
Run /synthesize <path> to fix individually, or /synthesize --oldest 3 to batch.
(future: scheduled Scout/Surgeon sweeps — Phase 3+, not yet built)
```

5. **If issues found, ask:**
   - Want to run `/synthesize` on the worst offender now?
   - Want to escalate to `--full` audit?

6. **Log the dispatch:** append an entry for the Scout dispatch to `$VAULT_ROOT/agent-log.md`.

## Refuse to do

- Modify any vault files. Audit is detection only — that's Scout's whole contract.
- Skip writing the audit report. Even a clean run gets a dated audit file so we have a history of vault health.
