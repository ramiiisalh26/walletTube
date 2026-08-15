from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    industry: str | None = None
    page: int = Field(default=0, ge=0)
    size: int = Field(default=10, ge=1, le=50)
    session_id: str | None = None          # anonymous session tracker
    referrer_clip_slug: str | None = None  # set when search originates from a shared clip
    no_cache: bool = False                 # bypass Redis read+write — for dev/testing
    # Opt-in LLM answer synthesis over the top results. Off by default: it costs
    # money and latency, so the UI should request it deliberately (a button),
    # not on every keystroke-search.
    generate_answer: bool = False


class ParentContext(BaseModel):
    """Surrounding ~2-minute context span for a search hit (small-to-big retrieval)."""
    text: str
    start_time: float
    end_time: float


class SearchResult(BaseModel):
    video_id: str
    title: str
    thumbnail_url: str | None
    channel_name: str | None
    text: str
    start_time: float
    end_time: float
    view_count: int
    similarity: float           # primary score — cross_score when refined, bi_score otherwise
    youtube_url: str            # watch page with timestamp — direct link
    embed_url: str              # embedded player starting at timestamp — faster for in-app preview
    context: ParentContext | None = None  # parent chunk; None for legacy rows without parents
    # Stage 5 fields — None when cross-encoder was skipped (clear winner gate)
    bi_score: float | None = None           # Stage 4 bi-encoder cosine score
    cross_score: float | None = None        # Stage 5 cross-encoder score
    parent_chunk_start: float | None = None # chunk start (same as start_time for refined results)
    segment_start: float | None = None      # precise segment start within chunk (for fire indicator)
    segment_end: float | None = None        # precise segment end within chunk (for fire indicator)


class Citation(BaseModel):
    """One `[n]` marker in a generated answer, resolved back to its source video."""
    marker: int              # the number the answer text cites, 1-based
    video_id: str
    title: str
    channel_name: str | None = None
    start_time: float
    youtube_url: str


class SearchResponse(BaseModel):
    query: str
    total_results: int
    source: str
    latency_ms: int
    indexing_more: bool
    detected_industry: str | None = None
    results: list[SearchResult]
    search_event_id: int | None = None  # ID of the persisted search_history row
    refined: bool = False               # True when Stage 5 cross-encoder re-ranking was applied
    # RAG — all null unless the request set generate_answer and rag_enabled is on.
    # answer is also null when generation was skipped (weak results, cap reached)
    # or failed; the results list is unaffected either way.
    answer: str | None = None
    citations: list[Citation] | None = None
    answer_skipped_reason: str | None = None  # "low_confidence" | "limit_reached" | "error" | "disabled"
    # Free-tier (anonymous, no login) daily usage. All null for logged-in users.
    free_searches_used: int | None = None
    free_searches_limit: int | None = None
    free_searches_remaining: int | None = None
