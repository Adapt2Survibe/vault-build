"""Tests for bin/vault-doctor — read-only vault health, including the
class of outage that killed MCP after the 2026-08-10 path move.

Stdlib-only. Never loads the embedding model. Session-start mode is one
line when healthy, prominent when not.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "vault-doctor"


def run_doc(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    e = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=e,
    )


def make_vault(root: Path) -> Path:
    personal = root / "vault-personal"
    for d in ("wiki/concepts", "sources", "inbox", "_maintenance", "journal"):
        (personal / d).mkdir(parents=True)
    (personal / "wiki" / "concepts" / "hub.md").write_text(
        "---\nid: wiki-hub\ntitle: Hub\ncreated: 2026-08-01\n"
        "tags: [needs-synthesis]\n---\n\nSee [[missing]].\n",
        encoding="utf-8",
    )
    (personal / "sources" / "2026-08-01-example.md").write_text(
        "---\nid: src-2026-08-01-example\ntitle: example\n"
        "tags: [durable]\nvia: lesson-capture\n---\n\n"
        "Scope: tests.\nVolatility: durable.\nAs of: 2026-08-01.\nVerify: n/a.\n",
        encoding="utf-8",
    )
    return personal


def test_script_exists_executable_stdlib_only() -> None:
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)
    src = SCRIPT.read_text(encoding="utf-8")
    assert src.startswith("#!/usr/bin/env python3")
    banned = (
        "import yaml",
        "import requests",
        "import vault_librarian",
        "from vault_librarian",
        "import numpy",
    )
    for name in banned:
        assert name not in src


def test_json_reports_counts_and_stale_src_path(tmp_path: Path) -> None:
    make_vault(tmp_path)
    venv = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    venv.mkdir(parents=True)
    dead = "/nonexistent/old-vault/vault-librarian/src"
    (venv / "sitecustomize.py").write_text(f'_SRC = "{dead}"\n', encoding="utf-8")
    (venv / "_editable_impl_vault_librarian.pth").write_text(dead + "\n", encoding="utf-8")

    cmds = tmp_path / "slash-commands" / "personal"
    cmds.mkdir(parents=True)
    (cmds / "recall.md").write_text(
        "See ~/Documents/Dev/vault/docs/lesson-schema.md\n", encoding="utf-8"
    )

    result = run_doc(
        "--root",
        str(tmp_path),
        "--venv",
        str(tmp_path / "venv"),
        "--json",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["counts"]["wiki"] == 1
    assert data["counts"]["sources"] == 1
    ids = {c["id"] for c in data["checks"]}
    assert "venv_src_paths" in ids
    venv_check = next(c for c in data["checks"] if c["id"] == "venv_src_paths")
    assert venv_check["ok"] is False
    assert venv_check["severity"] == "high"
    stale = next(c for c in data["checks"] if c["id"] == "stale_path_refs")
    assert stale["ok"] is False


def test_session_start_one_line_when_healthy(tmp_path: Path) -> None:
    make_vault(tmp_path)
    # No venv dir → doctor should still run; missing venv is a check, not a crash.
    # Give it a live src pointer so venv_src_paths is skip/ok rather than the
    # only story. Empty inbox, fresh files → session-start should not be silent
    # and should mention vault.
    result = run_doc(
        "--root",
        str(tmp_path),
        "--venv",
        str(tmp_path / "no-venv"),
        "--session-start",
    )
    assert result.returncode in (0, 1)
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert lines, "session-start must print something"
    assert any(ln.startswith("vault:") for ln in lines)


def test_inbox_older_than_24h_is_high(tmp_path: Path) -> None:
    personal = make_vault(tmp_path)
    stale = personal / "inbox" / "2026-07-01-old.md"
    stale.write_text("pending\n", encoding="utf-8")
    os.utime(stale, (0, 0))

    result = run_doc("--root", str(tmp_path), "--venv", str(tmp_path / "no-venv"), "--json")
    data = json.loads(result.stdout)
    inbox = next(c for c in data["checks"] if c["id"] == "inbox_pending")
    assert inbox["ok"] is False
    assert inbox["severity"] == "high"
    assert result.returncode == 1


def test_volatile_as_of_accepts_markdown_bold_label(tmp_path: Path) -> None:
    """Corpus lessons (and lesson-lint) allow **As of:** / **Volatility:**."""
    personal = make_vault(tmp_path)
    (personal / "sources" / "2026-06-01-bold-stamps.md").write_text(
        "---\nid: src-2026-06-01-bold\ntitle: bold stamps\n"
        "tags: [volatile]\nvia: lesson-capture\n---\n\n"
        "Scope: tests.\n**Volatility:** volatile — sdk key.\n"
        "**As of:** pinecone 9.1.0, 2026-06-01.\n**Verify:** pip show pinecone.\n",
        encoding="utf-8",
    )
    result = run_doc(
        "--root",
        str(tmp_path),
        "--venv",
        str(tmp_path / "no-venv"),
        "--stale-days",
        "30",
        "--json",
    )
    data = json.loads(result.stdout)
    vol = next(c for c in data["checks"] if c["id"] == "volatile_lessons")
    assert vol["ok"] is False
    assert "no As of" not in vol["detail"]
    assert "bold-stamps" in vol["detail"]


def test_volatile_lesson_older_than_stale_days_flags(tmp_path: Path) -> None:
    personal = make_vault(tmp_path)
    (personal / "sources" / "2026-01-01-old-volatile.md").write_text(
        "---\nid: src-2026-01-01-old-volatile\ntitle: old flag\n"
        "tags: [volatile]\nvia: lesson-capture\n---\n\n"
        "Scope: tests.\nVolatility: volatile — platform flag.\n"
        "As of: Claude Code 2.0.0, 2026-01-01.\nVerify: claude --help.\n",
        encoding="utf-8",
    )
    result = run_doc(
        "--root",
        str(tmp_path),
        "--venv",
        str(tmp_path / "no-venv"),
        "--stale-days",
        "30",
        "--json",
    )
    data = json.loads(result.stdout)
    vol = next(c for c in data["checks"] if c["id"] == "volatile_lessons")
    assert vol["ok"] is False
    assert "old-volatile" in vol["detail"]


def test_claimed_leftover_is_high(tmp_path: Path) -> None:
    personal = make_vault(tmp_path)
    leftover = personal / "inbox" / ".claimed-1-stranded.md"
    leftover.write_text("x\n", encoding="utf-8")
    result = run_doc("--root", str(tmp_path), "--venv", str(tmp_path / "no-venv"), "--json")
    data = json.loads(result.stdout)
    claimed = next(c for c in data["checks"] if c["id"] == "claimed_inbox")
    assert claimed["ok"] is False
    assert claimed["severity"] == "high"


def test_missing_vault_tree_is_high(tmp_path: Path) -> None:
    result = run_doc(
        "--root",
        str(tmp_path),
        "--venv",
        str(tmp_path / "no-venv"),
        "--json",
    )
    data = json.loads(result.stdout)
    tree = next(c for c in data["checks"] if c["id"] == "vault_tree")
    assert tree["ok"] is False
    assert tree["severity"] == "high"
    assert result.returncode == 1


def test_usage_unknown_flag_exits_2() -> None:
    result = run_doc("--definitely-not-a-flag")
    assert result.returncode == 2
