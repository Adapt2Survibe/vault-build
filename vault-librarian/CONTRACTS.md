# vault-librarian — Interface Contracts (Phase 1)

[Librarian README](README.md) · [Install](../INSTALL.md) · [Factory README](../README.md)

**Status:** LOCKED for the 2026-06-12 autonomous build. Builders implement against this spec exactly. Deviations require a written note in the builder's return payload.
**Amended 2026-06-12 (post-review-wall):** see the "Review-wall amendments" section at the bottom — those clauses supersede conflicting text above them.
**Scope:** Phase 1, Mac-local, personal vault. stdio MCP transport. sqlite-vec backend.

This document is the authority for module boundaries. If a docstring stub in an existing file disagrees with this spec, this spec wins (stubs predate design).

---

## Module map

All modules live in `src/vault_librarian/`:

| Module | Owns | Depends on |
|---|---|---|
| `config.py` | YAML config loading + validation | — |
| `chunker.py` | front-matter parsing, markdown chunking | — |
| `embedder.py` | sentence-transformers wrapper, nomic task prefixes | — |
| `store.py` | sqlite-vec persistence + KNN search | numpy, sqlite_vec |
| `indexer.py` | vault file discovery, incremental reindex | config, chunker, embedder, store |
| `tools.py` | search_wiki / search_sources / get_page logic | config, store, embedder |
| `server.py` | FastMCP wiring (stdio) | config, tools, store, embedder |
| `cli.py` | argparse entry point: serve / reindex / status | all of the above |

Test files map 1:1: `tests/test_config.py`, `tests/test_chunker.py`, `tests/test_store.py`, `tests/test_embedder.py`, `tests/test_indexer.py`, `tests/test_tools.py`, `tests/test_server.py`, `tests/test_cli.py`, plus `tests/test_capture.py` for `bin/vault-capture`.

Shared test fixtures live in `tests/conftest.py` (already written — do not modify): `FakeEmbedder`, `make_vault_tree`. `conftest.py` deliberately imports nothing from `vault_librarian` so test collection never breaks on a half-built package.

---

## config.py

```python
class VaultSection(BaseModel):      # vault:
    name: str                        #   personal | company
    root: Path                       #   "~" expanded via expanduser, resolved absolute

class EmbeddingSection(BaseModel):  # embedding:
    model: str = "nomic-ai/nomic-embed-text-v1.5"
    dimensions: int = 768
    device: str = "cpu"              # cpu | mps | cuda

class StoreSection(BaseModel):      # store:
    backend: str = "sqlite-vec"
    path: Path                       # "~" expanded, absolute

class ServerSection(BaseModel):     # server: (unused until Phase 3 HTTP; carried for config compat)
    host: str = "127.0.0.1"
    port: int = 8001

class SearchSection(BaseModel):     # search:
    default_top_k_wiki: int = 5
    default_top_k_sources: int = 3
    max_excerpt_words: int = 15

class IngestSection(BaseModel):     # ingest:
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 64

class Config(BaseModel):
    vault: VaultSection
    embedding: EmbeddingSection
    store: StoreSection
    server: ServerSection = ServerSection()
    search: SearchSection = SearchSection()
    ingest: IngestSection = IngestSection()

def load_config(path: str | Path) -> Config
```

- `load_config` reads YAML (`yaml.safe_load`), validates via pydantic, expands `~` in both paths.
- Missing file → `FileNotFoundError` with the path in the message.
- Invalid YAML / failed validation → let the underlying exception propagate (callers handle).
- Sections `server`, `search`, `ingest` are optional in the YAML (defaults above). `vault`, `embedding`, `store` are required.

## chunker.py

```python
@dataclass
class Chunk:
    text: str       # chunk body, stripped
    heading: str    # breadcrumb of nearest enclosing headings, " > "-joined, "" if none
    pos: int        # 0-based sequence within the document

def parse_front_matter(text: str) -> tuple[dict, str]
def chunk_markdown(body: str, chunk_size_tokens: int = 512, overlap_tokens: int = 64) -> list[Chunk]
def estimate_tokens(text: str) -> int    # max(1, round(word_count * 1.33)); empty/whitespace -> 0
```

