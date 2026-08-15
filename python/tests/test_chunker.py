"""
Unit tests for two-level transcript chunking.
No database or network required — pure logic tests.
"""
import pytest

from services.transcript.chunker import (
    Chunk,
    ParentChunk,
    chunk_transcript,
    chunk_transcript_with_parents,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_segments(count: int, duration: float = 5.0) -> list[dict]:
    """Generate `count` segments each lasting `duration` seconds."""
    return [
        {"text": f"word{i}", "start": i * duration, "duration": duration}
        for i in range(count)
    ]


# 5-minute transcript: 60 × 5s = 300 s
FIVE_MIN = make_segments(60)

# 30-second transcript: 6 × 5s = 30 s  (shorter than one parent window)
THIRTY_SEC = make_segments(6)


# ── chunk_transcript (single-level) ──────────────────────────────────────────

def test_single_level_basic():
    chunks = chunk_transcript(FIVE_MIN)
    assert chunks, "should produce chunks"
    assert all(isinstance(c, Chunk) for c in chunks)


def test_single_level_empty():
    assert chunk_transcript([]) == []


def test_single_level_indices_sequential():
    chunks = chunk_transcript(FIVE_MIN)
    for i, c in enumerate(chunks):
        assert c.index == i


def test_single_level_context_attached():
    chunks = chunk_transcript(FIVE_MIN)
    # Middle chunks should have both prev and next context
    mid = chunks[len(chunks) // 2]
    assert mid.prev_text is not None
    assert mid.next_text is not None


# ── chunk_transcript_with_parents ─────────────────────────────────────────────

def test_two_level_returns_both():
    parents, children = chunk_transcript_with_parents(FIVE_MIN)
    assert parents
    assert children


def test_two_level_empty():
    parents, children = chunk_transcript_with_parents([])
    assert parents == []
    assert children == []


def test_every_child_has_valid_parent_index():
    parents, children = chunk_transcript_with_parents(FIVE_MIN)
    parent_indices = {p.index for p in parents}
    for c in children:
        assert c.parent_index in parent_indices, (
            f"child {c.index} has parent_index={c.parent_index} not in {parent_indices}"
        )


def test_child_indices_globally_sequential():
    parents, children = chunk_transcript_with_parents(FIVE_MIN)
    for i, c in enumerate(children):
        assert c.index == i, f"child at position {i} has index {c.index}"


def test_parents_are_parent_chunk_instances():
    parents, _ = chunk_transcript_with_parents(FIVE_MIN)
    assert all(isinstance(p, ParentChunk) for p in parents)


def test_parent_spans_cover_full_duration():
    parents, _ = chunk_transcript_with_parents(FIVE_MIN)
    # First parent starts at or near the first segment
    assert parents[0].start_time == pytest.approx(0.0, abs=1.0)
    # Last parent ends at or near the last segment's end
    last_seg_end = FIVE_MIN[-1]["start"] + FIVE_MIN[-1]["duration"]
    assert parents[-1].end_time == pytest.approx(last_seg_end, abs=1.0)


def test_parent_spans_non_overlapping():
    parents, _ = chunk_transcript_with_parents(FIVE_MIN, parent_seconds=120)
    for i in range(len(parents) - 1):
        # Each parent must end before or exactly when the next one starts
        assert parents[i].end_time <= parents[i + 1].start_time + 0.001, (
            f"parent {i} end={parents[i].end_time} overlaps "
            f"parent {i+1} start={parents[i+1].start_time}"
        )


def test_children_within_parent_time_range():
    parents, children = chunk_transcript_with_parents(FIVE_MIN)
    parent_map = {p.index: p for p in parents}
    for c in children:
        parent = parent_map[c.parent_index]
        assert c.start_time >= parent.start_time - 0.001, (
            f"child start {c.start_time} < parent start {parent.start_time}"
        )
        assert c.end_time <= parent.end_time + 0.001, (
            f"child end {c.end_time} > parent end {parent.end_time}"
        )


def test_parent_seconds_respected():
    parents, _ = chunk_transcript_with_parents(FIVE_MIN, parent_seconds=120)
    # All parents except the last should span at least 100 s
    for p in parents[:-1]:
        span = p.end_time - p.start_time
        assert span >= 100, f"parent {p.index} span={span:.1f}s < 100s"


def test_short_transcript_single_parent():
    parents, children = chunk_transcript_with_parents(THIRTY_SEC)
    # A 30-second transcript fits in one parent window
    assert len(parents) == 1
    assert all(c.parent_index == 0 for c in children)


def test_parent_text_contains_child_words():
    parents, children = chunk_transcript_with_parents(FIVE_MIN)
    parent_map = {p.index: p for p in parents}
    for c in children:
        parent = parent_map[c.parent_index]
        # At least one word from child text appears in parent text
        first_word = c.text.split()[0]
        assert first_word in parent.text, (
            f"'{first_word}' not found in parent {c.parent_index} text"
        )


def test_parent_word_count_populated():
    parents, _ = chunk_transcript_with_parents(FIVE_MIN)
    for p in parents:
        assert p.word_count > 0


def test_custom_child_seconds():
    _, children_30 = chunk_transcript_with_parents(FIVE_MIN, child_seconds=30)
    _, children_60 = chunk_transcript_with_parents(FIVE_MIN, child_seconds=60)
    # Smaller child window → more chunks
    assert len(children_30) >= len(children_60)
