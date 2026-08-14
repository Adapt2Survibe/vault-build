"""sqlite-vec persistence + KNN search for the vault index.

Two vec0 virtual tables (vec_wiki / vec_source) keep kind filtering exact:
a KNN query runs against one kind's table only, never post-filters. Vec rowids
mirror chunks.chunk_id so metadata joins are direct.

Chunks are duck-typed (any object with .text/.heading/.pos) — this module must
not import chunker.py (CONTRACTS.md: sibling modules may not exist yet).
"""

from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import numpy as np
import sqlite_vec

_KINDS = ("wiki", "source")

# SQLite cannot parameterize table names; per-kind statements are fixed literals
# keyed by validated kind (never string-built from input).
_VEC_DELETE_SQL = {
    "wiki": "DELETE FROM vec_wiki WHERE rowid = ?",
    "source": "DELETE FROM vec_source WHERE rowid = ?",
}
_VEC_INSERT_SQL = {
    "wiki": "INSERT INTO vec_wiki (rowid, embedding) VALUES (?, ?)",
    "source": "INSERT INTO vec_source (rowid, embedding) VALUES (?, ?)",
}
_VEC_SEARCH_SQL = {
    "wiki": "SELECT rowid, distance FROM vec_wiki WHERE embedding MATCH ? AND k = ?",
    "source": "SELECT rowid, distance FROM vec_source WHERE embedding MATCH ? AND k = ?",
}


class ChunkLike(Protocol):
    """Structural stand-in for chunker.Chunk (store must not import chunker)."""

    text: str
    heading: str
    pos: int


@dataclass
class Hit:
    doc_id: str
    heading: str
    text: str
    pos: int
    score: float  # cosine similarity in [0,1] (vectors are normalized): 1 - dist^2 / 2, 4dp


def _check_kind(kind: str) -> None:
    if kind not in _KINDS:
        raise ValueError(f"unknown kind {kind!r}: expected one of {_KINDS}")


