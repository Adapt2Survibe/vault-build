# Vault Lesson Schema

**The single source of truth for how a cross-project engineering lesson is captured.** Every capture path — a lesson-capture rule in the operator's agent instructions, a session closeout sweep, and a hand-run `vault-capture` — MUST produce lessons in this shape and reference THIS file rather than re-describing the format (re-descriptions drift). The machine-checkable half of this schema is enforced deterministically by `bin/lesson-lint` (§ Enforcement); the judgment half is the AI's.

## When something is a lesson (the filter — all three must hold) — JUDGMENT, not linted

- **Cross-project** — it would help in a *different* project, not just where you learned it.
- **Non-obvious / cost real time** — it bit you, or is easy to forget and expensive to rediscover.
- **Durable enough to snapshot** — a settled fact / decision / pattern, not a moving target.

If all three don't hold, it's noise. The vault rots if flooded. No script can check these — they are the AI's / your call.

## The boundary (does it even go in the vault?) — JUDGMENT, not linted

- Recall trigger is a **technical symptom** → **vault lesson** (this schema).
- "How the AI should act" (a **behavioral prescription**) → the operator's agent rules file.
- How the operator **reasons** (a cognitive lens) → a personal mental-models file, not this repo.

Test: *"the same insight is a vault lesson if I'd reach for it facing a technical symptom, a CLAUDE.md rule if it prescribes behavior."*

## Shape of a lesson note

**Front matter:**
```yaml
title: <symptom-first — the way you'd ASK it later>   # aim: "launchd job runs but does nothing", not "watcher plist fix"
type_hint: note
via: lesson-capture                                    # the exact trigger token; lesson-lint only lints notes with this
tags: [<topic tags>, <exactly one of: durable | volatile>]
```

**Body** — a short prose lesson. Include, in whatever order reads well:
- One-line **essence**.
- **Symptom / Tell:** the diagnostic signal (symptom → cause).
- **Fix:** the concrete action.
- **Why:** the mechanism — so it transfers to cousin problems, and survives even if the specific fix changes.
- **Scope:** where it applies (OS / tool / version), so recall in the wrong context doesn't mislead. **This is the one body label that is always required** (it's the anti-mis-recall guard); the others are strongly recommended and flagged-if-absent, not rejected.
- **Volatility stamp (required — three lines):**
  - `Volatility:` starts with `durable` or `volatile` (free text may follow, e.g. `VOLATILE — obsolete when the bug is fixed`). Case-insensitive.
  - `As of:` contains a `YYYY-MM-DD` date; for `volatile` lessons it should also carry the version it's pinned to (e.g. `Claude Code 2.1.175`, or `~2.1.x`).
  - `Verify:` the ≤30-second recheck — or `n/a` for timeless durable facts.

**Parse tolerances (important — the linter honors these, so write naturally):** stamp values may be UPPERCASE, carry a trailing period, and hold free text after the keyword. `As of:` values carry free-form version text alongside the date. The linter EXTRACTS the date and the leading keyword; it never does an exact-string or whole-line date parse.

## How to capture a lesson (the one procedure — every path cites this, none restates it)

1. Author the note per § Shape (symptom-first title; Tell/Fix/Why/Scope; volatility stamp) and capture it:
   ```bash
   $VAULT_ROOT/bin/vault-capture - --via lesson-capture \
     --title "<symptom-first title>" --tags "<topics>,<durable|volatile>"
   ```
   with the body on stdin (the Tell/Fix/Why/Scope prose + the `Volatility:` / `As of:` / `Verify:` lines — version-pinned when volatile).
2. Run `$VAULT_ROOT/bin/lesson-lint` on the created inbox file(s); fix any FAIL before moving on (inbox files are pre-ingest staging — editable). Check the closing counts line: N captured must equal N linted.
3. Leave ingestion to `/ingest` (run it or offer to) — **never write `sources/` directly**; the routing invariant is inbox → `/ingest` → Scribe, and the gate re-runs there.

## Volatility rules

- **durable** — OS / unix / git / concurrency / math truths, or a decision's *why*. Stamp the date; `Verify: n/a` is fine.
- **volatile** — version-pinned platform behavior (CC flags, guards) or an open bug. Stamp date **+ version**, and give a real `Verify`. If it's a bug, say `obsolete when fixed` so the entry can be retired once the bug is gone.
- **The `durable`/`volatile` tag must match the leading word of the `Volatility:` value** (both durable or both volatile). This is a hard, linted consistency check.

## Enforcement — what keeps this consistent

Two layers, in the right order (deterministic first, judgment second):

