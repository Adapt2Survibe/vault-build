"""Tests for bin/vault-graph — deterministic wiki backlinks / broken links.

The 2026-07-21 architecture gauntlet killed an auto-hub pipeline. This is the
floor that still earns the "I imagined my own wiki" feel: what links here,
what's orphaned, what's broken, which slugs collide.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "vault-graph"


def run_graph(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def write_page(wiki: Path, rel: str, body: str, title: str | None = None) -> Path:
    path = wiki / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    slug = path.stem
    title = title or slug
    path.write_text(
        f"---\nid: wiki-{slug}\ntitle: {title}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_script_exists_executable_stdlib_only() -> None:
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)
    src = SCRIPT.read_text(encoding="utf-8")
    assert src.startswith("#!/usr/bin/env python3")
    for banned in ("import yaml", "import requests", "vault_librarian", "import numpy"):
        assert banned not in src


def test_json_reports_backlinks_broken_orphans_collisions(tmp_path: Path) -> None:
    vault = tmp_path / "vault-personal"
    wiki = vault / "wiki"
    write_page(
        wiki,
        "concepts/hub.md",
        "See [[wiki/tools/engramme]] and [[orphan-page]] and [[engramme]].",
    )
    write_page(wiki, "tools/engramme.md", "Root idea. Also [[missing-page]].")
    write_page(wiki, "practices/engramme.md", "Same stem, different section.")
    write_page(wiki, "concepts/lonely.md", "No inbound, no outbound.")
    (wiki / "_index.md").write_text("# skip me\n", encoding="utf-8")

    result = run_graph("--root", str(tmp_path), "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    pages = {p["id"] for p in data["pages"]}
    assert "wiki/concepts/hub" in pages
    assert "wiki/tools/engramme" in pages
    assert "wiki/_index" not in pages

    broken = {(b["from"], b["to"]) for b in data["broken"]}
    assert ("wiki/concepts/hub", "orphan-page") in broken
    assert ("wiki/tools/engramme", "missing-page") in broken

    collisions = {c["stem"] for c in data["collisions"]}
    assert "engramme" in collisions

    orphans = set(data["orphans"])
    assert "wiki/concepts/lonely" in orphans
    assert "wiki/concepts/hub" in orphans  # nothing points at hub
    assert "wiki/tools/engramme" not in orphans

    engramme = next(p for p in data["pages"] if p["id"] == "wiki/tools/engramme")
    assert "wiki/concepts/hub" in engramme["backlinks"]


def test_page_mode_prints_backlinks(tmp_path: Path) -> None:
    vault = tmp_path / "vault-personal"
    wiki = vault / "wiki"
    write_page(wiki, "concepts/hub.md", "See [[wiki/tools/engramme]].")
    write_page(wiki, "tools/engramme.md", "Root.")

    result = run_graph("--root", str(tmp_path), "--page", "engramme")
    assert result.returncode == 0, result.stderr
    assert "wiki/concepts/hub" in result.stdout
    assert "wiki/tools/engramme" in result.stdout


def test_ignores_links_inside_fences(tmp_path: Path) -> None:
    vault = tmp_path / "vault-personal"
    write_page(
        vault / "wiki",
        "concepts/fenced.md",
        "Real [[wiki/tools/engramme]].\n\n```\n[[not-a-link]]\n```\n",
    )
    write_page(vault / "wiki", "tools/engramme.md", "Root.")

    result = run_graph("--root", str(tmp_path), "--json")
    data = json.loads(result.stdout)
    broken_targets = {b["to"] for b in data["broken"]}
    assert "not-a-link" not in broken_targets


def test_missing_wiki_exits_1(tmp_path: Path) -> None:
    result = run_graph("--root", str(tmp_path))
    assert result.returncode == 1
    assert "vault-graph: error:" in result.stderr


def test_list_prints_page_ids(tmp_path: Path) -> None:
    vault = tmp_path / "vault-personal"
    write_page(vault / "wiki", "tools/engramme.md", "Root.")
    write_page(vault / "wiki", "concepts/hub.md", "See [[engramme]].")
    result = run_graph("--root", str(tmp_path), "--list")
    assert result.returncode == 0, result.stderr
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert "wiki/tools/engramme" in lines
    assert "wiki/concepts/hub" in lines


def test_alias_and_heading_stripped(tmp_path: Path) -> None:
    vault = tmp_path / "vault-personal"
    write_page(
        vault / "wiki",
        "concepts/hub.md",
        "See [[wiki/tools/engramme|the tool]] and [[engramme#setup]].",
    )
    write_page(vault / "wiki", "tools/engramme.md", "Root.")

    result = run_graph("--root", str(tmp_path), "--json")
    data = json.loads(result.stdout)
    assert data["broken"] == []
    engramme = next(p for p in data["pages"] if p["id"] == "wiki/tools/engramme")
    assert engramme["backlinks"] == ["wiki/concepts/hub"]
