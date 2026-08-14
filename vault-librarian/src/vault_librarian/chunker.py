"""Front-matter parsing and markdown chunking for vault-librarian.

Deterministic chunking per CONTRACTS.md: split on headings + blank lines,
pack blocks up to a token budget, carry block-granular overlap between
chunks, hard-split oversized non-fence blocks. Fenced code blocks are
atomic (never split mid-fence; heading detection suspended inside).
"""

import re
from dataclasses import dataclass, field

import yaml

_TOKENS_PER_WORD = 1.33
_HEADING_RE = re.compile(r"^(#{1,6}) (.+)$")
_FM_CLOSE_RE = re.compile(r"^---\s*$")


@dataclass
class Chunk:
    text: str  # chunk body, stripped
    heading: str  # breadcrumb of nearest enclosing headings, " > "-joined, "" if none
    pos: int  # 0-based sequence within the document


def estimate_tokens(text: str) -> int:
    """Estimate token count: max(1, round(word_count * 1.33)); empty/whitespace -> 0."""
    word_count = len(text.split())
    if word_count == 0:
        return 0
    return max(1, round(word_count * _TOKENS_PER_WORD))


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Split YAML front matter from a markdown document.

    Returns (metadata_dict, body_after_front_matter). No front matter,
    unclosed delimiter, or malformed/non-mapping YAML -> ({}, original_text)
    so the body still gets indexed.
    """
    if not text.startswith(("---\n", "---\r\n")):
        return {}, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if _FM_CLOSE_RE.match(lines[i]):
            raw = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :])
            try:
                meta = yaml.safe_load(raw)
            except yaml.YAMLError:
                return {}, text
            if meta is None:  # empty front-matter block
                return {}, body
            if not isinstance(meta, dict):
                return {}, text
            return meta, body
    return {}, text  # opening delimiter never closed


@dataclass
class _Block:
    text: str
    breadcrumb: str
    is_fence: bool = False


@dataclass
class _BlockSplitter:
    """Line-by-line state machine: paragraphs, headings (breadcrumb), fences."""

    blocks: list[_Block] = field(default_factory=list)
    _crumbs: list[tuple[int, str]] = field(default_factory=list)  # (level, title)
    _para: list[str] = field(default_factory=list)
    _fence: list[str] | None = None

    def _breadcrumb(self) -> str:
        return " > ".join(title for _, title in self._crumbs)

    def _flush_para(self) -> None:
        text = "\n".join(self._para).strip()
        if text:
            self.blocks.append(_Block(text, self._breadcrumb()))
        self._para = []

    def _close_fence(self) -> None:
        assert self._fence is not None
        text = "\n".join(self._fence).strip()
        if text:
            self.blocks.append(_Block(text, self._breadcrumb(), is_fence=True))
        self._fence = None

    def feed(self, line: str) -> None:
        if self._fence is not None:
            self._fence.append(line)
            if line.lstrip().startswith("```"):
                self._close_fence()
            return
        if line.lstrip().startswith("```"):
            self._flush_para()
            self._fence = [line]
            return
        m = _HEADING_RE.match(line)
        if m:  # heading: context only, never chunk body
            self._flush_para()
            level = len(m.group(1))
            while self._crumbs and self._crumbs[-1][0] >= level:
                self._crumbs.pop()
            self._crumbs.append((level, m.group(2).strip()))
            return
        if not line.strip():  # blank line: paragraph boundary
            self._flush_para()
            return
        self._para.append(line)

    def finish(self) -> list[_Block]:
        if self._fence is not None:  # unclosed fence: consume to EOF
            self._close_fence()
        self._flush_para()
        return self.blocks


def _split_blocks(body: str) -> list[_Block]:
    splitter = _BlockSplitter()
    for line in body.split("\n"):
        splitter.feed(line)
    return splitter.finish()


def _hard_split(text: str, chunk_size_tokens: int) -> list[str]:
    """Split one oversized block on whitespace into chunk_size_tokens-sized pieces."""
    words = text.split()
    words_per_piece = max(1, int(chunk_size_tokens / _TOKENS_PER_WORD))
    return [
        " ".join(words[i : i + words_per_piece]) for i in range(0, len(words), words_per_piece)
    ]


def _text_of(blocks: list[_Block]) -> str:
    return "\n\n".join(b.text for b in blocks)


def chunk_markdown(
    body: str, chunk_size_tokens: int = 512, overlap_tokens: int = 64
) -> list[Chunk]:
    """Chunk a markdown body (front matter already stripped) into Chunks."""
    blocks = _split_blocks(body)
    chunks: list[Chunk] = []
    current: list[_Block] = []

    def emit(group: list[_Block]) -> None:
        chunks.append(Chunk(text=_text_of(group).strip(), heading=group[0].breadcrumb,
                            pos=len(chunks)))

    i = 0
    while i < len(blocks):
        block = blocks[i]
        if estimate_tokens(_text_of([*current, block])) <= chunk_size_tokens:
            current.append(block)
            i += 1
            continue
        if not current:
            # Single block over budget: fences stay atomic, prose hard-splits.
            if block.is_fence:
                emit([block])
            else:
                for piece in _hard_split(block.text, chunk_size_tokens):
                    chunks.append(Chunk(text=piece, heading=block.breadcrumb, pos=len(chunks)))
            i += 1
            continue
        emit(current)
        # Block-granular overlap: trailing blocks of the emitted chunk, newest
        # last, while they fit the overlap budget (only when more blocks remain).
        overlap: list[_Block] = []
        for prev in reversed(current):
            candidate = [prev, *overlap]
            if estimate_tokens(_text_of(candidate)) > overlap_tokens:
                break
            overlap = candidate
        # Forward-progress guard: overlap must leave room for the pending block.
        while overlap and estimate_tokens(_text_of([*overlap, block])) > chunk_size_tokens:
            overlap.pop(0)
        current = overlap
    if current:
        emit(current)
    return chunks
