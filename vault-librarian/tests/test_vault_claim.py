"""Tests for bin/vault-claim — atomic inbox claim-by-rename.

Open-work: open-work/2026-07-15-inbox-claim-by-rename/.
Two sweep-mode /ingest sessions cannot both win the same inbox file.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "vault-claim"


def run_claim(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=merged,
    )


def test_script_exists_executable_stdlib_only() -> None:
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)
    src = SCRIPT.read_text(encoding="utf-8")
    assert src.startswith("#!/usr/bin/env python3")
    for banned in ("import yaml", "import requests", "vault_librarian", "import numpy"):
        assert banned not in src


def test_claim_renames_atomically_and_prints_new_path(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    src = inbox / "2026-08-13-120000-note.md"
    src.write_text("hello\n", encoding="utf-8")

    result = run_claim("claim", str(src))
    assert result.returncode == 0, result.stderr
    claimed = Path(result.stdout.strip())
    assert claimed.is_file()
    assert claimed.parent == inbox
    assert claimed.name.startswith(".claimed-")
    assert claimed.name.endswith("-2026-08-13-120000-note.md")
    assert not src.exists()
    assert claimed.read_text(encoding="utf-8") == "hello\n"


def test_second_claim_on_same_name_fails(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    src = inbox / "note.md"
    src.write_text("x\n", encoding="utf-8")
    first = run_claim("claim", str(src), env={"VAULT_CLAIM_PID": "42"})
    assert first.returncode == 0, first.stderr
    # Recreate the original name (the race: two sweepers both saw note.md).
    src.write_text("y\n", encoding="utf-8")
    # Same claim pid → dest .claimed-42-note.md already exists. os.rename
    # would overwrite; we must refuse.
    second = run_claim("claim", str(src), env={"VAULT_CLAIM_PID": "42"})
    assert second.returncode == 1
    assert "vault-claim: error:" in second.stderr
    assert src.exists(), "loser must leave the unclaimed file in place"
    assert Path(first.stdout.strip()).read_text(encoding="utf-8") == "x\n"


def test_release_restores_original_name(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    src = inbox / "note.md"
    src.write_text("x\n", encoding="utf-8")
    claimed = Path(run_claim("claim", str(src)).stdout.strip())
    result = run_claim("release", str(claimed))
    assert result.returncode == 0, result.stderr
    restored = Path(result.stdout.strip())
    assert restored == src
    assert src.is_file()
    assert not claimed.exists()


def test_sweep_releases_stale_dead_pid(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    # PID 1 is init/launchd — always alive on macOS. Use a pid that cannot exist.
    dead = inbox / ".claimed-999999999-stale.md"
    dead.write_text("left behind\n", encoding="utf-8")
    os.utime(dead, (0, 0))  # mtime = epoch → older than any max-age

    result = run_claim("sweep", "--inbox", str(inbox), "--max-age-seconds", "1")
    assert result.returncode == 0, result.stderr
    assert (inbox / "stale.md").is_file()
    assert not dead.exists()
    assert "released" in result.stdout.lower() or "1" in result.stdout


def test_sweep_keeps_live_pid_claim(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    # Claim subprocesses exit before sweep runs, so a live-pid fixture has to
    # be this test process itself — not a child that already reaped.
    claimed = inbox / f".claimed-{os.getpid()}-live.md"
    claimed.write_text("x\n", encoding="utf-8")
    os.utime(claimed, (0, 0))

    result = run_claim("sweep", "--inbox", str(inbox), "--max-age-seconds", "1")
    assert result.returncode == 0, result.stderr
    assert claimed.is_file(), "live-pid claim must not be swept even if old"
    assert not (inbox / "live.md").exists()


def test_claim_missing_file_exits_1(tmp_path: Path) -> None:
    result = run_claim("claim", str(tmp_path / "nope.md"))
    assert result.returncode == 1
    assert "vault-claim: error:" in result.stderr


def test_usage_without_args_exits_2() -> None:
    result = run_claim()
    assert result.returncode == 2
    assert "usage" in result.stderr.lower() or "usage" in result.stdout.lower()