**1. `bin/lesson-lint` — the deterministic FORM gate (real script committed in the repo at `bin/lesson-lint`).** Stdlib-only (it does NOT import the vault-librarian venv — the corpus itself documents that venv breaking). It runs only on notes whose front-matter `via` is exactly `lesson-capture`; every other note is a no-op pass (reference/website captures are never flagged for a missing stamp). It **rejects-and-flags; it never normalizes** (normalizing a body would be editing a source — forbidden). Its checks:

  *Hard (a real problem — reported as FAIL):*
  1. `via == lesson-capture` (the trigger gate; else no-op pass).
  2. `tags` contains exactly one of `durable` | `volatile`.
  3. the `durable`/`volatile` tag matches the leading keyword of the `Volatility:` value.
  4. `Volatility:` / `As of:` / `Verify:` lines all present.
  5. `Volatility:` value begins with `durable` or `volatile` (case-insensitive; free text after is fine).
  6. `As of:` contains an extractable `YYYY-MM-DD` that parses as a real date.
  7. `Scope:` present in the body.

  *Advisory (worth knowing — reported as FLAG, never blocks):*
  - title looks solution-first (ends in fix/patch/workaround/solution, or is a verbless slug, or <4 words) — a weak signal that the title isn't symptom-first (which is genuinely a judgment call).
  - body is missing a `Fix:` or a `Symptom:`/`Tell:` diagnostic label.
  - `volatile` lesson whose `As of:` carries only a bare date (no version token).
  - `volatile` lesson with `Verify: n/a` (volatile facts should carry a real recheck).
  - **batch-scoped:** two or more notes in the SAME lint invocation share a *substantive* `Verify:` line that is identical after normalization (case-folded, whitespace-collapsed, trailing periods/spaces stripped) (`n/a` variants excluded — they are sanctioned and repeat legitimately). Copy-paste is the mechanism that produces a recipe belonging to a different lesson; the gate cannot judge whether a recipe is TRUE of its note (that is a Law of a form gate, not a gap), but equality-after-normalization across a batch it can compute. **Limits, by design:** a single file linted alone has nothing to compare against, and a duplicate of an ALREADY-INGESTED note is invisible — closing either would mean reading `sources/`, which this stdlib-only gate deliberately does not do. Added 2026-08-07 after three captures written in the same second shipped one shared `Verify:` line that was correct for one note and factually wrong for another; live-corpus validated at 126 notes → 3 FLAGs, zero false positives.

**2. The AI (Scribe + the capture path) — the JUDGMENT layer.** Whether it's genuinely a lesson (the filter), the vault-vs-CLAUDE.md boundary, and whether the durable/volatile call and the symptom-first framing are *right* — none of that is machine-checkable; it's the AI's call at capture time.

**Where the gate runs:** every high-volume lesson path (`/closeout` sweep, `/mine-lessons`) MUST terminate at `inbox → /ingest → Scribe` — no skill writes `sources/` directly, or the gate never runs. On a lesson capture, `lesson-lint` runs at ingest; a hard FAIL leaves the inbox file in place and is reported (mirroring Scribe's fetch-fail path), never silently written. A periodic Scout re-lint over `sources/` is the mechanical backstop so the gate isn't solely AI-honored.

> **Build status:** `bin/lesson-lint` and its Scribe/ingest wiring are live. A Scout re-lint of already-ingested lessons is not in this snapshot, so the `sources/` backstop is not yet mechanical.

## Staleness (volatile lessons)

- **verify-on-recall (the everyday leg):** requires `/recall` to surface the `Volatility:` / `As of:` / `Verify:` stamp when it returns a volatile source, with a "re-verify" nudge. *(Live as of 2026-07-12 — `/recall` step 3.5.)*
- **Scout / doctor staleness check (the automated backstop):** `bin/vault-doctor` flags volatile lessons whose `As of:` date is older than `--stale-days` (default 90). Comparing the `As of:` version token against the current tool version is not in this snapshot.
- Nothing yet auto-retires an `obsolete when fixed` lesson — retirement is manual on recall for now.

**Known unlinted route (named, accepted):** phone-channel captures carry `via: phone`, so the gate correctly no-ops them — the phone is not a lesson path. A lesson that arrives by phone should be re-captured through § How to capture a lesson at ingest time, not written to `sources/` as-is with a lesson's ambitions.

## Change control

Edit THIS file to change the lesson format — never edit the format into the individual mechanisms; they cite this file by path. When the machine-checkable checks here change, update `bin/lesson-lint` **in the same change**. The binding tests live in `vault-librarian/tests/test_lesson_lint.py` and include round-trips through REAL `vault-capture` output (the Phase-2 gauntlet found that hand-written fixtures alone green-lit a gate that never fired on real capture files — quoted-vs-bare YAML; don't regress that). If this file moves, update the pointer in the `VAULT-LESSON-CAPTURE` rule, `/closeout`, `/mine-lessons`, and Scribe.