- `parse_front_matter`: if text starts with `---\n`, parse up to the closing `---` line with `yaml.safe_load`. Returns `(metadata_dict, body_after_front_matter)`. No front matter or malformed YAML → `({}, original_text)` (malformed front matter must not kill indexing; the body still gets indexed).
- `chunk_markdown` algorithm (deterministic):
  1. Split body into blocks on heading lines (`^#{1,6} `) and blank-line paragraph boundaries. Track heading breadcrumb (e.g. `## Setup` under `# Install` → `Install > Setup`). Heading text itself is context, not chunk body.
  2. Pack consecutive blocks into a chunk while `estimate_tokens(chunk + next_block) <= chunk_size_tokens`.
  3. On emit, start the next chunk with the trailing blocks of the previous one up to `overlap_tokens` (block-granular overlap; only when more blocks remain).
  4. A single block larger than `chunk_size_tokens` is hard-split on whitespace into `chunk_size_tokens`-sized pieces.
  5. Whitespace-only blocks are dropped. Empty body → `[]`.
- Fenced code blocks (``` ... ```) are treated as single atomic blocks (never split mid-fence; heading detection suspended inside fences).

## embedder.py

```python
class Embedder:
    def __init__(self, model_name: str, device: str = "cpu", _loader: Callable | None = None)
    def embed_documents(self, texts: list[str]) -> np.ndarray   # (n, dim) float32, L2-normalized
    def embed_query(self, text: str) -> np.ndarray              # (dim,)  float32, L2-normalized
```

- Model loads lazily on first embed call (server start must stay fast for the stdio handshake). `_loader` is injectable for tests; default loader: `SentenceTransformer(model_name, device=device, trust_remote_code=True)` — nomic requires `trust_remote_code`.
- **nomic task prefixes (retrieval quality depends on these):** when `"nomic"` is in `model_name` (case-insensitive), prepend `"search_document: "` to every document text and `"search_query: "` to query text before encoding. Other models get no prefix.
- Encode with `normalize_embeddings=True`, `batch_size=32`, `show_progress_bar=False`. Cast to float32.
- `embed_documents([])` → shape `(0,)` array, no model load.

## store.py

```python
@dataclass
class Hit:
    doc_id: str
    heading: str
    text: str
    pos: int
    score: float    # cosine similarity in [0,1] (vectors are normalized): 1 - dist^2 / 2, rounded 4dp

class Store:
    def __init__(self, db_path: str | Path, dimensions: int = 768)
    def upsert_document(self, kind: str, doc_id: str, file_path: str, content_hash: str,
                        chunks: list[Chunk], embeddings: np.ndarray) -> int   # chunk count
    def delete_document(self, doc_id: str) -> None
    def search(self, kind: str, query_embedding: np.ndarray, top_k: int) -> list[Hit]
    def indexed_state(self) -> dict[str, str]        # doc_id -> content_hash
    def get_document(self, doc_id: str) -> dict | None  # {doc_id, kind, file_path, content_hash, indexed_at}
    def counts(self) -> dict                          # {"wiki_docs", "source_docs", "wiki_chunks", "source_chunks"}
    def last_indexed_at(self) -> str | None           # max(indexed_at) ISO string or None
    def close(self) -> None                           # also __enter__/__exit__
```

- Schema (created idempotently on open; parent dir created if missing):
  ```sql
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
  CREATE VIRTUAL TABLE IF NOT EXISTS vec_wiki   USING vec0(embedding float[<dim>]);
  CREATE VIRTUAL TABLE IF NOT EXISTS vec_source USING vec0(embedding float[<dim>]);
  ```
- Two vec tables (one per kind) so kind filtering is exact, not post-KNN. Vec rowid == `chunks.chunk_id`.
- Connection: `enable_load_extension(True)` → `sqlite_vec.load(conn)` → disable; `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000` (a reindex may run while the server reads).
- `upsert_document` runs in one transaction: delete old chunk rows + vec rows for `doc_id`, REPLACE the document row (`indexed_at` = current UTC ISO), insert chunks + vectors. Validates `len(chunks) == len(embeddings)` and embedding dim == `dimensions` (raise `ValueError` — a dim mismatch means wrong model and must fail loudly, not corrupt the index). Embeddings stored as float32 bytes (`np.asarray(v, dtype=np.float32).tobytes()`). Normalize defensively before storing (idempotent on already-normalized vectors).
- `search`: KNN on the kind's vec table — `SELECT rowid, distance FROM vec_<kind> WHERE embedding MATCH ? AND k = ?` — join `chunks` on `chunk_id` for metadata. Empty index → `[]` (must not raise).
- Zero rows for unknown `kind` → `ValueError` (catches typos at the boundary).

