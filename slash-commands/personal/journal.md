---
description: Open or create today's journal entry in the personal vault
argument-hint: [optional first thought]
allowed-tools: [Read, Write, Bash]
---

# Journal

Open or create today's journal entry in the personal vault. If `$ARGUMENTS` has content, append it as the first note.

## Initial thought (optional)
$ARGUMENTS

## Instructions

Resolve `VAULT_ROOT` first: this checkout, the directory that contains `AGENTS.md` and `bin/vault-capture`. Every `$VAULT_ROOT` path below is under that directory. Do not assume `~/Dev/vault` or any other home path.

1. **Resolve today's date** in the operator's local timezone. Format: `YYYY-MM-DD`.

2. **Check for existing entry** at `$VAULT_ROOT/vault-personal/journal/{YYYY-MM-DD}.md`.

3. **If it exists:**
   - Read it.
   - If `$ARGUMENTS` has content, append under the `## Notes` section as a new bullet with a timestamp:
     ```
     - **HH:MM** — {the thought}
     ```
   - Show the user the current state of today's entry.

4. **If it doesn't exist:**
   - Create it with this template:
     ```markdown
     ---
     title: Journal {YYYY-MM-DD}
     date: {YYYY-MM-DD}
     ---

     # {YYYY-MM-DD}

     ## Notes

     {- **HH:MM** — first thought, if $ARGUMENTS provided}

     ## Decisions

     ## Tomorrow
     ```
   - Show the user the new file.

5. **Surface relevant context (optional, only if useful):**
   - If yesterday's `## Tomorrow` section had items, surface them at the top of today's response:
     ```
     From yesterday's "Tomorrow":
       - {item 1}
       - {item 2}
     ```
   - Do not auto-copy them into today's entry. Show them and let the user decide.

6. **Reminder:**
   - If today is Sunday and there are no entries from earlier in the week, gently suggest a weekly review.
   - If the user has multiple `needs-synthesis` stubs piling up (check `$VAULT_ROOT/vault-personal/_maintenance/` for recent audit reports), mention the count.

## Refuse to do

- Modify journal entries from previous days. Past journal entries are append-only mentally — leave them alone.
- Auto-promote journal content to wiki. That's Surgeon's job, on review.
