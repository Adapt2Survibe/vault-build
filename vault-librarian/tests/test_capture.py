"""Subprocess tests for bin/vault-capture (CONTRACTS.md § bin/vault-capture).

The script is zero-dependency stdlib Python; these tests exercise it as a real
subprocess with $VAULT_ROOT pointed at a temp vault tree. No vault_librarian
imports — capture is upstream of the indexing pipeline and never embeds.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "vault-capture"

NOTE_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{6})-([a-z0-9-]+)\.md$")


@pytest.fixture
def capture_root(tmp_path: Path) -> Path:
    """A vault root with empty personal + company inboxes."""
    root = tmp_path / "vroot"
    for vault in ("personal", "company"):
        (root / f"vault-{vault}" / "inbox").mkdir(parents=True)
    return root


def run_capture(
    root: Path, *args: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "VAULT_ROOT": str(root)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        input=stdin,
        timeout=30,
    )


def inbox(root: Path, vault: str = "personal") -> Path:
    return root / f"vault-{vault}" / "inbox"


def created_file(
    result: subprocess.CompletedProcess[str], root: Path, vault: str = "personal"
) -> Path:
    """Assert success contract: exit 0, stdout = abs path of a real inbox file."""
    assert result.returncode == 0, f"stderr: {result.stderr}"
    path = Path(result.stdout.strip())
    assert path.is_absolute(), f"stdout must be an absolute path, got: {result.stdout!r}"
    assert path.is_file()
    assert path.parent.resolve() == inbox(root, vault).resolve()
    return path


def read_note(path: Path) -> tuple[dict, str]:
    """Split a captured note into (front-matter dict, body)."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "captured note must start with YAML front matter"
    end = text.index("\n---\n", 4)
    meta = yaml.safe_load(text[4 : end + 1])
    body = text[end + 5 :]
    return meta, body


def ts_window(span: int = 10) -> list[str]:
    """Timestamp prefixes covering now-1s .. now+span seconds (collision pre-seeding)."""
    now = datetime.now()
    return [(now + timedelta(seconds=i)).strftime("%Y-%m-%d-%H%M%S") for i in range(-1, span + 1)]


# --- script artifact -------------------------------------------------------


def test_script_exists_executable_with_python3_shebang() -> None:
    assert SCRIPT.is_file(), f"missing script: {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), "vault-capture must have the executable bit set"
    first_line = SCRIPT.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/usr/bin/env python3"


def test_script_imports_only_stdlib() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    imported = set(re.findall(r"^(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)", source, re.M))
    third_party = imported - set(sys.stdlib_module_names)
    assert not third_party, f"non-stdlib imports found: {third_party}"


# --- text notes ------------------------------------------------------------


def test_text_note_front_matter_fields(capture_root: Path) -> None:
    result = run_capture(capture_root, "quick", "thought", "about", "embeddings")
    path = created_file(result, capture_root)
    meta, body = read_note(path)
    assert meta["via"] == "vault-capture"
    assert meta["type_hint"] == "note"
    assert isinstance(meta["captured"], datetime)
    assert abs((datetime.now() - meta["captured"]).total_seconds()) < 120
    assert body.strip() == "quick thought about embeddings"


def test_text_note_captured_is_iso_local_timestamp(capture_root: Path) -> None:
    result = run_capture(capture_root, "timestamp", "check")
    path = created_file(result, capture_root)
    raw = path.read_text(encoding="utf-8")
    assert re.search(r"^captured: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", raw, re.M)


def test_text_note_filename_pattern_and_local_time(capture_root: Path) -> None:
    result = run_capture(capture_root, "filename", "pattern", "check")
    path = created_file(result, capture_root)
    match = NOTE_FILENAME_RE.fullmatch(path.name)
    assert match, f"filename does not match <ts>-<slug>.md: {path.name}"
    stamp = datetime.strptime(match.group(1), "%Y-%m-%d-%H%M%S")
    assert abs((datetime.now() - stamp).total_seconds()) < 120, "timestamp must be local time"
    assert match.group(2) == "filename-pattern-check"


def test_stdout_is_exactly_one_line(capture_root: Path) -> None:
    result = run_capture(capture_root, "one", "line", "out")
    assert result.returncode == 0
    assert result.stdout == result.stdout.strip() + "\n"
    assert "\n" not in result.stdout.strip()


def test_single_word_that_is_not_a_file_becomes_text_note(capture_root: Path) -> None:
    result = run_capture(capture_root, "hello")
    path = created_file(result, capture_root)
    meta, body = read_note(path)
    assert meta["type_hint"] == "note"
    assert body.strip() == "hello"