## indexer.py

```python
@dataclass
class IndexStats:
    scanned: int; indexed: int; skipped: int; deleted: int; chunks: int; seconds: float
    def to_dict(self) -> dict

def discover(config: Config) -> list[DocRef]   # DocRef: dataclass(kind, doc_id, path)
def reindex(config: Config, store: Store, embedder, force: bool = False,
            only: list[Path] | None = None) -> IndexStats
```

- Discovery:
  - wiki: `<vault.root>/wiki/**/*.md`, excluding any file named `_index.md`. `doc_id` = posix relative path from vault root, no `.md` suffix (e.g. `wiki/tools/engramme`) — matches the `[[wiki/...]]` citation format in the slash commands.
  - sources: `<vault.root>/sources/*.md`, excluding `_index.md`. `doc_id` = front-matter `id` if present, else `src-<filename stem>`.
  - journal/ and inbox/ are NOT indexed in Phase 1 (locked scope).
- `content_hash` = sha256 of raw file bytes, hexdigest.
- `reindex`:
  1. discover → for each doc, skip when hash matches `store.indexed_state()` and not `force`.
  2. changed/new docs: `parse_front_matter` → `chunk_markdown` (sizes from `config.ingest`) → `embedder.embed_documents` → `store.upsert_document`. Documents with zero chunks after parsing (empty body) are counted in `skipped`, not indexed.
  3. docs present in store but missing on disk → `store.delete_document` (counted in `deleted`). **Skip this deletion pass entirely when `only` is given** (a single-file reindex must never garbage-collect the rest of the index).
  4. `only`: restrict processing to docs whose path is in the given list (resolve both sides before comparing).
- Embed per-document (a failed file affects only that file). A file that errors (unreadable, embed failure) is logged to stderr and skipped — reindex completes the rest and reports it; one bad file must not abort the sweep. Add an `errors: int` field to IndexStats if implementing this (preferred) — document the choice in the builder note.

## tools.py

```python
def search_wiki(config, store, embedder, query: str, top_k: int | None = None) -> dict
def search_sources(config, store, embedder, query: str, top_k: int | None = None) -> dict
def get_page(config, store, page_id: str) -> dict
```

- `search_wiki` → `{"results": [{"page_id", "heading", "excerpt", "score"}, ...]}` ordered by score desc. `top_k` defaults from `config.search.default_top_k_wiki`. Excerpt = chunk text capped at **60 words** (wiki is our own synthesis layer) with `" …"` appended when truncated.
- `search_sources` → `{"results": [{"source_id", "heading", "excerpt", "score"}, ...]}`. `top_k` default from config. Excerpt capped at **`config.search.max_excerpt_words` (15) words** — this is the vault contract: never quote sources longer than that. Cap is mandatory, applied in tools.py (defense in depth, regardless of what callers render).
- Both: empty/whitespace query → `{"results": [], "note": "empty query"}` without touching the embedder. Empty index → `{"results": [], "note": "index is empty — run: vault-librarian reindex"}`.
- `get_page`:
  1. Resolve `page_id` → store lookup (`get_document`) → `file_path`. Fallback: treat `page_id` as a vault-root-relative path, with/without `.md` appended.
  2. **Path traversal guard (security, mandatory):** the resolved absolute path MUST satisfy `resolved.is_relative_to(config.vault.root.resolve())`; otherwise raise `ValueError("page_id escapes vault root")`. Test this with `../` and absolute-path inputs.
  3. Return `{"page_id", "file_path" (vault-relative), "content"}`. Content capped at 100,000 chars with `"truncated": true` flag when capped. Missing page → `{"error": "not found", "page_id": ...}` (not an exception — MCP callers need a graceful miss).

## server.py