def _normalize(vectors: np.ndarray) -> np.ndarray:
    """L2-normalize rows defensively (idempotent on already-normalized input)."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (vectors / norms).astype(np.float32)


class Store:
    """sqlite-vec backed vector store for wiki and source chunks."""

    def __init__(self, db_path: str | Path, dimensions: int = 768):
        self._db_path = Path(db_path)
        self._dimensions = dimensions
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._create_schema()
        self._check_existing_dimensions()

    def _create_schema(self) -> None:
        dim = int(self._dimensions)
        self._conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS documents (
              doc_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL CHECK(kind IN ('wiki','source')),
              file_path TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              indexed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
              chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
              doc_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              heading TEXT NOT NULL DEFAULT '',
              pos INTEGER NOT NULL,
              text TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_wiki   USING vec0(embedding float[{dim}]);
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_source USING vec0(embedding float[{dim}]);
            """
        )
        self._conn.commit()

    def _check_existing_dimensions(self) -> None:
        """Fail at open if an existing db was built with a different dim.

        CREATE VIRTUAL TABLE IF NOT EXISTS does not rewrite a live vec0 table,
        so a model/dim change would otherwise surface as confusing per-document
        upsert errors. Residual #1 from the 2026-06-12 review wall.
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='vec_wiki'"
        ).fetchone()
        if not row or not row[0]:
            raise ValueError("vec_wiki table missing after schema init")
        match = re.search(r"float\[(\d+)\]", row[0])
        if match is None:
            raise ValueError(f"cannot parse vec_wiki dimensions from {row[0]!r}")
        existing = int(match.group(1))
        if existing != int(self._dimensions):
            raise ValueError(
                f"store dimensions changed: db has float[{existing}], "
                f"config has {self._dimensions} — delete the db and reindex"
            )

    def _delete_doc_rows(self, doc_id: str) -> None:
        """Remove a document's chunk + vec rows (caller manages the transaction)."""
        old = self._conn.execute(
            "SELECT chunk_id, kind FROM chunks WHERE doc_id = ?", (doc_id,)
        ).fetchall()
        for chunk_id, old_kind in old:
            self._conn.execute(_VEC_DELETE_SQL[old_kind], (chunk_id,))
        self._conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

    def upsert_document(
        self,
        kind: str,
        doc_id: str,
        file_path: str,
        content_hash: str,
        chunks: list[ChunkLike],
        embeddings: np.ndarray,
    ) -> int:
        """Replace a document's chunks + vectors atomically. Returns the chunk count."""
        _check_kind(kind)
        emb = np.asarray(embeddings, dtype=np.float32)
        if emb.ndim == 1 and emb.size == 0:
            emb = emb.reshape(0, self._dimensions)
        if emb.ndim != 2 or emb.shape[0] != len(chunks):
            raise ValueError(
                f"embeddings shape {emb.shape} does not match {len(chunks)} chunks"
                f" for doc {doc_id!r}"
            )
        if len(chunks) and emb.shape[1] != self._dimensions:
            raise ValueError(
                f"embedding dim {emb.shape[1]} != store dim {self._dimensions}"
                f" for doc {doc_id!r} — wrong model?"
            )
        if len(chunks) and not np.isfinite(emb).all():
            # NaN/Inf rows would persist and poison every later KNN — fail loudly.
            raise ValueError(f"embeddings contain non-finite values for doc {doc_id!r}")
        if len(chunks):
            emb = _normalize(emb)

        indexed_at = datetime.now(UTC).isoformat()
        with self._conn:
            self._delete_doc_rows(doc_id)
            self._conn.execute(
                "INSERT OR REPLACE INTO documents"
                " (doc_id, kind, file_path, content_hash, indexed_at) VALUES (?, ?, ?, ?, ?)",
                (doc_id, kind, file_path, content_hash, indexed_at),
            )
            for chunk, vec in zip(chunks, emb):
                cur = self._conn.execute(
                    "INSERT INTO chunks (doc_id, kind, heading, pos, text) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, kind, chunk.heading, chunk.pos, chunk.text),
                )
                self._conn.execute(
                    _VEC_INSERT_SQL[kind],
                    (cur.lastrowid, np.asarray(vec, dtype=np.float32).tobytes()),
                )
        return len(chunks)

    def delete_document(self, doc_id: str) -> None:
        with self._conn:
            self._delete_doc_rows(doc_id)
            self._conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))

    def search(self, kind: str, query_embedding: np.ndarray, top_k: int) -> list[Hit]:
        """KNN search within one kind. Empty index returns [] (never raises)."""
        _check_kind(kind)
        q = np.asarray(query_embedding, dtype=np.float32).reshape(-1)
        if q.shape[0] != self._dimensions:
            raise ValueError(
                f"query embedding dim {q.shape[0]} != store dim {self._dimensions}"
            )
        if not np.isfinite(q).all():
            raise ValueError("query embedding contains non-finite values")
        norm = float(np.linalg.norm(q))
        if norm > 0.0:
            q = (q / norm).astype(np.float32)
        rows = self._conn.execute(
            _VEC_SEARCH_SQL[kind], (q.tobytes(), int(top_k))
        ).fetchall()
        hits: list[Hit] = []
        for rowid, dist in rows:
            meta = self._conn.execute(
                "SELECT doc_id, heading, text, pos FROM chunks WHERE chunk_id = ?", (rowid,)
            ).fetchone()
            if meta is None:  # orphan vec row — skip rather than fabricate a hit
                print(
                    f"vault-librarian: warning: orphan vec row {rowid} in vec_{kind}"
                    " — index may be corrupt; run: vault-librarian reindex --force",
                    file=sys.stderr,
                )
                continue
            score = round(1.0 - (dist * dist) / 2.0, 4)
            hits.append(
                Hit(doc_id=meta[0], heading=meta[1], text=meta[2], pos=meta[3], score=score)
            )
        return hits

    def has_chunks(self, kind: str) -> bool:
        """Cheap per-kind emptiness probe (the search hot path's empty-index guard)."""
        _check_kind(kind)
        row = self._conn.execute("SELECT 1 FROM chunks WHERE kind = ? LIMIT 1", (kind,)).fetchone()
        return row is not None

    def indexed_state(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT doc_id, content_hash FROM documents").fetchall()
        return dict(rows)

    def get_document(self, doc_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT doc_id, kind, file_path, content_hash, indexed_at"
            " FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "doc_id": row[0],
            "kind": row[1],
            "file_path": row[2],
            "content_hash": row[3],
            "indexed_at": row[4],
        }

    def counts(self) -> dict:
        out = {"wiki_docs": 0, "source_docs": 0, "wiki_chunks": 0, "source_chunks": 0}
        for kind, n in self._conn.execute("SELECT kind, count(*) FROM documents GROUP BY kind"):
            out[f"{kind}_docs"] = n
        for kind, n in self._conn.execute("SELECT kind, count(*) FROM chunks GROUP BY kind"):
            out[f"{kind}_chunks"] = n
        return out

    def last_indexed_at(self) -> str | None:
        return self._conn.execute("SELECT max(indexed_at) FROM documents").fetchone()[0]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