def test_multiword_args_containing_a_file_path_stay_a_text_note(
    capture_root: Path, tmp_path: Path
) -> None:
    real_file = tmp_path / "notes.txt"
    real_file.write_text("file body\n")
    result = run_capture(capture_root, "check", str(real_file))
    path = created_file(result, capture_root)
    assert path.suffix == ".md"
    meta, body = read_note(path)
    assert meta["type_hint"] == "note"
    assert body.strip() == f"check {real_file}"


# --- options ---------------------------------------------------------------


def test_title_option_sets_front_matter_and_slug(capture_root: Path) -> None:
    result = run_capture(capture_root, "some", "body", "text", "--title", "Engramme Notes")
    path = created_file(result, capture_root)
    meta, body = read_note(path)
    assert meta["title"] == "Engramme Notes"
    assert NOTE_FILENAME_RE.fullmatch(path.name).group(2) == "engramme-notes"
    assert body.strip() == "some body text"


def test_tags_option_parsed_into_list(capture_root: Path) -> None:
    result = run_capture(capture_root, "tagged", "note", "--tags", "ai,memory,tools")
    path = created_file(result, capture_root)
    meta, _ = read_note(path)
    assert meta["tags"] == ["ai", "memory", "tools"]


def test_optional_fields_omitted_when_absent(capture_root: Path) -> None:
    result = run_capture(capture_root, "bare", "note")
    path = created_file(result, capture_root)
    meta, _ = read_note(path)
    assert "title" not in meta
    assert "tags" not in meta
    assert "url" not in meta


def test_vault_company_routes_to_company_inbox(capture_root: Path) -> None:
    result = run_capture(capture_root, "company", "note", "--vault", "company")
    path = created_file(result, capture_root, vault="company")
    meta, _ = read_note(path)
    assert meta["type_hint"] == "note"
    assert not list(inbox(capture_root, "personal").iterdir())


def test_invalid_vault_choice_rejected(capture_root: Path) -> None:
    result = run_capture(capture_root, "note", "--vault", "shared")
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr
    assert not list(inbox(capture_root, "personal").iterdir())
    assert not list(inbox(capture_root, "company").iterdir())


# --- slug rules ------------------------------------------------------------


def test_slug_lowercased_and_punctuation_collapsed(capture_root: Path) -> None:
    result = run_capture(capture_root, "body", "--title", "Hello, World! (v2)")
    path = created_file(result, capture_root)
    assert NOTE_FILENAME_RE.fullmatch(path.name).group(2) == "hello-world-v2"


def test_slug_capped_at_40_chars_without_edge_hyphens(capture_root: Path) -> None:
    long_title = "the quick brown fox jumps over the lazy dog repeatedly and forever"
    result = run_capture(capture_root, "body", "--title", long_title)
    path = created_file(result, capture_root)
    slug = NOTE_FILENAME_RE.fullmatch(path.name).group(2)
    assert len(slug) <= 40
    assert not slug.startswith("-")
    assert not slug.endswith("-")


def test_slug_from_first_six_words_when_no_title(capture_root: Path) -> None:
    result = run_capture(capture_root, "-", stdin="alpha beta gamma delta epsilon zeta eta theta")
    path = created_file(result, capture_root)
    assert NOTE_FILENAME_RE.fullmatch(path.name).group(2) == "alpha-beta-gamma-delta-epsilon-zeta"


def test_unsluggable_title_still_produces_valid_filename(capture_root: Path) -> None:
    result = run_capture(capture_root, "body", "--title", "???!!!")
    path = created_file(result, capture_root)
    assert NOTE_FILENAME_RE.fullmatch(path.name), f"invalid filename: {path.name}"


def test_slug_collision_appends_2(capture_root: Path) -> None:
    box = inbox(capture_root)
    for ts in ts_window():
        (box / f"{ts}-fixed-slug.md").write_text("occupied\n")
    result = run_capture(capture_root, "collide", "--title", "Fixed Slug")
    path = created_file(result, capture_root)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{6}-fixed-slug-2\.md", path.name)


def test_slug_collision_appends_3_when_2_taken(capture_root: Path) -> None:
    box = inbox(capture_root)
    for ts in ts_window():
        (box / f"{ts}-fixed-slug.md").write_text("occupied\n")
        (box / f"{ts}-fixed-slug-2.md").write_text("occupied\n")
    result = run_capture(capture_root, "collide", "--title", "Fixed Slug")
    path = created_file(result, capture_root)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{6}-fixed-slug-3\.md", path.name)


