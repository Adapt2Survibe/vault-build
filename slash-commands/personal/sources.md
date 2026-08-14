---
description: List, inspect, or open source documents in the personal vault
argument-hint: [list | <source-id> | <search-query>]
allowed-tools: [Read, Glob, Grep, mcp__vault-librarian__search_sources, mcp__vault-librarian__get_page]
---

# Sources

Browse the personal vault's canonical source layer. Read-only.

## Input
$ARGUMENTS

## Instructions

Resolve `VAULT_ROOT` first: this checkout, the directory that contains `AGENTS.md` and `bin/vault-capture`. Every `$VAULT_ROOT` path below is under that directory. Do not assume `~/Dev/vault` or any other home path.

1. **Determine intent:**
   - `list` or empty → list all sources, sorted by ingestion date descending.
   - Looks like a source ID (`src-2026-01-15-example-note`) → fetch and display that specific source.
   - Anything else → treat as a search query, use the `mcp__vault-librarian__search_sources` tool.

### List mode

Read `$VAULT_ROOT/vault-personal/sources/_index.md` if it has a manifest, otherwise glob `$VAULT_ROOT/vault-personal/sources/*.md` and parse front matter.

Display:

```
Sources in vault: 23

By type:
  website:     8
  pdf:         5
  transcript:  6
  article:     3
  export:      1

Recent (last 30 days):
  src-2026-01-15-example-note      Example note — a captured thought     2026-01-15
  src-2026-01-12-example-article   Example article — a saved web page    2026-01-12
  src-2026-01-08-example-call      Example transcript — a recorded call  2026-01-08
  ...
  (illustrative placeholders — real listings show your actual sources)

Show all (--all) | Filter by type (--type=transcript) | Search (just type a query)
```

### Inspect mode (specific source ID)

Display:

```
src-2026-01-12-example-article

  Type:           website
  Ingested:       2026-01-12
  Original URL:   https://example.com/some-article
  Title:          Example article — a saved web page
  Cited by:       2 wiki pages
                    - wiki/tools/example-tool.md
                    - wiki/concepts/example-concept.md
  Anchors:        #intro, #details, #summary

  --- content (first 30 lines) ---
  {first 30 lines of the source body}
  ---

  Open full file: $VAULT_ROOT/vault-personal/sources/2026-01-12-example-article.md
```

To find "cited by" pages, Grep `$VAULT_ROOT/vault-personal/wiki/` for the source ID.

### Search mode

Use the `mcp__vault-librarian__search_sources` tool with the query, top_k=5.

Display:

```
Search: "example topic"

Top matches:
  1. src-2026-01-12-example-article  (score 0.91)
     "...a short paraphrased excerpt from the matching source..."
     #details

  2. src-2026-01-08-example-call  (score 0.78)
     "...another paraphrased excerpt from a different source..."

  3. ...

  (illustrative placeholders — real searches show your actual matches)

Inspect a result: /sources <source-id>
```

## Refuse to do

- Edit sources. They are immutable once ingested. Corrections live in the wiki entry that cites them.
- Delete sources. If you genuinely need to retire a source, do it by hand and note it in today's journal entry — vault data is gitignored, so there is no git history to fall back on.
- Quote source content beyond what's needed for inspection. Even in search results, paraphrase or show short excerpts only.
