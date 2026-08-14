"""Tests for vault_librarian.chunker — written first, per CONTRACTS.md `chunker.py`.

Token math used throughout (estimate_tokens = max(1, round(words * 1.33))):
  2 words -> 3   3 words -> 4   4 words -> 5   6 words -> 8
  8 words -> 11  9 words -> 12  12 words -> 16  18 words -> 24
"""

import textwrap

from vault_librarian.chunker import (
    Chunk,
    chunk_markdown,
    estimate_tokens,
    parse_front_matter,
)

# Four 6-word blocks (8 tokens each) used by the packing/overlap tests.
BLOCK_A = "alpha apple ant arrow axis amber"
BLOCK_B = "bravo book bell birch blaze brick"
BLOCK_C = "charlie cat coal crane cliff coral"
BLOCK_D = "delta dust dome drift dunes dawn"
FOUR_BLOCKS = "\n\n".join([BLOCK_A, BLOCK_B, BLOCK_C, BLOCK_D])


class TestEstimateTokens:
    def test_empty_string_is_zero(self):
        assert estimate_tokens("") == 0

    def test_whitespace_only_is_zero(self):
        assert estimate_tokens("   \n\t  ") == 0

    def test_single_word_is_one(self):
        assert estimate_tokens("word") == 1

    def test_two_words(self):
        assert estimate_tokens("two words") == 3  # round(2.66)

    def test_three_words(self):
        assert estimate_tokens("one two three") == 4  # round(3.99)

    def test_hundred_words(self):
        assert estimate_tokens(" ".join(["w"] * 100)) == 133


class TestParseFrontMatter:
    def test_valid_front_matter_parsed(self):
        text = "---\nid: wiki-x\ntitle: Test Page\ntags: [a, b]\n---\n\nBody text here.\n"
        meta, body = parse_front_matter(text)
        assert meta == {"id": "wiki-x", "title": "Test Page", "tags": ["a", "b"]}
        assert "Body text here." in body
        assert "id: wiki-x" not in body

    def test_realistic_wiki_front_matter(self):
        text = textwrap.dedent(
            """\
            ---
            id: wiki-engramme
            title: Engramme
            created: 2026-06-01
            tags: [needs-synthesis]
            sources: [src-2026-06-01-engramme-site]
            last_synthesized: null
            ---

            # Engramme

            Engramme is a memory augmentation startup.
            """
        )
        meta, body = parse_front_matter(text)
        assert meta["id"] == "wiki-engramme"
        assert meta["tags"] == ["needs-synthesis"]
        assert meta["sources"] == ["src-2026-06-01-engramme-site"]
        assert meta["last_synthesized"] is None
        assert "# Engramme" in body

    def test_no_front_matter_returns_empty_dict_and_original(self):
        text = "Just a body.\nNo delimiters anywhere."
        assert parse_front_matter(text) == ({}, text)

    def test_malformed_yaml_returns_empty_dict_and_original(self):
        text = "---\ntags: [unclosed\n---\nBody survives malformed front matter."
        meta, body = parse_front_matter(text)
        assert meta == {}
        assert body == text  # ORIGINAL text — body must still get indexed

    def test_unclosed_front_matter_returns_original(self):
        text = "---\nid: x\nbody never closes the fence"
        assert parse_front_matter(text) == ({}, text)

    def test_delimiter_not_on_first_line_is_not_front_matter(self):
        text = "\n---\nid: x\n---\nBody"
        assert parse_front_matter(text) == ({}, text)

    def test_empty_front_matter_block(self):
        meta, body = parse_front_matter("---\n---\nBody after empty block.")
        assert meta == {}
        assert "Body after empty block." in body
        assert "---" not in body

    def test_later_thematic_break_stays_in_body(self):
        text = "---\nid: x\n---\nFirst part\n\n---\n\nSecond part"
        meta, body = parse_front_matter(text)
        assert meta == {"id": "x"}
        assert "First part" in body
        assert "Second part" in body
        assert "---" in body  # the in-body rule survives

    def test_non_dict_yaml_returns_empty_dict_and_original(self):
        text = "---\n- just\n- a list\n---\nBody here."
        meta, body = parse_front_matter(text)
        assert meta == {}
        assert body == text


class TestChunkMarkdownBasics:
    def test_empty_body_returns_empty_list(self):
        assert chunk_markdown("") == []

    def test_whitespace_only_body_returns_empty_list(self):
        assert chunk_markdown("   \n\n  \t\n") == []

    def test_single_paragraph_single_chunk(self):
        chunks = chunk_markdown("hello world")
        assert chunks == [Chunk(text="hello world", heading="", pos=0)]

    def test_chunk_text_is_stripped(self):
        chunks = chunk_markdown("\n\n  hello world  \n\n")
        assert chunks[0].text == "hello world"

    def test_whitespace_only_blocks_dropped(self):
        chunks = chunk_markdown("first paragraph\n\n   \n\t\n\nsecond paragraph")
        assert len(chunks) == 1
        assert "first paragraph" in chunks[0].text
        assert "second paragraph" in chunks[0].text

    def test_pos_is_sequential_from_zero(self):
        chunks = chunk_markdown(FOUR_BLOCKS, chunk_size_tokens=20, overlap_tokens=0)
        assert [c.pos for c in chunks] == list(range(len(chunks)))
        assert len(chunks) > 1