# --- stdin capture ---------------------------------------------------------


def test_stdin_capture_reads_body(capture_root: Path) -> None:
    result = run_capture(capture_root, "-", stdin="line one\nline two\n")
    path = created_file(result, capture_root)
    meta, body = read_note(path)
    assert meta["type_hint"] == "note"
    assert body.strip() == "line one\nline two"


# --- URL capture -----------------------------------------------------------


def test_url_capture_front_matter_and_placeholder_body(capture_root: Path) -> None:
    url = "https://www.engramme.example/blog/launch"
    result = run_capture(capture_root, url)
    path = created_file(result, capture_root)
    meta, body = read_note(path)
    assert meta["type_hint"] == "website"
    assert meta["url"] == url
    assert meta["via"] == "vault-capture"
    assert body.strip() == "(URL capture — fetch on ingest)"


def test_url_slug_from_host_and_path(capture_root: Path) -> None:
    result = run_capture(capture_root, "https://news.example.com/ai/2026")
    path = created_file(result, capture_root)
    slug = NOTE_FILENAME_RE.fullmatch(path.name).group(2)
    assert slug == "news-example-com-ai-2026"


def test_url_capture_with_title_and_tags(capture_root: Path) -> None:
    result = run_capture(
        capture_root,
        "http://example.com/page",
        "--title",
        "Example Page",
        "--tags",
        "web,refs",
    )
    path = created_file(result, capture_root)
    meta, _ = read_note(path)
    assert meta["title"] == "Example Page"
    assert meta["tags"] == ["web", "refs"]
    assert meta["url"] == "http://example.com/page"
    assert NOTE_FILENAME_RE.fullmatch(path.name).group(2) == "example-page"


# --- file capture ----------------------------------------------------------


def test_file_capture_copies_bytes_unmodified(capture_root: Path, tmp_path: Path) -> None:
    src = tmp_path / "data.bin"
    payload = b"alpha\x00beta\xffgamma" * 10
    src.write_bytes(payload)
    result = run_capture(capture_root, str(src))
    path = created_file(result, capture_root)
    assert path.read_bytes() == payload


def test_file_capture_leaves_original_untouched(capture_root: Path, tmp_path: Path) -> None:
    src = tmp_path / "keepme.txt"
    src.write_text("original content\n")
    before = src.stat().st_mtime_ns
    result = run_capture(capture_root, str(src))
    created_file(result, capture_root)
    assert src.is_file(), "original must never be moved"
    assert src.read_text() == "original content\n"
    assert src.stat().st_mtime_ns == before


def test_file_capture_dest_name_is_ts_plus_original_name(
    capture_root: Path, tmp_path: Path
) -> None:
    src = tmp_path / "notes.txt"
    src.write_text("plain text\n")
    result = run_capture(capture_root, str(src))
    path = created_file(result, capture_root)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{6}-notes\.txt", path.name)


def test_file_capture_md_copied_verbatim_without_new_front_matter(
    capture_root: Path, tmp_path: Path
) -> None:
    src = tmp_path / "existing.md"
    original = "---\nid: src-existing\ntitle: Existing\n---\n\nAlready has front matter.\n"
    src.write_text(original)
    result = run_capture(capture_root, str(src))
    path = created_file(result, capture_root)
    assert path.read_text(encoding="utf-8") == original


def test_file_capture_collision_appends_2_before_extension(
    capture_root: Path, tmp_path: Path
) -> None:
    src = tmp_path / "data.txt"
    src.write_text("payload\n")
    box = inbox(capture_root)
    for ts in ts_window():
        (box / f"{ts}-data.txt").write_text("occupied\n")
    result = run_capture(capture_root, str(src))
    path = created_file(result, capture_root)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{6}-data-2\.txt", path.name)
    assert path.read_text() == "payload\n"


# --- error paths -----------------------------------------------------------


def test_no_args_exit_1_nothing_to_capture(capture_root: Path) -> None:
    result = run_capture(capture_root)
    assert result.returncode == 1
    assert "nothing to capture" in result.stderr.lower()
    assert result.stdout == ""


def test_empty_stdin_exit_1_nothing_to_capture(capture_root: Path) -> None:
    result = run_capture(capture_root, "-", stdin="")
    assert result.returncode == 1
    assert "nothing to capture" in result.stderr.lower()
    assert result.stdout == ""