```python
def build_server(config: Config, embedder_factory=None, store_factory=None) -> FastMCP
def serve(config: Config) -> None     # build_server(...).run()  — stdio transport
```

- `FastMCP(name="vault-librarian")`. Three `@mcp.tool()` functions delegating to `tools.py`, with docstrings (these become the tool descriptions Claude sees — write them as one-line imperative descriptions + arg meanings).
- Embedder and Store are created lazily/once (module of closures or small holder class). `embedder_factory`/`store_factory` injectable for tests (default: real `Embedder(...)` / `Store(...)` from config).
- Tool signatures: `search_wiki(query: str, top_k: int = 5)`, `search_sources(query: str, top_k: int = 3)`, `get_page(page_id: str)`. Defaults in the MCP layer mirror config defaults; passing explicit top_k overrides.
- No prints to stdout anywhere in server path (stdio transport: stdout belongs to the MCP protocol). Diagnostics → stderr.

## cli.py

```python
def main(argv: list[str] | None = None) -> int
```

Subcommands (argparse):
- `serve --config PATH` → `server.serve(config)`.
- `reindex --config PATH [--force] [--only PATH ...]` → runs indexer.reindex with a real Embedder + Store, prints `IndexStats.to_dict()` as a single JSON line to **stdout** (machine-readable — Scribe and the stamp pipeline parse this), human log lines to stderr. Exit 0 on success, 2 on error (config missing, vault root missing).
- `status --config PATH` → JSON line: `{"db": str, "counts": {...}, "last_indexed_at": str|None}`. Works without loading the embedding model (must be fast).
- No subcommand → print usage to stderr, exit 2.

## bin/vault-capture  (repo-root `bin/`, NOT inside vault-librarian)

Zero-dependency Python 3 script (stdlib only, no venv — capture must work from any shell instantly). `#!/usr/bin/env python3`, executable bit set.

```
vault-capture <text words...>        # quick text note
vault-capture <existing-file-path>   # stage a file copy
vault-capture <http(s)://url>        # stage a URL for fetch-on-ingest
vault-capture -                      # read note body from stdin
options: --vault personal|company (default personal), --title TEXT, --tags a,b,c
```

- Vault root: `$VAULT_ROOT` env override (tests use this), default = this checkout (the directory that contains `bin/vault-capture`). Inbox = `<root>/vault-<vault>/inbox/`. Error (exit 1, stderr) if inbox dir missing.
- Output filename: `<YYYY-MM-DD-HHMMSS>-<slug>.md` (local time). Slug: from `--title`, else first ~6 words of text / URL host+path / original filename; lowercase, `[a-z0-9-]` only, max 40 chars, no leading/trailing `-`. Collision → append `-2`, `-3`, ….
- Text/stdin/URL captures get YAML front matter: `captured` (ISO local timestamp), `via: vault-capture`, `type_hint` (`note` | `website`), plus `title`, `tags`, `url` when present. Body = the text (URL captures: body line `(URL capture — fetch on ingest)`).
- File capture: copy the file unmodified to `inbox/<ts>-<original-name>` (`shutil.copy2`); never alter or move the original. Non-md files are copied as-is (Scribe handles classification).
- On success print exactly one line to stdout: the absolute path of the created inbox file. Errors → stderr + exit 1.
- Empty input (no args, empty stdin) → error exit 1 ("nothing to capture").

---

## Cross-cutting rules

- Type hints everywhere; ruff-clean under the repo config (line-length 100, `E,F,I,N,W,UP`).
- No prints to stdout except where a contract says so (CLI JSON lines, capture path line).
- Tests use `FakeEmbedder` from conftest — never load sentence-transformers in unit tests (no network, no 500MB model). The one real-model integration test is written later, marked `@pytest.mark.slow`, excluded by default (`-m "not slow"` is configured in pyproject).
- Front-matter schemas for vault content (Scribe writes these; indexer/tools read them):

  Source file (`sources/YYYY-MM-DD-<slug>.md`):
  ```yaml
  ---
  id: src-YYYY-MM-DD-<slug>
  title: <human title>
  type: website | article | pdf | transcript | note | export | email
  ingested: YYYY-MM-DD
  url: <original url, when applicable>
  tags: [optional, list]
  via: vault-capture | ingest | scribe-sweep
  ---
  ```

  Wiki page (`wiki/<section>/<slug>.md`):
  ```yaml
  ---
  id: wiki-<slug>
  title: <human title>
  created: YYYY-MM-DD
  tags: [needs-synthesis]      # removed at graduation
  sources: [src-YYYY-MM-DD-<slug>]
  last_synthesized: null        # date set by Surgeon
  ---
  ```