class TestHeadingBreadcrumbs:
    def test_no_heading_gives_empty_breadcrumb(self):
        chunks = chunk_markdown("plain paragraph text")
        assert chunks[0].heading == ""

    def test_heading_line_is_context_not_body(self):
        chunks = chunk_markdown("# Install\n\nrun the installer now")
        assert chunks == [Chunk(text="run the installer now", heading="Install", pos=0)]

    def test_nested_headings_join_with_arrow(self):
        chunks = chunk_markdown("# Install\n\n## Setup\n\nconfigure the thing")
        assert chunks[0].heading == "Install > Setup"

    def test_sibling_heading_replaces_same_level(self):
        body = (
            "# Install\n\n## Setup\n\nfirst section words here\n\n"
            "## Verify\n\nsecond section words here"
        )
        chunks = chunk_markdown(body, chunk_size_tokens=6, overlap_tokens=0)
        assert len(chunks) == 2
        assert chunks[0].heading == "Install > Setup"
        assert chunks[1].heading == "Install > Verify"

    def test_higher_level_heading_pops_deeper_crumbs(self):
        body = "# A\n\n## B\n\n### C\n\npara one here\n\n## D\n\npara two here"
        chunks = chunk_markdown(body, chunk_size_tokens=5, overlap_tokens=0)
        assert len(chunks) == 2
        assert chunks[0].heading == "A > B > C"
        assert chunks[1].heading == "A > D"

    def test_new_h1_resets_breadcrumb(self):
        body = "# First\n\npara one here\n\n# Second\n\npara two here"
        chunks = chunk_markdown(body, chunk_size_tokens=5, overlap_tokens=0)
        assert chunks[0].heading == "First"
        assert chunks[1].heading == "Second"

    def test_heading_only_document_returns_empty(self):
        assert chunk_markdown("# Title\n\n## Subtitle") == []

    def test_hash_without_space_is_not_a_heading(self):
        chunks = chunk_markdown("#tag and some words")
        assert chunks[0].heading == ""
        assert "#tag" in chunks[0].text

    def test_seven_hashes_is_not_a_heading(self):
        chunks = chunk_markdown("####### seven hashes line")
        assert chunks[0].heading == ""
        assert "#######" in chunks[0].text

    def test_chunk_spanning_heading_uses_first_block_breadcrumb(self):
        # Packing may merge blocks across a heading boundary; the chunk takes
        # the breadcrumb of its FIRST block (interpretation noted in deviations).
        body = "intro words here\n\n# Section\n\nbody words here"
        chunks = chunk_markdown(body)  # default size: everything packs into one
        assert len(chunks) == 1
        assert chunks[0].heading == ""
        assert "intro words here" in chunks[0].text
        assert "body words here" in chunks[0].text
        assert "# Section" not in chunks[0].text


class TestPacking:
    def test_small_blocks_pack_into_one_chunk(self):
        chunks = chunk_markdown(FOUR_BLOCKS)  # defaults: plenty of room
        assert len(chunks) == 1
        for block in (BLOCK_A, BLOCK_B, BLOCK_C, BLOCK_D):
            assert block in chunks[0].text

    def test_splits_when_budget_exceeded(self):
        # 2 blocks = 12 words = 16 tokens <= 20; 3 blocks = 18 words = 24 > 20.
        chunks = chunk_markdown(FOUR_BLOCKS, chunk_size_tokens=20, overlap_tokens=0)
        assert len(chunks) == 2
        assert chunks[0].text == f"{BLOCK_A}\n\n{BLOCK_B}"
        assert chunks[1].text == f"{BLOCK_C}\n\n{BLOCK_D}"

    def test_every_chunk_within_budget(self):
        chunks = chunk_markdown(FOUR_BLOCKS, chunk_size_tokens=20, overlap_tokens=0)
        assert all(estimate_tokens(c.text) <= 20 for c in chunks)


class TestOverlap:
    def test_block_granular_overlap(self):
        # overlap budget 10 fits exactly one 8-token block: each chunk after the
        # first starts with the trailing block of the previous chunk.
        chunks = chunk_markdown(FOUR_BLOCKS, chunk_size_tokens=20, overlap_tokens=10)
        assert len(chunks) == 3
        assert chunks[0].text == f"{BLOCK_A}\n\n{BLOCK_B}"
        assert chunks[1].text == f"{BLOCK_B}\n\n{BLOCK_C}"
        assert chunks[2].text == f"{BLOCK_C}\n\n{BLOCK_D}"

    def test_no_overlap_when_zero(self):
        chunks = chunk_markdown(FOUR_BLOCKS, chunk_size_tokens=20, overlap_tokens=0)
        assert len(chunks) == 2
        assert BLOCK_B not in chunks[1].text

    def test_overlap_skipped_when_trailing_block_too_big(self):
        # trailing block is 8 tokens > overlap budget 4 -> no overlap carried.
        chunks = chunk_markdown(FOUR_BLOCKS, chunk_size_tokens=20, overlap_tokens=4)
        assert len(chunks) == 2
        assert chunks[1].text == f"{BLOCK_C}\n\n{BLOCK_D}"

    def test_no_trailing_overlap_only_chunk(self):
        # "only when more blocks remain": the final chunk must not spawn an
        # extra chunk consisting solely of overlap.
        chunks = chunk_markdown(FOUR_BLOCKS, chunk_size_tokens=20, overlap_tokens=10)
        assert chunks[-1].text != BLOCK_D
        assert len(chunks) == 3