def test_whitespace_only_stdin_exit_1(capture_root: Path) -> None:
    result = run_capture(capture_root, "-", stdin="  \n\t\n")
    assert result.returncode == 1
    assert "nothing to capture" in result.stderr.lower()
    assert result.stdout == ""


def test_missing_inbox_exit_1_with_stderr_message(tmp_path: Path) -> None:
    bare_root = tmp_path / "no-inbox-root"
    bare_root.mkdir()
    result = run_capture(bare_root, "some", "text")
    assert result.returncode == 1
    assert "inbox" in result.stderr.lower()
    assert result.stdout == ""


def test_missing_company_inbox_exit_1(tmp_path: Path) -> None:
    root = tmp_path / "personal-only-root"
    (root / "vault-personal" / "inbox").mkdir(parents=True)
    result = run_capture(root, "text", "--vault", "company")
    assert result.returncode == 1
    assert "inbox" in result.stderr.lower()
    assert result.stdout == ""


# --- --via provenance flag (phone-capture channel spec, 2026-06-12) ------------


class TestViaFlag:
    """--via records capture provenance; default stays vault-capture."""

    def test_default_via_unchanged(self, capture_root: Path) -> None:
        result = run_capture(capture_root, "default via note")
        meta, _ = read_note(created_file(result, capture_root))
        assert meta["via"] == "vault-capture"

    def test_via_phone_recorded(self, capture_root: Path) -> None:
        result = run_capture(capture_root, "phone note", "--via", "phone")
        meta, _ = read_note(created_file(result, capture_root))
        assert meta["via"] == "phone"

    def test_via_applies_to_url_capture(self, capture_root: Path) -> None:
        result = run_capture(capture_root, "https://example.com/x", "--via", "phone")
        meta, _ = read_note(created_file(result, capture_root))
        assert meta["via"] == "phone"
        assert meta["type_hint"] == "website"

    def test_via_applies_to_stdin_capture(self, capture_root: Path) -> None:
        result = run_capture(capture_root, "-", "--via", "phone", stdin="stdin body words")
        meta, _ = read_note(created_file(result, capture_root))
        assert meta["via"] == "phone"

    def test_hostile_via_is_yaml_safe(self, capture_root: Path) -> None:
        result = run_capture(capture_root, "note words", "--via", "x: y")
        meta, _ = read_note(created_file(result, capture_root))
        assert meta["via"] == "x: y"
        assert "y" not in meta  # the colon must not mint a second YAML key


# --- metadata flags with a file input are ignored: warn, don't discard silently -----
# (2026-08-14: a real capture passed --title/--tags/--via with a file path and got a
# verbatim copy with no diagnostic. The verbatim copy is CORRECT and contracted; the
# silence is not — the operator's stated provenance vanished with exit 0 and no stderr.)


class TestFileCaptureIgnoredFlagsWarn:
    """File capture stays byte-verbatim, but ignored metadata flags must be announced."""

    def _file(self, tmp_path: Path) -> Path:
        src = tmp_path / "note.md"
        src.write_text("body stays verbatim\n", encoding="utf-8")
        return src

    def test_title_with_file_input_warns_on_stderr(
        self, capture_root: Path, tmp_path: Path
    ) -> None:
        result = run_capture(capture_root, str(self._file(tmp_path)), "--title", "IGNORED")
        assert result.returncode == 0
        assert "--title" in result.stderr
        assert "ignored" in result.stderr.lower()

    def test_tags_and_via_with_file_input_both_named_in_warning(
        self, capture_root: Path, tmp_path: Path
    ) -> None:
        result = run_capture(
            capture_root, str(self._file(tmp_path)), "--tags", "a,b", "--via", "somewhere"
        )
        assert result.returncode == 0
        assert "--tags" in result.stderr
        assert "--via" in result.stderr

    def test_warning_does_not_disturb_stdout_or_bytes(
        self, capture_root: Path, tmp_path: Path
    ) -> None:
        src = self._file(tmp_path)
        result = run_capture(capture_root, str(src), "--title", "IGNORED")
        assert len(result.stdout.strip().splitlines()) == 1
        dest = created_file(result, capture_root)
        assert dest.read_bytes() == src.read_bytes()

    def test_no_warning_when_no_metadata_flags_passed(
        self, capture_root: Path, tmp_path: Path
    ) -> None:
        result = run_capture(capture_root, str(self._file(tmp_path)))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_no_warning_for_text_capture_where_flags_are_honored(
        self, capture_root: Path
    ) -> None:
        result = run_capture(capture_root, "a text note", "--title", "Honored", "--via", "x")
        assert result.returncode == 0
        assert result.stderr == ""
