"""Tests for bin/vault-relink-venv — rewrite librarian venv src pointers.

The 2026-08-10 ~/Documents/Dev → ~/Dev move left sitecustomize.py and the
editable .pth pointing at a deleted path. MCP then died with
ModuleNotFoundError. This script is the mechanical repair.

Stdlib-only; exercises the real script as a subprocess.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "vault-relink-venv"


def run_relink(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def make_venv(tmp: Path, src_line: str) -> Path:
    """Minimal site-packages tree matching the real librarian venv layout."""
    sp = tmp / "lib" / "python3.12" / "site-packages"
    sp.mkdir(parents=True)
    (sp / "sitecustomize.py").write_text(
        "# Durable guard\n"
        "import sys\n"
        f'_SRC = "{src_line}"\n'
        "if _SRC not in sys.path:\n"
        "    sys.path.insert(0, _SRC)\n",
        encoding="utf-8",
    )
    (sp / "_editable_impl_vault_librarian.pth").write_text(src_line + "\n", encoding="utf-8")
    return tmp


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
        assert name not in src, f"relink must be stdlib-only; found {name}"


def test_rewrites_sitecustomize_and_pth(tmp_path: Path) -> None:
    dead = "/nonexistent/old-vault/vault-librarian/src"
    live = tmp_path / "live-src"
    live.mkdir()
    (live / "vault_librarian").mkdir()
    venv = make_venv(tmp_path / "venv", dead)

    result = run_relink("--venv", str(venv), "--src", str(live))
    assert result.returncode == 0, result.stderr
    assert "rewrote" in result.stdout.lower() or "ok" in result.stdout.lower()

    sc = (venv / "lib" / "python3.12" / "site-packages" / "sitecustomize.py").read_text(
        encoding="utf-8"
    )
    assert dead not in sc
    assert str(live.resolve()) in sc

    pth = (
        venv / "lib" / "python3.12" / "site-packages" / "_editable_impl_vault_librarian.pth"
    ).read_text(encoding="utf-8")
    assert pth.strip() == str(live.resolve())


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    dead = "/old/path/src"
    live = tmp_path / "src"
    live.mkdir()
    venv = make_venv(tmp_path / "venv", dead)

    result = run_relink("--venv", str(venv), "--src", str(live), "--dry-run")
    assert result.returncode == 0, result.stderr
    sc = (venv / "lib" / "python3.12" / "site-packages" / "sitecustomize.py").read_text(
        encoding="utf-8"
    )
    assert f'_SRC = "{dead}"' in sc


def test_missing_venv_exits_1(tmp_path: Path) -> None:
    result = run_relink("--venv", str(tmp_path / "nope"), "--src", str(tmp_path))
    assert result.returncode == 1
    assert "vault-relink-venv: error:" in result.stderr


def test_missing_src_exits_1(tmp_path: Path) -> None:
    venv = make_venv(tmp_path / "venv", "/old")
    result = run_relink("--venv", str(venv), "--src", str(tmp_path / "missing-src"))
    assert result.returncode == 1
    assert "vault-relink-venv: error:" in result.stderr


def test_idempotent_when_already_correct(tmp_path: Path) -> None:
    live = tmp_path / "src"
    live.mkdir()
    venv = make_venv(tmp_path / "venv", str(live.resolve()))
    result = run_relink("--venv", str(venv), "--src", str(live))
    assert result.returncode == 0, result.stderr
    assert "already" in result.stdout.lower() or "ok" in result.stdout.lower()
