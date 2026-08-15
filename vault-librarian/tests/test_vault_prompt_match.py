"""Tests for bin/vault-prompt-match — just-in-time vault surfacing.

Deterministic title overlap. No embedding model. Quiet when nothing hits.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "bin" / "vault-prompt-match"


def run_match(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=10,
    )


def make_tree(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    sources = root / "vault-personal" / "sources"
    wiki = root / "vault-personal" / "wiki" / "practices"
    sources.mkdir(parents=True)
    wiki.mkdir(parents=True)
    (sources / "2026-08-13-librarian-import.md").write_text(
        "---\n"
        "title: librarian import dies after repo move leftover pth\n"
        "via: lesson-capture\n"
        "tags: [macos, volatile]\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )
    (sources / "2026-08-01-not-a-lesson.md").write_text(
        "---\ntitle: a random article about cats\nvia: vault-capture\n---\n\nbody\n",
        encoding="utf-8",
    )
    (wiki / "librarian-import-dies-after-repo-move.md").write_text(
        "---\n"
        "id: wiki-librarian-import-dies-after-repo-move\n"
        "title: librarian import dies after repo move leftover pth\n"
        "---\n\n# librarian import dies after repo move leftover pth\n",
        encoding="utf-8",
    )
    return root


class TestPromptMatch:
    def test_symptom_hits_lesson_and_wiki(self, tmp_path: Path) -> None:
        root = make_tree(tmp_path)
        r = run_match("--root", str(root), "--prompt", "vault librarian import dies after I moved the repo")
        assert r.returncode == 0
        assert "librarian import dies" in r.stdout
        assert "LESSON" in r.stdout
        assert "WIKI" in r.stdout

    def test_greeting_is_quiet(self, tmp_path: Path) -> None:
        root = make_tree(tmp_path)
        r = run_match("--root", str(root), "--prompt", "ok")
        assert r.returncode == 0
        assert r.stdout == ""

    def test_two_token_overlap_is_quiet(self, tmp_path: Path) -> None:
        root = make_tree(tmp_path)
        r = run_match("--root", str(root), "--prompt", "I moved the repo folder yesterday")
        assert r.returncode == 0
        assert r.stdout == ""

    def test_unrelated_technical_prompt_is_quiet(self, tmp_path: Path) -> None:
        root = make_tree(tmp_path)
        r = run_match("--root", str(root), "--prompt", "how do I center a div in CSS")
        assert r.returncode == 0
        assert r.stdout == ""

    def test_non_lesson_source_not_listed(self, tmp_path: Path) -> None:
        root = make_tree(tmp_path)
        r = run_match("--root", str(root), "--prompt", "article about cats and kittens")
        assert "cats" not in r.stdout

    def test_hook_mode_emits_additional_context_json(self, tmp_path: Path) -> None:
        root = make_tree(tmp_path)
        payload = json.dumps(
            {
                "prompt": "ModuleNotFoundError vault_librarian after repo move leftover pth",
                "cwd": str(root),
            }
        )
        r = run_match("--root", str(root), "--hook", stdin=payload)
        assert r.returncode == 0
        data = json.loads(r.stdout)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert data["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "librarian import" in ctx
        assert "/recall" in ctx
        assert "Vault title overlap" in ctx
        assert "Do not re-derive" not in ctx

    def test_hook_mode_quiet_on_miss(self, tmp_path: Path) -> None:
        root = make_tree(tmp_path)
        r = run_match(
            "--root",
            str(root),
            "--hook",
            stdin=json.dumps({"prompt": "thanks", "cwd": str(root)}),
        )
        assert r.returncode == 0
        assert r.stdout == ""
