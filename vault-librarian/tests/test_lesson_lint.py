"""Tests for bin/lesson-lint — the deterministic FORM gate for vault lessons.

Spec: docs/lesson-schema.md § Enforcement (corpus-validated by the 2026-07-11
verification pass). Stdlib-only script; validates a lesson note's machine-checkable
form (7 hard checks + 4 flag-tier advisories) and no-ops on non-lesson notes.
HARD fail → nonzero exit; FLAG → exit 0 with a warning. It never edits the note.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LINT = Path(__file__).resolve().parents[2] / "bin" / "lesson-lint"


def run_lint(*paths: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LINT), *paths],
        capture_output=True,
        text=True,
        timeout=30,
    )


def write_note(tmp_path: Path, name: str, front: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(f"---\n{front}\n---\n\n{body}\n", encoding="utf-8")
    return p


# A fully-conforming durable lesson in the REAL captured vocabulary
# (UPPERCASE value, parenthetical, trailing period, n/a verify).
GOOD_DURABLE_FRONT = (
    "title: launchd job runs but does nothing\n"
    "type_hint: note\n"
    "via: lesson-capture\n"
    "tags: [macos, launchd, gotcha, durable]"
)
_VOL_D = "Volatility: DURABLE (macOS behavior)."
GOOD_DURABLE_BODY = (
    "Symptom: a thing happens.\n\n"
    "Tell: the diagnostic.\nFix: do X.\nWhy: because Y.\n"
    "Scope: macOS launchd.\n\n"
    "Volatility: DURABLE (macOS behavior).\n"
    "As of: 2026-07-11.\n"
    "Verify: n/a (timeless)."
)

# A conforming volatile lesson with a freeform version token.
GOOD_VOLATILE_FRONT = (
    "title: headless agent still writes despite the restrict flag\n"
    "type_hint: note\n"
    "via: lesson-capture\n"
    "tags: [claude-code, headless, volatile]"
)
GOOD_VOLATILE_BODY = (
    "Symptom: restriction flag ignored.\n\n"
    "Tell: it still wrote.\nFix: use the other flag.\nWhy: the flag only pre-approves.\n"
    "Scope: headless claude -p.\n\n"
    "Volatility: VOLATILE (CLI flag semantics).\n"
    "As of: Claude Code ~2.1.x, 2026-07-11.\n"
    "Verify: check current claude --help."
)


def test_script_exists_executable_stdlib_only() -> None:
    assert LINT.is_file()
    assert os.access(LINT, os.X_OK)
    src = LINT.read_text()
    assert src.startswith("#!/usr/bin/env python3")
    for banned in ("import yaml", "import requests", "vault_librarian", "import numpy"):
        assert banned not in src, f"lesson-lint must be stdlib-only; found {banned}"


class TestTriggerGate:
    def test_non_lesson_note_is_noop_pass(self, tmp_path: Path) -> None:
        # a plain source capture — no via: lesson-capture — must never be flagged
        p = write_note(
            tmp_path, "src.md",
            "title: Some article\ntype_hint: website\nvia: vault-capture\ntags: [reference]",
            "Just some captured content. No stamp, no Scope, nothing.",
        )
        r = run_lint(str(p))
        assert r.returncode == 0, r.stdout + r.stderr

    def test_missing_via_is_noop_pass(self, tmp_path: Path) -> None:
        p = write_note(tmp_path, "n.md", "title: x\ntags: [a]", "body")
        assert run_lint(str(p)).returncode == 0


class TestConforming:
    def test_good_durable_passes(self, tmp_path: Path) -> None:
        p = write_note(tmp_path, "d.md", GOOD_DURABLE_FRONT, GOOD_DURABLE_BODY)
        r = run_lint(str(p))
        assert r.returncode == 0, r.stdout + r.stderr

    def test_good_volatile_passes(self, tmp_path: Path) -> None:
        p = write_note(tmp_path, "v.md", GOOD_VOLATILE_FRONT, GOOD_VOLATILE_BODY)
        r = run_lint(str(p))
        assert r.returncode == 0, r.stdout + r.stderr

    def test_inline_labels_pass(self, tmp_path: Path) -> None:
        # the real corpus writes Tell/Fix/Scope INLINE in prose, not as their own lines
        body = (
            "Symptom: it broke. Tell: the sign. Fix: do X. Why: mechanism. "
            "Scope: any macOS launchd job.\n\n"
            "Volatility: DURABLE (behavior).\nAs of: 2026-07-11.\nVerify: n/a."
        )
        p = write_note(tmp_path, "inline.md", GOOD_DURABLE_FRONT, body)
        r = run_lint(str(p))
        assert r.returncode == 0, r.stdout + r.stderr

    def test_real_corpus_vocabulary_passes(self, tmp_path: Path) -> None:
        # exact shapes seen in the real corpus: obsolete-when-fixed, freeform version
        body = GOOD_VOLATILE_BODY.replace(
            "Volatility: VOLATILE (CLI flag semantics).",
            "Volatility: VOLATILE — open upstream bug. OBSOLETE WHEN FIXED: delete if it works.",
        )
        p = write_note(tmp_path, "ov.md", GOOD_VOLATILE_FRONT, body)
        assert run_lint(str(p)).returncode == 0


class TestHardFails:
    def test_no_volatility_tag_fails(self, tmp_path: Path) -> None:
        front = GOOD_DURABLE_FRONT.replace(", durable]", "]")
        p = write_note(tmp_path, "n.md", front, GOOD_DURABLE_BODY)
        r = run_lint(str(p))
        assert r.returncode != 0
        assert "durable" in (r.stdout + r.stderr).lower()

    def test_two_volatility_tags_fails(self, tmp_path: Path) -> None:
        front = GOOD_DURABLE_FRONT.replace(", durable]", ", durable, volatile]")
        p = write_note(tmp_path, "n.md", front, GOOD_DURABLE_BODY)
        assert run_lint(str(p)).returncode != 0

    def test_tag_value_mismatch_fails(self, tmp_path: Path) -> None:
        # tag says durable, Volatility says volatile
        p = write_note(
            tmp_path, "n.md", GOOD_DURABLE_FRONT,
            GOOD_DURABLE_BODY.replace(_VOL_D, "Volatility: VOLATILE (x)."),
        )
        r = run_lint(str(p))
        assert r.returncode != 0
        out = (r.stdout + r.stderr).lower()
        assert "match" in out or "consist" in out

    def test_missing_asof_line_fails(self, tmp_path: Path) -> None:
        p = write_note(
            tmp_path, "n.md", GOOD_DURABLE_FRONT,
            GOOD_DURABLE_BODY.replace("As of: 2026-07-11.\n", ""),
        )
        assert run_lint(str(p)).returncode != 0

    def test_bad_volatility_value_fails(self, tmp_path: Path) -> None:
        p = write_note(
            tmp_path, "n.md", GOOD_DURABLE_FRONT,
            GOOD_DURABLE_BODY.replace(_VOL_D, "Volatility: maybe someday"),
        )
        assert run_lint(str(p)).returncode != 0

    def test_unparseable_asof_date_fails(self, tmp_path: Path) -> None:
        p = write_note(
            tmp_path, "n.md", GOOD_DURABLE_FRONT,
            GOOD_DURABLE_BODY.replace("As of: 2026-07-11.", "As of: sometime last week."),
        )
        assert run_lint(str(p)).returncode != 0

    def test_missing_scope_fails(self, tmp_path: Path) -> None:
        p = write_note(
            tmp_path, "n.md", GOOD_DURABLE_FRONT,
            GOOD_DURABLE_BODY.replace("Scope: macOS launchd.\n", ""),
        )
        r = run_lint(str(p))
        assert r.returncode != 0
        assert "scope" in (r.stdout + r.stderr).lower()


class TestFlagsNeverFail:
    def test_solution_first_title_flags_but_passes(self, tmp_path: Path) -> None:
        front = GOOD_DURABLE_FRONT.replace(
            "title: launchd job runs but does nothing", "title: watcher plist fix"
        )
        p = write_note(tmp_path, "n.md", front, GOOD_DURABLE_BODY)
        r = run_lint(str(p))
        assert r.returncode == 0, "flags must never block"
        assert "flag" in (r.stdout + r.stderr).lower() or "title" in (r.stdout + r.stderr).lower()

    def test_volatile_bare_date_flags_but_passes(self, tmp_path: Path) -> None:
        body = GOOD_VOLATILE_BODY.replace(
            "As of: Claude Code ~2.1.x, 2026-07-11.", "As of: 2026-07-11."
        )
        p = write_note(tmp_path, "n.md", GOOD_VOLATILE_FRONT, body)
        assert run_lint(str(p)).returncode == 0

    def test_volatile_verify_na_flags_but_passes(self, tmp_path: Path) -> None:
        body = GOOD_VOLATILE_BODY.replace("Verify: check current claude --help.", "Verify: n/a.")
        p = write_note(tmp_path, "n.md", GOOD_VOLATILE_FRONT, body)
        assert run_lint(str(p)).returncode == 0

    def test_missing_fix_label_flags_but_passes(self, tmp_path: Path) -> None:
        body = GOOD_DURABLE_BODY.replace("Fix: do X.\n", "")
        p = write_note(tmp_path, "n.md", GOOD_DURABLE_FRONT, body)
        assert run_lint(str(p)).returncode == 0


class TestMultiFileAndDir:
    def test_one_fail_makes_run_nonzero(self, tmp_path: Path) -> None:
        good = write_note(tmp_path, "good.md", GOOD_DURABLE_FRONT, GOOD_DURABLE_BODY)
        bad = write_note(
            tmp_path, "bad.md", GOOD_DURABLE_FRONT,
            GOOD_DURABLE_BODY.replace("Scope: macOS launchd.\n", ""),
        )
        assert run_lint(str(good), str(bad)).returncode != 0

    def test_dir_lints_all_notes(self, tmp_path: Path) -> None:
        d = tmp_path / "sources"
        d.mkdir()
        write_note(d, "a.md", GOOD_DURABLE_FRONT, GOOD_DURABLE_BODY)
        write_note(d, "b.md", GOOD_VOLATILE_FRONT, GOOD_VOLATILE_BODY)
        assert run_lint(str(d)).returncode == 0


class TestRealCapturePipeline:
    """P0 from the Phase-2 gauntlet: vault-capture JSON-quotes via/tags in front
    matter; the linter must parse ACTUAL capture output, not hand-written
    fixtures. These tests run the real vault-capture -> lesson-lint pipeline."""

    CAPTURE = Path(__file__).resolve().parents[2] / "bin" / "vault-capture"

    def _capture_lesson(self, root: Path, body: str, tags: str) -> Path:
        (root / "vault-personal" / "inbox").mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "VAULT_ROOT": str(root)}
        r = subprocess.run(
            [sys.executable, str(self.CAPTURE), "-", "--via", "lesson-capture",
             "--title", "launchd job runs but does nothing", "--tags", tags],
            input=body, env=env, capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, r.stderr
        return Path(r.stdout.strip())

    def test_conforming_capture_output_is_linted_and_passes(self, tmp_path: Path) -> None:
        note = self._capture_lesson(tmp_path, GOOD_DURABLE_BODY, "macos,launchd,durable")
        r = run_lint(str(note))
        out = r.stdout + r.stderr
        # the gate must FIRE on real capture output (quoted via) and pass it
        assert "no lesson-capture notes" not in out, "gate no-oped on real capture output"
        assert r.returncode == 0, out
        assert out.startswith("ok"), out

    def test_malformed_capture_output_fails_through_real_pipeline(self, tmp_path: Path) -> None:
        # no stamp, no Scope -> must HARD FAIL, not silently no-op
        note = self._capture_lesson(tmp_path, "just a bare thought", "macos,durable")
        r = run_lint(str(note))
        out = r.stdout + r.stderr
        assert "no lesson-capture notes" not in out, "gate no-oped on real capture output"
        assert r.returncode != 0, out


class TestArchiveDirsSkipped:
    """Dir-mode must not lint archived/quarantined notes: inbox/_processed/ holds
    pre-convention archival copies and .failed/ holds quarantine — both are
    historical records, not live pipeline state (found by real use, 2026-07-12)."""

    def test_processed_and_failed_dirs_ignored(self, tmp_path: Path) -> None:
        d = tmp_path / "inbox"
        (d / "_processed").mkdir(parents=True)
        (d / ".failed").mkdir()
        write_note(d, "live.md", GOOD_DURABLE_FRONT, GOOD_DURABLE_BODY)
        # archived + quarantined copies are malformed on purpose
        write_note(d / "_processed", "old.md", GOOD_DURABLE_FRONT, "no stamp at all")
        write_note(d / ".failed", "bad.md", GOOD_DURABLE_FRONT, "junk")
        r = run_lint(str(d))
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "1 lesson note(s) linted" in out  # only the live one



def flag_messages(out: str) -> list[str]:
    """The MESSAGE half of each FLAG line, path stripped.

    Load-bearing, not a convenience. pytest derives tmp_path from the test's own
    name, so a `"duplicate" in out` assertion matches the temp PATH of a test named
    test_duplicate_* and passes/fails for a reason unrelated to the gate. That is
    the same substring-containment defect the vault records in
    `substring-gate-blesses-truncation` — caught here by a negative test failing
    before the feature existed (2026-08-07).
    """
    msgs = []
    for line in out.splitlines():
        if line.startswith("FLAG ") and ": " in line:
            msgs.append(line.split(": ", 1)[1])
    return msgs


class TestDuplicateVerifyLine:
    """Batch-scoped copy-paste detector for the 'Verify:' stamp.

    Origin: three captures written in the SAME SECOND (inbox stamp 2026-07-28
    21:22:05; facts dated `As of: 2026-07-23`; ingested 2026-08-07, which is what
    their source filenames carry — three real dates, name the one you mean) shared an
    identical (after normalization) `Verify:` line. It was correct for substring-gate-blesses-
    truncation and factually WRONG for dev-green-consumer-red-pinned-dep-skew
    (pinned-dependency skew carrying 'a design lesson about matching semantics').
    Every hard check passed, because HARD-4 asks only whether a `Verify:` line is
    PRESENT. That a form gate cannot judge semantic truth is a Law, not a gap
    (first-principles review, 2026-08-07: MINIMAL). Copy-paste is the MECHANISM
    that produces wrong recipes, and equality after normalization IS computable — that is the
    whole scope of this check.

    Advisory only. Corpus-validated by a live run against `vault-personal/sources`
    (2026-08-07): **126 lesson notes → exactly 3 FLAGs, all the real defect, zero
    false positives.** The numbers that matter if you ever widen the exclusion list:
    73 n/a (57.9%) / 53 substantive / 1 duplicate group. Cross-tab: volatile+n/a = 0,
    volatile+substantive = 29 (all of them), durable+substantive = 24 — i.e. the
    `Verify:` field is NOT a vacuous placeholder, which is why this patch fixes the
    gate and leaves the field alone.
    """

    MARKER = "copy-paste"  # distinctive; cannot occur in a pytest tmp path

    def _volatile(self, tmp_path: Path, name: str, verify: str) -> Path:
        body = GOOD_VOLATILE_BODY.replace("Verify: check current claude --help.", verify)
        return write_note(tmp_path, name, GOOD_VOLATILE_FRONT, body)

    def _dup_flags(self, out: str) -> list[str]:
        return [m for m in flag_messages(out) if self.MARKER in m.lower()]

    def test_duplicate_substantive_verify_flags_both_but_passes(self, tmp_path: Path) -> None:
        shared = "Verify: run the gate against a truncated string and confirm it rejects."
        a = self._volatile(tmp_path, "a.md", shared)
        b = self._volatile(tmp_path, "b.md", shared)
        r = run_lint(str(a), str(b))
        out = r.stdout + r.stderr
        assert r.returncode == 0, f"advisory flags must never block: {out}"
        assert len(self._dup_flags(out)) == 2, f"both notes must be flagged: {out}"

    def test_duplicate_na_verify_does_not_flag(self, tmp_path: Path) -> None:
        """The false-positive guard the whole check depends on. 73 of 126 real notes carry
        an 'n/a' variant — sanctioned for durable lessons. Flagging those makes the
        check pure noise, and a noisy gate gets ignored."""
        a = write_note(tmp_path, "a.md", GOOD_DURABLE_FRONT, GOOD_DURABLE_BODY)
        b = write_note(tmp_path, "b.md", GOOD_DURABLE_FRONT, GOOD_DURABLE_BODY)
        r = run_lint(str(a), str(b))
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert self._dup_flags(out) == [], f"'n/a' is legitimately repeated: {out}"

    def test_na_variants_all_excluded(self, tmp_path: Path) -> None:
        """The real corpus writes n/a three ways; all are sanctioned."""
        a = self._volatile(tmp_path, "a.md", "Verify: n/a")
        b = self._volatile(tmp_path, "b.md", "Verify: n/a.")
        c = self._volatile(tmp_path, "c.md", "Verify: n/a (durable — timeless).")
        r = run_lint(str(a), str(b), str(c))
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert self._dup_flags(out) == [], out

    def test_unique_verify_lines_do_not_flag(self, tmp_path: Path) -> None:
        a = self._volatile(tmp_path, "a.md", "Verify: run `claude --help` and look for the flag.")
        b = self._volatile(tmp_path, "b.md", "Verify: upsert a record and compare the types back.")
        r = run_lint(str(a), str(b))
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert self._dup_flags(out) == [], out

    def test_normalizes_case_and_trailing_period(self, tmp_path: Path) -> None:
        """A copy-paste that picked up a capitalization or punctuation tweak is still
        a copy-paste — byte-equality alone would miss it."""
        a = self._volatile(tmp_path, "a.md", "Verify: Re-run the gate on a truncated string")
        b = self._volatile(tmp_path, "b.md", "Verify: re-run the gate on a truncated string.")
        r = run_lint(str(a), str(b))
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert len(self._dup_flags(out)) == 2, out

    def test_three_way_duplicate_flags_all_three(self, tmp_path: Path) -> None:
        """The exact shape of the real defect: three notes, one shared line."""
        shared = "Verify: durable — a design lesson about matching semantics."
        paths = [self._volatile(tmp_path, f"{n}.md", shared) for n in ("a", "b", "c")]
        r = run_lint(*[str(p) for p in paths])
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert len(self._dup_flags(out)) == 3, out

    def test_two_separate_duplicate_groups_both_flag(self, tmp_path: Path) -> None:
        """Groups must be independent — one shared line must not absorb another."""
        for n in ("a", "b"):
            self._volatile(tmp_path, f"{n}.md", "Verify: recipe one.")
        for n in ("c", "d"):
            self._volatile(tmp_path, f"{n}.md", "Verify: recipe two.")
        self._volatile(tmp_path, "e.md", "Verify: a unique recipe nobody shares.")
        r = run_lint(str(tmp_path))
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert len(self._dup_flags(out)) == 4, f"4 flagged, e.md untouched: {out}"

    def test_single_file_cannot_flag_documented_limit(self, tmp_path: Path) -> None:
        """Explicit limitation, asserted so it cannot silently change: the check is
        BATCH-scoped. One file alone has nothing to compare against, and a duplicate
        of an ALREADY-INGESTED note is invisible. Closing that would mean reading
        sources/, which this stdlib-only form gate deliberately does not do."""
        a = self._volatile(tmp_path, "a.md", "Verify: some shared recipe.")
        r = run_lint(str(a))
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert self._dup_flags(out) == [], out

    def test_non_lesson_notes_never_join_a_group(self, tmp_path: Path) -> None:
        """A non-lesson note (no via: lesson-capture) is a no-op pass and must not
        contribute its Verify line to any duplicate group."""
        self._volatile(tmp_path, "a.md", "Verify: shared recipe.")
        write_note(
            tmp_path, "b.md",
            "title: Some article\ntype_hint: website\nvia: vault-capture\ntags: [reference]",
            "Verify: shared recipe.",
        )
        r = run_lint(str(tmp_path))
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert self._dup_flags(out) == [], f"non-lesson note must not form a group: {out}"


class TestReviewFindings20260807:
    """Regressions for defects found by the 2026-08-07 review wall.

    Each test pins a failure that was REPRODUCED live, not theorized:
    UnicodeDecodeError escaping the OSError handler, an empty `Verify:` value
    passing every check, NA_RE swallowing substantive recipes, and a note
    grouping with itself under overlapping argv.
    """

    def _volatile(self, tmp_path: Path, name: str, verify: str) -> Path:
        body = GOOD_VOLATILE_BODY.replace("Verify: check current claude --help.", verify)
        return write_note(tmp_path, name, GOOD_VOLATILE_FRONT, body)

    def _dup_flags(self, out: str) -> list[str]:
        return [m for m in flag_messages(out) if "copy-paste" in m.lower()]

    # --- undecodable note must not abort the batch -------------------------
    def test_undecodable_note_fails_alone_and_batch_survives(self, tmp_path: Path) -> None:
        """A non-UTF-8 byte raises UnicodeDecodeError, which subclasses ValueError
        NOT OSError — so it escaped the handler written to quarantine exactly this
        and killed the whole run. Collect-then-print made it worse: the healthy
        notes' results were discarded too."""
        write_note(tmp_path, "a.md", GOOD_DURABLE_FRONT, GOOD_DURABLE_BODY)
        bad = tmp_path / "b.md"
        bad.write_bytes(
            f"---\n{GOOD_DURABLE_FRONT}\n---\n\nSymptom: caf\xe9. Fix: x. Scope: macOS.\n\n"
            "Volatility: durable.\nAs of: 2026-07-11.\nVerify: n/a.\n".encode("latin-1")
        )
        r = run_lint(str(tmp_path))
        out = r.stdout + r.stderr
        assert "Traceback" not in out, f"one bad note must not abort the run: {out}"
        assert "b.md" in out and "unreadable" in out, out
        assert "a.md" in out, f"healthy sibling's result must survive: {out}"
        assert "lesson note(s) linted" in out, f"counts line must still print: {out}"

    # --- empty Verify value is a missing stamp ----------------------------
    def test_empty_verify_value_hard_fails(self, tmp_path: Path) -> None:
        """`- **Verify:**` with nothing after it passed everything: HARD-4 tests
        `is None` and got '', FLAG d tests startswith('n/a') and '' doesn't, and
        normalize_verify then excluded it from grouping. Net: ok, 0 failed."""
        body = GOOD_DURABLE_BODY.replace("Verify: n/a (timeless).", "- **Verify:**")
        p = write_note(tmp_path, "n.md", GOOD_DURABLE_FRONT, body)
        r = run_lint(str(p))
        out = r.stdout + r.stderr
        assert r.returncode != 0, f"an empty Verify: value must not pass: {out}"
        assert "verify" in out.lower(), out

    # --- NA_RE must not swallow substantive recipes ------------------------
    def test_substantive_recipe_starting_with_na_still_groups(self, tmp_path: Path) -> None:
        """`NA region failover drill` is a real recipe, not a placeholder. The
        optional slash in NA_RE excluded it from grouping, so two copy-pasted
        copies went unflagged — the exact defect this check exists to catch."""
        shared = "Verify: NA region failover drill"
        a = self._volatile(tmp_path, "a.md", shared)
        b = self._volatile(tmp_path, "b.md", shared)
        r = run_lint(str(a), str(b))
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert len(self._dup_flags(out)) == 2, f"substantive 'NA ...' must group: {out}"

    def test_na_unless_qualifier_still_groups(self, tmp_path: Path) -> None:
        shared = "Verify: n/a unless the API changes — then re-run the smoke test"
        a = self._volatile(tmp_path, "a.md", shared)
        b = self._volatile(tmp_path, "b.md", shared)
        r = run_lint(str(a), str(b))
        assert len(self._dup_flags(r.stdout + r.stderr)) == 2, r.stdout + r.stderr

    def test_bare_na_variants_still_excluded(self, tmp_path: Path) -> None:
        """The three sanctioned corpus forms all carry a slash and must stay excluded."""
        a = self._volatile(tmp_path, "a.md", "Verify: n/a")
        b = self._volatile(tmp_path, "b.md", "Verify: n/a.")
        c = self._volatile(tmp_path, "c.md", "Verify: n/a (durable — timeless).")
        r = run_lint(str(a), str(b), str(c))
        assert self._dup_flags(r.stdout + r.stderr) == [], r.stdout + r.stderr

    # --- overlapping argv must not self-duplicate --------------------------
    def test_same_file_twice_is_not_its_own_duplicate(self, tmp_path: Path) -> None:
        a = self._volatile(tmp_path, "a.md", "Verify: some unique recipe.")
        r = run_lint(str(a), str(a))
        out = r.stdout + r.stderr
        assert self._dup_flags(out) == [], f"a note must not group with itself: {out}"

    def test_dir_plus_file_inside_it_is_not_a_duplicate(self, tmp_path: Path) -> None:
        a = self._volatile(tmp_path, "a.md", "Verify: another unique recipe.")
        r = run_lint(str(tmp_path), str(a))
        out = r.stdout + r.stderr
        assert self._dup_flags(out) == [], f"dir + contained file must not group: {out}"

    # --- gaps the reviewers named -----------------------------------------
    def test_note_missing_verify_entirely_in_a_batch(self, tmp_path: Path) -> None:
        """normalize_verify's None guard is load-bearing: mutation testing showed
        removing it kills the whole batch with AttributeError. Nothing pinned it."""
        body = GOOD_DURABLE_BODY.replace("Verify: n/a (timeless).", "")
        write_note(tmp_path, "missing.md", GOOD_DURABLE_FRONT, body)
        shared = "Verify: a shared recipe across two notes."
        self._volatile(tmp_path, "a.md", shared)
        self._volatile(tmp_path, "b.md", shared)
        r = run_lint(str(tmp_path))
        out = r.stdout + r.stderr
        assert "Traceback" not in out, out
        assert r.returncode != 0, "the missing-Verify note must HARD FAIL"
        assert len(self._dup_flags(out)) == 2, f"siblings must still group: {out}"

    def test_counts_line_exact_with_duplicate_flags(self, tmp_path: Path) -> None:
        """Four callers parse this line to detect a vacuous run, and the restructure
        moved every counter relative to the print loop. Nothing asserted it."""
        shared = "Verify: recipe shared by two."
        self._volatile(tmp_path, "a.md", shared)
        self._volatile(tmp_path, "b.md", shared)
        self._volatile(tmp_path, "c.md", "Verify: a recipe of its own.")
        write_note(tmp_path, "notalesson.md",
                   "title: x\ntype_hint: website\nvia: vault-capture\ntags: [ref]", "body")
        r = run_lint(str(tmp_path))
        out = r.stdout + r.stderr
        assert "3 lesson note(s) linted, 0 failed, 1 non-lesson file(s) skipped" in out, out

    def test_hard_fail_note_still_joins_a_duplicate_group(self, tmp_path: Path) -> None:
        """FAILs and FLAGs now print from the same results row; nothing pinned it."""
        shared = "Verify: recipe shared across a good and a bad note."
        self._volatile(tmp_path, "good.md", shared)
        bad_body = GOOD_VOLATILE_BODY.replace(
            "Verify: check current claude --help.", shared
        ).replace("Scope: headless claude -p.\n", "")
        write_note(tmp_path, "bad.md", GOOD_VOLATILE_FRONT, bad_body)
        r = run_lint(str(tmp_path))
        out = r.stdout + r.stderr
        assert r.returncode != 0, "the Scope-less note must still HARD FAIL"
        assert len(self._dup_flags(out)) == 2, f"both must still be flagged: {out}"
