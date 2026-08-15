"""
Two-level transcript chunking: parent (context) + child (search) chunks.

Public API
----------
chunk_transcript(segments, ...)
    Single-level chunking — returns a flat list of Chunk objects.
    Used by the Celery pipeline and the fast-index extension endpoint.

chunk_transcript_with_parents(segments, ...)
    Two-level chunking — returns (parents, children).
    Parents are wide context spans (~2 min, no overlap, no embedding).
    Children are precise search windows (~30 s, 5–10 s overlap, embedded).

Boundary logic
--------------
Each parent is a non-overlapping ~parent_seconds window built by
accumulating raw segments until their span >= parent_seconds, then flushing.
Children are built *within* each parent's segment slice using the standard
overlapping-window algorithm, so a child's [start_time, end_time] always
falls inside its parent's span.  Cross-parent prev/next context is omitted
at child level — the parent text provides that wider context at search time.
"""
from dataclasses import dataclass, field

from config import settings


@dataclass
class Chunk:
    index: int
    text: str
    start_time: float
    end_time: float
    prev_text: str | None
    next_text: str | None
    parent_index: int = 0   # index into the parallel ParentChunk list


@dataclass
class ParentChunk:
    index: int
    text: str
    start_time: float
    end_time: float
    word_count: int


# ── Single-level (existing public interface) ──────────────────────────────────

def chunk_transcript(
    segments: list[dict],
    chunk_seconds: int | None = None,
    overlap_seconds: int | None = None,
) -> list[Chunk]:
    """
    Split a transcript into overlapping fixed-duration chunks.

    Parameters
    ----------
    segments : list[dict]
        Each dict: {"text": str, "start": float, "duration": float}
    chunk_seconds : int
        Target window size in seconds (default: config value, 30 s).
    overlap_seconds : int
        Overlap with adjacent chunks (default: config value, 5 s).

    Gap / max-duration guard
    ------------------------
    Two pathological cases are handled before the main accumulator runs:

    1. Zero / negative duration — segments with duration <= 0 are skipped
       entirely.  They produce zero-second chunks that are useless for
       timestamp navigation and pollute the embedding store.

    2. Timestamp gap — if a segment starts more than `max_gap` seconds after
       the current chunk_start (e.g. a long silence or a chapter cut in the
       raw transcript), the current accumulator is flushed BEFORE the segment
       is added.  Without this guard, one chunk can span minutes or hours
       (observed max: 2165 s in production data).

    3. Max duration — even without a gap, if `seg_end - chunk_start` exceeds
       `max_chunk_seconds` the accumulator is force-flushed.  This is a
       safety net for transcripts where segments arrive with huge durations.
    """
    window   = chunk_seconds or settings.chunk_duration_seconds
    overlap  = overlap_seconds or settings.chunk_overlap_seconds
    max_gap  = window * 3          # >3× target window = silence/chapter cut
    max_dur  = window * 4          # absolute ceiling: never exceed 4× target

    if not segments:
        return []

    chunks: list[Chunk] = []
    current: list[dict] = []
    chunk_start = segments[0]["start"]

    for seg in segments:
        # Guard 1: skip zero/negative duration segments
        if seg.get("duration", 0) <= 0:
            continue

        seg_start = seg["start"]
        seg_end   = seg_start + seg["duration"]

        # Guard 2: gap detection — flush before a long silence / chapter cut
        if current and (seg_start - chunk_start) > max_gap:
            prev_end = current[-1]["start"] + current[-1].get("duration", 0)
            _flush(chunks, current, chunk_start, prev_end)
            current = []
            chunk_start = seg_start

        # Guard 3: max duration ceiling — flush if already over the hard cap
        if current and (seg_end - chunk_start) > max_dur:
            prev_end = current[-1]["start"] + current[-1].get("duration", 0)
            _flush(chunks, current, chunk_start, prev_end)
            overlap_start = prev_end - overlap
            current = [s for s in current if s["start"] >= overlap_start]
            chunk_start = current[0]["start"] if current else seg_start

        current.append(seg)

        if seg_end - chunk_start >= window:
            _flush(chunks, current, chunk_start, seg_end)
            overlap_start = seg_end - overlap
            current = [s for s in current if s["start"] >= overlap_start]
            chunk_start = current[0]["start"] if current else seg_end

    if current:
        seg_end = current[-1]["start"] + current[-1].get("duration", 0)
        _flush(chunks, current, chunk_start, seg_end)

    _attach_context(chunks)
    return chunks


# ── Two-level (small-to-big) ──────────────────────────────────────────────────

def chunk_transcript_with_parents(
    segments: list[dict],
    parent_seconds: int = 120,
    child_seconds: int | None = None,
    child_overlap: int | None = None,
) -> tuple[list[ParentChunk], list[Chunk]]:
    """
    Build a two-level hierarchy: parent context spans + child search chunks.

    Returns
    -------
    (parents, children)
        parents  — list[ParentChunk], no embedding, ~parent_seconds each
        children — list[Chunk], embedded, globally indexed, each with
                   parent_index pointing into the parents list
    """
    if not segments:
        return [], []

    # ── Step 1: partition segments into non-overlapping parent windows ─────────
    parent_segs: list[list[dict]] = []
    current: list[dict] = []
    window_start = segments[0]["start"]

    for seg in segments:
        current.append(seg)
        seg_end = seg["start"] + seg.get("duration", 0)
        if seg_end - window_start >= parent_seconds:
            parent_segs.append(current)
            current = []
            window_start = seg_end   # next parent starts right after; no overlap

    if current:
        parent_segs.append(current)

    # ── Step 2: build ParentChunk objects ─────────────────────────────────────
    parents: list[ParentChunk] = []
    for idx, segs in enumerate(parent_segs):
        text = " ".join(s["text"].strip() for s in segs if s["text"].strip())
        if not text:
            continue
        t_start = segs[0]["start"]
        t_end = segs[-1]["start"] + segs[-1].get("duration", 0)
        parents.append(
            ParentChunk(
                index=idx,
                text=text,
                start_time=round(t_start, 3),
                end_time=round(t_end, 3),
                word_count=len(text.split()),
            )
        )

    # ── Step 3: build child chunks within each parent's segment slice ─────────
    all_children: list[Chunk] = []
    global_idx = 0

    for parent in parents:
        segs = parent_segs[parent.index]
        local_children = chunk_transcript(segs, chunk_seconds=child_seconds, overlap_seconds=child_overlap)
        for child in local_children:
            child.index = global_idx
            child.parent_index = parent.index
            all_children.append(child)
            global_idx += 1

    return parents, all_children


# ── Internals ─────────────────────────────────────────────────────────────────

def _flush(chunks: list[Chunk], segments: list[dict], start: float, end: float) -> None:
    text = " ".join(s["text"].strip() for s in segments if s["text"].strip())
    if not text:
        return
    chunks.append(
        Chunk(
            index=len(chunks),
            text=text,
            start_time=round(start, 3),
            end_time=round(end, 3),
            prev_text=None,
            next_text=None,
        )
    )


def _attach_context(chunks: list[Chunk]) -> None:
    """Fill prev_text / next_text with adjacent chunk text (trimmed to 200 chars)."""
    for i, chunk in enumerate(chunks):
        if i > 0:
            chunk.prev_text = chunks[i - 1].text[-200:]
        if i < len(chunks) - 1:
            chunk.next_text = chunks[i + 1].text[:200]
