"""Subprocess tests for bin/vault-phone-watcher (phone-capture channel spec).

The watcher drains a drop folder into the vault inbox by delegating to
bin/vault-capture. Stdlib-only script, tested as a real subprocess with
VAULTDROP_DIR / VAULT_ROOT / VAULT_CAPTURE_BIN env overrides.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

WATCHER = Path(__file__).resolve().parents[2] / "bin" / "vault-phone-watcher"
CAPTURE = Path(__file__).resolve().parents[2] / "bin" / "vault-capture"

STAMP_KEYS = {
    "ran_at",
    "ok",
    "reason",
    "processed",
    "failed",
    "pending",
    "quarantined",
    "cadence_seconds",
    "next_run_by",
}


@pytest.fixture
def channel(tmp_path: Path) -> dict:
    """A drop folder + vault root pair, wired through env overrides."""
    drop = tmp_path / "VaultDrop"
    drop.mkdir()
    root = tmp_path / "vroot"
    (root / "vault-personal" / "inbox").mkdir(parents=True)
    (root / "vault-personal" / "_maintenance").mkdir(parents=True)
    return {"drop": drop, "root": root}


def run_watcher(
    channel: dict, capture_bin: Path | None = None, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "VAULTDROP_DIR": str(channel["drop"]),
        "VAULT_ROOT": str(channel["root"]),
        "VAULT_CAPTURE_BIN": str(capture_bin if capture_bin is not None else CAPTURE),
        "VAULT_LOCK_FILE": str(channel["drop"].parent / "watcher.lock"),
        **(extra_env or {}),
    }
    return subprocess.run(
        [sys.executable, str(WATCHER)], env=env, capture_output=True, text=True, timeout=60
    )


def inbox_files(channel: dict) -> list[Path]:
    return sorted((channel["root"] / "vault-personal" / "inbox").iterdir())


def stamp(channel: dict) -> dict:
    p = channel["root"] / "vault-personal" / "_maintenance" / "phone-channel-stamp.json"
    return json.loads(p.read_text())


def front_matter_of(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    end = text.index("\n---\n", 4)
    return yaml.safe_load(text[4 : end + 1])


def test_script_exists_executable_stdlib_only() -> None:
    assert WATCHER.is_file()
    assert os.access(WATCHER, os.X_OK)
    src = WATCHER.read_text()
    assert src.startswith("#!/usr/bin/env python3")
    for banned in ("import requests", "import numpy", "import yaml"):
        assert banned not in src


@pytest.mark.skipif(
    not os.path.exists("/usr/bin/python3"), reason="system python3 not present"
)
def test_runs_under_system_python3(channel: dict) -> None:
    """The launchd job runs /usr/bin/python3 (system, ~3.9) ON PURPOSE — it can't
    be eaten by iCloud/uv/brew the way the venv symlink was (2026-06-14 outage).
    This pins that the watcher stays compatible with that older interpreter, so a
    future 3.10+-only construct can't silently break the unattended job."""
    (channel["drop"] / "sys-py.txt").write_text("ran under system python words")
    env = {
        **os.environ,
        "VAULTDROP_DIR": str(channel["drop"]),
        "VAULT_ROOT": str(channel["root"]),
        "VAULT_CAPTURE_BIN": str(CAPTURE),
        "VAULT_LOCK_FILE": str(channel["drop"].parent / "sys.lock"),
    }
    result = subprocess.run(
        ["/usr/bin/python3", str(WATCHER)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    s = stamp(channel)
    assert s["ok"] is True and s["processed"] == 1


class TestHappyPath:
    def test_text_note_lands_in_inbox_with_phone_provenance(self, channel: dict) -> None:
        (channel["drop"] / "capture-x.txt").write_text("a thought from the road")
        result = run_watcher(channel)
        assert result.returncode == 0, result.stderr
        files = inbox_files(channel)
        assert len(files) == 1
        meta = front_matter_of(files[0])
        assert meta["via"] == "phone"
        assert "a thought from the road" in files[0].read_text()
        assert not (channel["drop"] / "capture-x.txt").exists()  # canonical copy moved on
        s = stamp(channel)
        assert s["ok"] is True and s["processed"] == 1 and s["pending"] == 0

    def test_single_url_becomes_url_capture(self, channel: dict) -> None:
        (channel["drop"] / "capture-u.txt").write_text("https://example.com/article\n")
        run_watcher(channel)
        meta = front_matter_of(inbox_files(channel)[0])
        assert meta["type_hint"] == "website"
        assert meta["url"] == "https://example.com/article"
        assert meta["via"] == "phone"

    def test_multiline_text_with_url_inside_stays_a_note(self, channel: dict) -> None:
        (channel["drop"] / "capture-m.txt").write_text(
            "check this later\nhttps://example.com/thing\n"
        )
        run_watcher(channel)
        meta = front_matter_of(inbox_files(channel)[0])
        assert meta["type_hint"] == "note"
        assert "url" not in meta

    def test_multiple_files_processed_oldest_first(self, channel: dict) -> None:
        (channel["drop"] / "a.txt").write_text("first note words")
        (channel["drop"] / "b.txt").write_text("second note words")
        run_watcher(channel)
        assert len(inbox_files(channel)) == 2
        assert stamp(channel)["processed"] == 2

    def test_zero_file_run_still_writes_stamp(self, channel: dict) -> None:
        result = run_watcher(channel)
        assert result.returncode == 0
        s = stamp(channel)
        assert s["ok"] is True and s["processed"] == 0 and s["pending"] == 0


class TestFailurePaths:
    def test_empty_file_quarantined(self, channel: dict) -> None:
        (channel["drop"] / "empty.txt").write_text("   \n")
        run_watcher(channel)
        assert not (channel["drop"] / "empty.txt").exists()
        assert (channel["drop"] / ".failed" / "empty.txt").exists()
        s = stamp(channel)
        assert s["quarantined"] == 1 and s["ok"] is True and s["pending"] == 0

    def test_undecodable_file_quarantined(self, channel: dict) -> None:
        (channel["drop"] / "bin.txt").write_bytes(b"\xff\xfe\x00\x01garbage")
        run_watcher(channel)
        assert (channel["drop"] / ".failed" / "bin.txt").exists()
        assert stamp(channel)["quarantined"] == 1

    def test_failing_capture_keeps_file_and_flags_stamp(self, channel: dict, tmp_path) -> None:
        broken = tmp_path / "broken-capture"
        broken.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(7)\n")
        broken.chmod(0o755)
        (channel["drop"] / "note.txt").write_text("important words")
        result = run_watcher(channel, capture_bin=broken)
        assert result.returncode == 0  # the run completes; the failure is reported, not fatal
        assert (channel["drop"] / "note.txt").exists()  # retry queue
        s = stamp(channel)
        assert s["ok"] is False and s["failed"] == 1 and s["pending"] == 1
        assert "note.txt" in s["reason"] and "rc 7" in s["reason"]

    def test_quarantine_name_collision_gets_suffix(self, channel: dict) -> None:
        (channel["drop"] / ".failed").mkdir()
        (channel["drop"] / ".failed" / "empty.txt").write_text("older quarantine")
        (channel["drop"] / "empty.txt").write_text(" ")
        run_watcher(channel)
        quarantined = sorted(p.name for p in (channel["drop"] / ".failed").iterdir())
        assert quarantined == ["empty-2.txt", "empty.txt"]

    def test_missing_drop_dir_exits_1_with_stamp(self, channel: dict) -> None:
        import shutil

        shutil.rmtree(channel["drop"])
        result = run_watcher(channel)
        assert result.returncode == 1
        s = stamp(channel)
        assert s["ok"] is False and "missing" in s["reason"]


class TestSkips:
    def test_icloud_placeholder_left_alone_but_counted_pending(self, channel: dict) -> None:
        placeholder = channel["drop"] / ".capture-x.txt.icloud"
        placeholder.write_bytes(b"plist-placeholder")
        result = run_watcher(channel, extra_env={"PATH": os.environ["PATH"]})
        assert result.returncode == 0
        assert placeholder.exists()
        s = stamp(channel)
        assert s["processed"] == 0 and s["quarantined"] == 0
        assert s["pending"] == 1  # a stuck placeholder must be visible, not ok/0/0

    def test_dotfiles_ignored(self, channel: dict) -> None:
        (channel["drop"] / ".DS_Store").write_bytes(b"junk")
        run_watcher(channel)
        assert (channel["drop"] / ".DS_Store").exists()
        assert stamp(channel)["processed"] == 0

    def test_failed_dir_contents_never_reprocessed(self, channel: dict) -> None:
        (channel["drop"] / ".failed").mkdir()
        (channel["drop"] / ".failed" / "old.txt").write_text("quarantined words")
        result = run_watcher(channel)
        assert result.returncode == 0
        assert inbox_files(channel) == []
        assert (channel["drop"] / ".failed" / "old.txt").exists()  # untouched evidence
        s = stamp(channel)
        assert s["processed"] == 0 and s["quarantined"] == 0

    def test_lock_held_means_quiet_noop(self, channel: dict) -> None:
        (channel["drop"] / "note.txt").write_text("waiting words")
        lock_path = channel["drop"].parent / "watcher.lock"
        with open(lock_path, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            result = run_watcher(channel)
        assert result.returncode == 0
        assert (channel["drop"] / "note.txt").exists()  # untouched
        maint = channel["root"] / "vault-personal" / "_maintenance"
        assert not (maint / "phone-channel-stamp.json").exists()  # sibling owns this cycle


class TestStampContract:
    def test_stamp_schema_exact(self, channel: dict) -> None:
        run_watcher(channel)
        s = stamp(channel)
        assert set(s) == STAMP_KEYS
        assert s["cadence_seconds"] == 300
        ran = datetime.fromisoformat(s["ran_at"])
        nxt = datetime.fromisoformat(s["next_run_by"])
        assert nxt - ran == timedelta(seconds=600)

    def test_stamp_is_valid_json_after_overwrite(self, channel: dict) -> None:
        run_watcher(channel)
        (channel["drop"] / "n.txt").write_text("second run words")
        run_watcher(channel)
        s = stamp(channel)  # parses → atomic replace held
        assert s["processed"] == 1


class TestHardening:
    """Review-wall findings (run 20260612-phone-channel), reproduced before fixing."""

    def test_symlink_in_drop_quarantined_never_read(self, channel: dict, tmp_path: Path) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_text("private key material words")
        (channel["drop"] / "innocent.txt").symlink_to(secret)
        result = run_watcher(channel)
        assert result.returncode == 0
        assert inbox_files(channel) == []  # the target must never be captured
        assert secret.exists()
        assert not (channel["drop"] / "innocent.txt").exists()
        failed = list((channel["drop"] / ".failed").iterdir())
        assert len(failed) == 1 and failed[0].is_symlink()
        assert stamp(channel)["quarantined"] == 1

    def test_brctl_missing_keeps_run_healthy(self, channel: dict, tmp_path: Path) -> None:
        (channel["drop"] / ".x.txt.icloud").write_bytes(b"ph")
        (channel["drop"] / "note.txt").write_text("real note words")
        empty_path = tmp_path / "emptybin"
        empty_path.mkdir()
        result = run_watcher(channel, extra_env={"PATH": str(empty_path)})
        assert result.returncode == 0, result.stderr
        s = stamp(channel)
        assert s["processed"] == 1  # the real note still drained

    def test_vanishing_sibling_does_not_crash_run(self, channel: dict, tmp_path: Path) -> None:
        # A capture stub that deletes the OTHER drop file mid-run (iCloud race),
        # then succeeds: the run must finish, stamp, and not crash on the ghost.
        stub = tmp_path / "stub-capture"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "ghost = os.environ['GHOST']\n"
            "if os.path.exists(ghost):\n"
            "    os.unlink(ghost)\n"
            "print('/dev/null')\n"
        )
        stub.chmod(0o755)
        (channel["drop"] / "a-first.txt").write_text("first words")
        (channel["drop"] / "b-ghost.txt").write_text("ghost words")
        result = run_watcher(
            channel,
            capture_bin=stub,
            extra_env={"GHOST": str(channel["drop"] / "b-ghost.txt")},
        )
        assert result.returncode == 0, result.stderr
        s = stamp(channel)  # stamp written despite the mid-run disappearance
        assert s["processed"] >= 1

    def test_enumeration_denied_stamps_and_exits_1(self, channel: dict) -> None:
        (channel["drop"] / "note.txt").write_text("words")
        channel["drop"].chmod(0o000)
        try:
            result = run_watcher(channel)
        finally:
            channel["drop"].chmod(0o755)
        assert result.returncode == 1
        s = stamp(channel)
        assert s["ok"] is False and "enumerate" in s["reason"]

    def test_capture_timeout_counted_as_failure(self, channel: dict, tmp_path: Path) -> None:
        sleeper = tmp_path / "sleeper"
        sleeper.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n")
        sleeper.chmod(0o755)
        (channel["drop"] / "note.txt").write_text("slow words")
        result = run_watcher(
            channel, capture_bin=sleeper, extra_env={"VAULT_CAPTURE_TIMEOUT": "1"}
        )
        assert result.returncode == 0
        s = stamp(channel)
        assert s["ok"] is False and s["failed"] == 1
        assert "timeout" in s["reason"].lower()
        assert (channel["drop"] / "note.txt").exists()  # stays in the retry queue