class TestOversizedBlocks:
    def test_single_oversized_block_hard_split(self):
        words = [f"w{i}" for i in range(200)]
        chunks = chunk_markdown(" ".join(words), chunk_size_tokens=50, overlap_tokens=0)
        assert len(chunks) > 1
        assert all(estimate_tokens(c.text) <= 50 for c in chunks)
        # No words lost, duplicated, or reordered across the split.
        assert " ".join(c.text for c in chunks).split() == words
        assert [c.pos for c in chunks] == list(range(len(chunks)))

    def test_hard_split_pieces_are_chunk_sized(self):
        words = " ".join(f"w{i}" for i in range(200))
        chunks = chunk_markdown(words, chunk_size_tokens=50, overlap_tokens=0)
        # All pieces except the final remainder should be near the budget.
        assert all(estimate_tokens(c.text) >= 25 for c in chunks[:-1])

    def test_oversized_block_between_normal_blocks(self):
        big = " ".join(f"x{i}" for i in range(60))  # ~80 tokens > 20
        body = f"{BLOCK_A}\n\n{big}\n\n{BLOCK_D}"
        chunks = chunk_markdown(body, chunk_size_tokens=20, overlap_tokens=0)
        joined = " ".join(c.text for c in chunks).split()
        assert joined == body.split()
        assert all(estimate_tokens(c.text) <= 20 for c in chunks)

    def test_hard_split_inherits_breadcrumb(self):
        big = " ".join(f"x{i}" for i in range(60))
        chunks = chunk_markdown(f"# Big Section\n\n{big}", chunk_size_tokens=20, overlap_tokens=0)
        assert len(chunks) > 1
        assert all(c.heading == "Big Section" for c in chunks)


class TestFencedCodeBlocks:
    FENCE = "```python\ndef f():\n\n    return 1\n```"

    def test_fence_with_blank_line_stays_atomic(self):
        body = f"Intro paragraph here.\n\n{self.FENCE}\n\nOutro paragraph here."
        chunks = chunk_markdown(body, chunk_size_tokens=10, overlap_tokens=0)
        assert len(chunks) == 3
        assert chunks[1].text == self.FENCE  # delimiters + interior blank line intact

    def test_heading_detection_suspended_inside_fence(self):
        body = "# Real Heading\n\n```\n# not a heading\n```\n\nAfter paragraph."
        chunks = chunk_markdown(body, chunk_size_tokens=8, overlap_tokens=0)
        assert len(chunks) == 2
        assert "# not a heading" in chunks[0].text
        assert chunks[0].heading == "Real Heading"
        assert chunks[1].heading == "Real Heading"  # crumb unpolluted by fence content

    def test_oversized_fence_stays_atomic(self):
        # "Never split mid-fence" wins over hard-split (noted in deviations).
        code_lines = "\n".join(f"line{i} = {i}" for i in range(50))  # 150 words
        fence = f"```python\n{code_lines}\n```"
        chunks = chunk_markdown(fence, chunk_size_tokens=20, overlap_tokens=0)
        assert len(chunks) == 1
        assert chunks[0].text == fence
        assert estimate_tokens(chunks[0].text) > 20

    def test_unclosed_fence_consumed_to_end(self):
        body = "before words here\n\n```\ncode line one\ncode line two"
        chunks = chunk_markdown(body)
        joined = "\n".join(c.text for c in chunks)
        assert "code line one" in joined
        assert "code line two" in joined

    def test_heading_after_fence_close_is_detected(self):
        body = "```\ncode\n```\n\n# After Fence\n\ntrailing paragraph words"
        chunks = chunk_markdown(body, chunk_size_tokens=4, overlap_tokens=0)
        assert chunks[-1].heading == "After Fence"
        assert "trailing paragraph words" in chunks[-1].text


class TestEndToEnd:
    def test_front_matter_then_chunk_pipeline(self):
        text = textwrap.dedent(
            """\
            ---
            id: wiki-engramme
            title: Engramme
            tags: [needs-synthesis]
            ---

            # Engramme

            Engramme is a memory augmentation startup. Their product records
            everything and makes personal memory searchable.
            """
        )
        meta, body = parse_front_matter(text)
        chunks = chunk_markdown(body)
        assert meta["id"] == "wiki-engramme"
        assert len(chunks) == 1
        assert chunks[0].heading == "Engramme"
        assert "memory augmentation startup" in chunks[0].text
        assert "needs-synthesis" not in chunks[0].text  # front matter never indexed