---

## Review-wall amendments (2026-06-12, supersede conflicting clauses above)

From the /rev code review (run `20260612-vault-phase1`); each is implemented and pinned by `tests/test_review_fixes.py`:

1. **IndexStats carries `errors: int` unconditionally** — it is part of the reindex JSON line schema; consumers (Scribe, stamps) must read it, not just the exit code.
2. **reindex exit codes:** 0 = success (including partial-failure runs with some `errors`); **1 = total failure** (`errors > 0`, `indexed == 0`, and every non-skipped document errored — e.g. dead model load); 2 = operational error (missing/invalid config, missing vault root). The CLI also exits 2 on malformed YAML / schema-invalid / empty configs (clean stderr line, no traceback).
3. **Source front-matter `id` must match `^src-`** — anything else falls back to `src-<stem>` with a stderr warning (an arbitrary id can collide with another doc_id and evict it). Duplicate source ids within one discovery pass: first file keeps the id, later ones fall back to `src-<stem>` with a stderr warning.
4. **Deletion-pass safety:** a document whose file still exists on disk is never garbage-collected even when its doc_id is no longer discovered (id drift / transient unreadability); an empty discovery result with a non-empty index skips the deletion pass entirely.
5. **Empty-body documents are upserted with zero chunks** (still counted in `skipped`): previously-indexed chunks are cleared and the stored hash advances.
6. **Excerpts are char-bounded as well as word-capped:** `max_words * 40` chars — whitespace-poor text (base64, CJK) must not ride through the word cap.
7. **Embeddings must be finite:** NaN/Inf rows or query vectors raise `ValueError` (the dim-mismatch fail-loudly rule extended to the same corruption class).
8. **Embedder hardening:** `revision` config field pins the model repo commit (trust_remote_code supply chain); a failed model load is cached and re-raised instantly; model load + encode run under `redirect_stdout(stderr)` (stdio protocol protection).
9. **get_page surface is the whole vault root** (journal/, inbox/, _maintenance/ included — e.g. Surgeon backups), documented as a feature for the single-user vault. Misses return a `hint`; unreadable files return `{"error": "unreadable: ..."}`.
10. **Search degrades gracefully on SQLITE_BUSY** (concurrent reindex): `{"results": [], "note": "index temporarily unavailable …"}` instead of a raw ToolError.
11. **vault-capture:** exclusive-create (`open 'x'`) for all inbox writes — same-second same-slug captures can never overwrite; a path-like argument that is not an existing file fails loudly (exit 1) instead of becoming a text note; argparse usage errors exit 2 (the contracted exit 1 covers the enumerated capture errors).

---

## Phone-channel amendments (2026-06-12, second review wall)

12. **`via` is parameterizable:** `bin/vault-capture` gained `--via <label>` (default `vault-capture`); the value is YAML-quoted so hostile labels cannot mint front-matter keys. The source front-matter `via` enum becomes `vault-capture | phone | ingest | scribe-sweep` — a list of known values, not a closed set; Scribe passes the inbox `via` through to the source unchanged.
13. **Phone-channel stamp contract:** `bin/vault-phone-watcher` writes `vault-personal/_maintenance/phone-channel-stamp.json` atomically every run: `{ran_at, ok, reason, processed, failed, pending, quarantined, cadence_seconds, next_run_by}`. `pending` includes `.icloud` placeholders (captures stuck mid-sync must be visible). Consumers compare the CURRENT time to `next_run_by` (parse as ISO datetimes). The watcher's lock lives outside iCloud-synced space (`/tmp/vault-phone-watcher.lock`, `VAULT_LOCK_FILE` override); subprocess calls carry timeouts; symlinks in the drop folder are quarantined unread (security); a top-level handler stamps `ok: false` with the crash reason on unexpected errors.
