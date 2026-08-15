"""
Core search orchestration.

Flow:
  1. Redis cache check
  2. Plan daily-limit enforcement
  3. gRPC → embed query (768-dim vector)
  4. Auto-detect industry (cosine vs industries.embedding)
  5. Find top matching topics within that industry (cosine vs topics.embedding)
  6. Get video IDs via video_topics; fallback → primary_industry_id on videos
  7. Cosine search on transcript_chunks WHERE video_id IN (step 6)
  8. Publish search.miss if < threshold high-quality results
  9. Increment daily usage
  10. Persist search_history (background)
  11. Update popular_searches (background)
  12. Cache (6 h) and return
"""
import asyncio
import hashlib
import json
import logging
import math
import re
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.modules.search.reranker import CrossEncoderReranker, should_refine
from api.schemas.search import ParentContext, SearchRequest, SearchResponse, SearchResult
from api.services import analytics_service, embedding_client, kafka_producer, rag_service
from config import settings

logger = logging.getLogger(__name__)


def _sigmoid(x: float) -> float:
    """Convert a cross-encoder raw logit to a [0, 1] probability."""
    return 1.0 / (1.0 + math.exp(-max(-500.0, min(500.0, x))))


_TECH_KEYWORDS = frozenset({
    "java", "python", "javascript", "typescript", "ruby", "php", "golang", "rust",
    "cpp", "csharp", "swift", "kotlin", "scala", "haskell", "elixir", "erlang",
    "clojure", "lisp", "perl", "dart", "lua", "matlab", "nodejs", "node",
    "react", "angular", "vue", "svelte", "django", "flask", "spring",
    "rails", "laravel", "express", "nextjs", "fastapi",
})

# Languages that can be confused by the embedding model — maps each to its competitors.
# A result is penalised when it contains a competitor but NOT the queried language.
_LANG_COMPETITORS: dict[str, frozenset[str]] = {
    "java":       frozenset({"javascript", "typescript"}),
    "python":     frozenset({"ruby", "perl"}),
    "javascript": frozenset({"typescript"}),
    "typescript": frozenset({"javascript"}),
    "golang":     frozenset(),
    "rust":       frozenset(),
    "kotlin":     frozenset({"java"}),
    "swift":      frozenset(),
    "cpp":        frozenset({"c", "csharp"}),
    "csharp":     frozenset({"cpp", "java"}),
    "ruby":       frozenset({"python"}),
    "php":        frozenset(),
}


def _extract_lang_keywords(query: str) -> list[str]:
    """Return programming-language keywords found as whole words in the query."""
    words = set(re.split(r"\W+", query.lower()))
    # "java" must NOT match inside "javascript" — use whole-word presence in the
    # split-word set so "javascript" never collides with "java".
    return [lang for lang in _LANG_COMPETITORS if lang in words]


def _language_tier(query_langs: list[str], text: str, title: str) -> int:
    """
    Return a tier for language-disambiguation sorting:
      2  — text/title contains the queried language (good match)
      1  — contains queried language AND a competitor (ambiguous but relevant)
      0  — contains neither (neutral)
     -1  — contains only a competing language (wrong language — demote)
    """
    if not query_langs:
        return 0
    combined = (text + " " + (title or "")).lower()

    has_query = any(bool(re.search(r"\b" + lang + r"\b", combined)) for lang in query_langs)
    has_rival = any(
        bool(re.search(r"\b" + rival + r"\b", combined))
        for lang in query_langs
        for rival in _LANG_COMPETITORS.get(lang, frozenset())
    )

    if has_query and not has_rival:
        return 2
    if has_query and has_rival:
        return 1
    if has_rival and not has_query:
        return -1
    return 0


_STOPWORDS = frozenset({
    "in", "a", "an", "the", "is", "are", "was", "were", "what", "how", "to",
    "do", "of", "for", "with", "that", "this", "it", "on", "at", "and", "or",
    "but", "when", "where", "why", "which", "who", "be", "by", "from", "as",
    "into", "can", "will", "vs", "between", "difference", "example", "tutorial",
    "learn", "using", "use", "get", "set", "make", "create", "build", "about",
    "i", "my", "me", "we", "you", "he", "she", "they", "them",
})


def _extract_topic_words(query: str, lang_keywords: list[str]) -> list[str]:
    """Meaningful content words from the query — stripped of language names and stopwords."""
    lang_set = set(lang_keywords)
    return [
        w for w in re.split(r"\W+", query.lower())
        if w and len(w) >= 3 and w not in _STOPWORDS and w not in lang_set
    ]


def _topic_tier(topic_words: list[str], text: str) -> int:
    """Return 1 if any topic keyword appears in the chunk text, else 0."""
    if not topic_words:
        return 0
    text_lower = text.lower()
    return 1 if any(w in text_lower for w in topic_words) else 0


def _keyword_bonus(query: str, text: str) -> float:
    """Tiny bonus when tech keywords in the query also appear in the chunk text."""
    q_tech = {w for w in re.split(r"\W+", query.lower())} & _TECH_KEYWORDS
    if not q_tech:
        return 0.0
    t_words = set(re.split(r"\W+", text.lower()))
    return min(0.08, 0.04 * len(q_tech & t_words))


# Framework / platform names ONLY (no bare languages like "python"/"java" — a FastAPI
# video must not be penalised for the word "python"). Adds a lexical signal the
# semantic encoders miss: they match the generic "build a CRUD/REST API" intent and
# ignore which framework the query actually named.
_FRAMEWORKS = frozenset({
    "react", "angular", "vue", "svelte", "astro", "django", "flask", "fastapi",
    "spring", "rails", "laravel", "express", "nextjs", "nuxt", "remix", "nestjs",
    "fastify", "node", "nodejs", "firebase", "razor", "blazor", "aspnet", "dotnet",
    "symfony", "phoenix", "deno",
})

# Pull a deeper reranked pool than we display so the framework boost can lift a
# right-framework video that the blend alone ranked outside the top 10.
_RERANK_POOL = 25


def _framework_adjust(
    title: str, text: str, query_fw: set[str], rival_fw: set[str], weight: float
) -> float:
    """
    Lexical framework nudge layered on the blended score.

      +weight/2  if the query's framework appears in the title OR text (right framework)
      -weight    if a RIVAL framework is in the title and the query's is not
                 (the video is primarily about a different framework)
       0.0       otherwise

    The boost is deliberately HALF the penalty: cross-encoder probs already saturate
    near 1.0, so a full +weight pushes most right-framework videos over 1.0 and they
    all clamp to 100% (losing the differentiated scores). A gentle lift keeps the right
    framework ahead without flattening, while the firm penalty does the real demotion.
    Penalty keys off the title only (the primary-topic signal) so a passing mention of
    another framework in the transcript doesn't demote an otherwise-correct video.
    """
    if weight <= 0 or not query_fw:
        return 0.0
    title_words = set(re.split(r"\W+", (title or "").lower()))
    text_words  = set(re.split(r"\W+", (text  or "").lower()))
    adj = 0.0
    if query_fw & (title_words | text_words):
        adj += weight * 0.5
    if (rival_fw & title_words) and not (query_fw & title_words):
        adj -= weight
    return adj


_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# ── Abbreviation expansion ────────────────────────────────────────────────────
# Bi-encoder embeddings struggle with acronyms that rarely appear verbatim in
# transcripts.  Appending the full form ("DTO" → "DTO data transfer object")
# pulls the query vector toward the semantic neighbourhood where instructors
# actually talk about the concept.
_ABBREVIATIONS: dict[str, str] = {
    # OOP / design
    "dto":    "data transfer object",
    "dao":    "data access object",
    "pojo":   "plain old java object",
    "oop":    "object oriented programming",
    "fp":     "functional programming",
    "solid":  "single responsibility open closed liskov substitution interface segregation dependency inversion",
    "dry":    "don't repeat yourself principle",
    "kiss":   "keep it simple principle",
    "yagni":  "you aren't gonna need it",
    "mvc":    "model view controller",
    "mvvm":   "model view viewmodel",
    "di":     "dependency injection",
    "ioc":    "inversion of control",
    "ddd":    "domain driven design",
    "cqrs":   "command query responsibility segregation",
    "tdd":    "test driven development",
    "bdd":    "behavior driven development",
    # Data / DB
    "orm":    "object relational mapping",
    "acid":   "atomicity consistency isolation durability",
    "crud":   "create read update delete",
    "nosql":  "not only sql",
    # Web / API
    "jwt":    "json web token",
    "oauth":  "open authorization",
    "ssl":    "secure sockets layer",
    "tls":    "transport layer security",
    "cors":   "cross origin resource sharing",
    "spa":    "single page application",
    "ssr":    "server side rendering",
    "csr":    "client side rendering",
    "grpc":   "google remote procedure call",
    # DevOps / infra
    "ci":     "continuous integration",
    "cd":     "continuous deployment delivery",
    "iac":    "infrastructure as code",
    "vpc":    "virtual private cloud",
    "cdn":    "content delivery network",
    "k8s":    "kubernetes",
    # Algorithms
    "bfs":    "breadth first search",
    "dfs":    "depth first search",
    "dp":     "dynamic programming",
    "dag":    "directed acyclic graph",
    "bst":    "binary search tree",
    "lru":    "least recently used cache",
    # ML / AI
    "nlp":    "natural language processing",
    "cnn":    "convolutional neural network",
    "rnn":    "recurrent neural network",
    "gan":    "generative adversarial network",
    "llm":    "large language model",
    "rag":    "retrieval augmented generation",
    # Systems
    "jvm":    "java virtual machine",
    "gc":     "garbage collection",
    "tcp":    "transmission control protocol",
    "udp":    "user datagram protocol",
    "ipc":    "interprocess communication",
    "cli":    "command line interface",
    "regex":  "regular expression",
}


def _expand_abbreviations(query: str) -> str:
    """Append full form after each known abbreviation in the query."""
    def _replace(m: re.Match) -> str:
        word = m.group(0)
        full = _ABBREVIATIONS.get(word.lower())
        return f"{word} {full}" if full else word
    return re.sub(r'\b\w+\b', _replace, query)


def _decompose_query(query: str) -> list[str]:
    """
    Split compound queries at 'vs'/'versus' and multi-word 'and' boundaries.

    'java abstract class vs interface and what is DTO'
      → ['java abstract class', 'interface', 'what is DTO']

    Returns a single-element list when no natural split is found so the
    caller can always iterate without branching.
    """
    # First split on vs / versus — always two distinct concepts
    vs_parts = re.split(r'\s+(?:vs\.?|versus)\s+', query, flags=re.IGNORECASE)
    result: list[str] = []
    for part in vs_parts:
        # Only split on 'and' when both sides have ≥ 2 words (avoids splitting
        # phrases like "lists and dicts in python" where both sides are same topic)
        and_parts = re.split(r'\s+and\s+', part, flags=re.IGNORECASE)
        if len(and_parts) > 1 and all(len(p.split()) >= 2 for p in and_parts):
            result.extend(and_parts)
        else:
            result.append(part)
    return [p.strip() for p in result if p.strip()]


_INDUSTRY_MIN_SIM = 0.40
_TOPIC_MIN_SIM    = 0.35
_TOPIC_LIMIT      = 8       # top N topics to collect videos from
_VIDEO_RANK_LIMIT = 150     # top N videos by embedding before chunk search

# ── Threshold retrieval (primary path) ───────────────────────────────────────
# Only return chunks with similarity >= 0.75 — guarantees every chunk is
# genuinely relevant before the cross-encoder sees it.  No per-video cap:
# the threshold itself is the quality filter.  Hard cap controls CE cost.
_THRESHOLD_SIMILARITY = 0.75   # minimum cosine similarity to enter the CE pool
_THRESHOLD_HARD_CAP   = 300    # safety ceiling: never send more than 300 chunks to CE
_THRESHOLD_MIN        = 20     # if fewer than this pass threshold, fall back to top-N

# ── Top-N fallback (when threshold yields too few results) ───────────────────
# Used for very specific queries where few chunks score >= 0.75 (e.g. rare topics,
# long-tail queries).  Falls back to the original ranked + per-video-capped approach.
_CHUNK_LIMIT      = 200     # max chunks in top-N fallback path
_CHUNKS_PER_VIDEO = 3       # per-video cap for fallback path only
_FALLBACK_PRELIMIT = 200    # HNSW top-K the fallback pulls (also the ef_search we set).
                            # Measured: 200 ≈ 850ms / ~198 chunks. Higher = slower
                            # (ef_search=1000 was ~32s — too much graph traversal).

# ── Cross-encoder segment cap ─────────────────────────────────────────────────
# Reduced from 400: test showed 2,036ms at 400 segs (over 300ms budget).
# 300 chunks × ~1.6 segs/chunk ≈ 480 → trimmed to 300; ~140ms on CPU.
_MAX_SEGMENTS     = 300

# ── SQL fragments ─────────────────────────────────────────────────────────────

_TOPIC_SQL = """
SELECT id
FROM topics
WHERE industry_id = :industry_id
  AND is_active = TRUE
  AND embedding IS NOT NULL
  AND 1 - (embedding <=> CAST(:vec AS vector)) >= :min_sim
ORDER BY embedding <=> CAST(:vec AS vector)
LIMIT :limit
"""

_VIDEOS_BY_TOPICS_SQL = """
SELECT DISTINCT video_id FROM video_topics WHERE topic_id = ANY(CAST(:topic_ids AS integer[]))
"""

_VIDEOS_BY_INDUSTRY_SQL = """
SELECT id FROM videos
WHERE primary_industry_id = :industry_id AND indexing_status = 'indexed'
"""

# Rank candidate videos by their embedding similarity to the query — narrows chunk search space
_RANK_VIDEOS_SQL = """
SELECT id FROM videos
WHERE id = ANY(CAST(:video_ids AS integer[]))
  AND embedding IS NOT NULL
ORDER BY embedding <=> CAST(:vector AS vector)
LIMIT :limit
"""

_CHUNK_SEARCH_SQL = """
WITH scored AS (
    -- Score every candidate chunk from the shortlisted videos
    SELECT
        tc.id            AS chunk_id,
        v.id             AS video_id,
        v.youtube_video_id,
        v.title,
        v.thumbnail_url,
        ch.name          AS channel_name,
        tc.text,
        tc.start_time,
        tc.end_time,
        v.view_count,
        v.quality_score  AS video_quality,
        v.published_at,
        1 - (tc.embedding <=> CAST(:vector AS vector)) AS vector_score,
        pc.text          AS parent_text,
        pc.start_time    AS parent_start_time,
        pc.end_time      AS parent_end_time,
        ROW_NUMBER() OVER (
            PARTITION BY tc.video_id
            ORDER BY tc.embedding <=> CAST(:vector AS vector)
        ) AS rn
    FROM transcript_chunks tc
    JOIN videos  v  ON tc.video_id = v.id
    LEFT JOIN channels ch ON v.channel_id = ch.id
    LEFT JOIN transcript_parent_chunks pc ON tc.parent_chunk_id = pc.id
    WHERE tc.video_id = ANY(CAST(:video_ids AS integer[]))
      AND 1 - (tc.embedding <=> CAST(:vector AS vector)) >= :min_score
),
capped AS (
    -- Keep only the best :per_video chunks per video so one 10k-chunk course
    -- cannot crowd out every other relevant video from the pool
    SELECT * FROM scored WHERE rn <= :per_video
)
SELECT * FROM capped ORDER BY vector_score DESC LIMIT :limit
"""

_CHUNK_SEARCH_ALL_SQL = """
WITH topk AS (
    -- Step 1: HNSW index fetches the :prelimit NEAREST chunks. This is the only
    -- part that touches all vectors and it uses the index (no full scan). The
    -- old query put the per-video window function over the whole table, which
    -- forced a sequential scan of every chunk (~129s). Here the window function
    -- below runs on just :prelimit rows.
    SELECT tc.id, tc.video_id, tc.text, tc.start_time, tc.end_time,
           tc.parent_chunk_id,
           1 - (tc.embedding <=> CAST(:vector AS vector)) AS vector_score
    FROM transcript_chunks tc
    ORDER BY tc.embedding <=> CAST(:vector AS vector)
    LIMIT :prelimit
),
scored AS (
    SELECT
        t.id             AS chunk_id,
        v.id             AS video_id,
        v.youtube_video_id,
        v.title,
        v.thumbnail_url,
        ch.name          AS channel_name,
        t.text,
        t.start_time,
        t.end_time,
        v.view_count,
        v.quality_score  AS video_quality,
        v.published_at,
        t.vector_score,
        pc.text          AS parent_text,
        pc.start_time    AS parent_start_time,
        pc.end_time      AS parent_end_time,
        ROW_NUMBER() OVER (
            PARTITION BY t.video_id
            ORDER BY t.vector_score DESC
        ) AS rn
    FROM topk t
    JOIN videos  v  ON t.video_id = v.id
    LEFT JOIN channels ch ON v.channel_id = ch.id
    LEFT JOIN transcript_parent_chunks pc ON t.parent_chunk_id = pc.id
    WHERE v.indexing_status = 'indexed'
      AND t.vector_score >= :min_score
)
SELECT * FROM scored WHERE rn <= :per_video ORDER BY vector_score DESC LIMIT :limit
"""

# Threshold variants — no per-video cap; quality is enforced by the similarity
# floor instead.  Hard cap (LIMIT :hard_cap) keeps CE cost bounded.
_CHUNK_THRESHOLD_SQL = """
SELECT
    tc.id            AS chunk_id,
    v.id             AS video_id,
    v.youtube_video_id,
    v.title,
    v.thumbnail_url,
    ch.name          AS channel_name,
    tc.text,
    tc.start_time,
    tc.end_time,
    v.view_count,
    v.quality_score  AS video_quality,
    v.published_at,
    1 - (tc.embedding <=> CAST(:vector AS vector)) AS vector_score,
    pc.text          AS parent_text,
    pc.start_time    AS parent_start_time,
    pc.end_time      AS parent_end_time
FROM transcript_chunks tc
JOIN videos  v  ON tc.video_id = v.id
LEFT JOIN channels ch ON v.channel_id = ch.id
LEFT JOIN transcript_parent_chunks pc ON tc.parent_chunk_id = pc.id
WHERE tc.video_id = ANY(CAST(:video_ids AS integer[]))
  AND 1 - (tc.embedding <=> CAST(:vector AS vector)) >= :threshold
ORDER BY tc.embedding <=> CAST(:vector AS vector)
LIMIT :hard_cap
"""

_CHUNK_THRESHOLD_ALL_SQL = """
SELECT
    tc.id            AS chunk_id,
    v.id             AS video_id,
    v.youtube_video_id,
    v.title,
    v.thumbnail_url,
    ch.name          AS channel_name,
    tc.text,
    tc.start_time,
    tc.end_time,
    v.view_count,
    v.quality_score  AS video_quality,
    v.published_at,
    1 - (tc.embedding <=> CAST(:vector AS vector)) AS vector_score,
    pc.text          AS parent_text,
    pc.start_time    AS parent_start_time,
    pc.end_time      AS parent_end_time
FROM transcript_chunks tc
JOIN videos  v  ON tc.video_id = v.id
LEFT JOIN channels ch ON v.channel_id = ch.id
LEFT JOIN transcript_parent_chunks pc ON tc.parent_chunk_id = pc.id
WHERE v.indexing_status = 'indexed'
  AND 1 - (tc.embedding <=> CAST(:vector AS vector)) >= :threshold
ORDER BY tc.embedding <=> CAST(:vector AS vector)
LIMIT :hard_cap
"""

# ─────────────────────────────────────────────────────────────────────────────


def _cache_key(query: str, industry_slug: str | None) -> str:
    base = query.lower().strip()
    if industry_slug:
        base += f"::{industry_slug}"
    return "cache:search:" + hashlib.md5(base.encode()).hexdigest()


def _to_vector_str(vector: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vector) + "]"


# ── Anonymous free-tier gate ──────────────────────────────────────────────────
# Counts searches per browser session id in Redis, resetting at midnight UTC.
# Atomic: rejects (returns -1) when already at the limit, otherwise increments
# and returns the new count so the caller can report remaining searches.
_FREE_LIMIT_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current >= tonumber(ARGV[1]) then
    return -1
end
local newVal = redis.call('INCR', KEYS[1])
if newVal == 1 then
    redis.call('EXPIREAT', KEYS[1], tonumber(ARGV[2]))
end
return newVal
"""


def _free_limit_key(session_id: str) -> str:
    return f"freelimit:{session_id}:{date.today().isoformat()}"


def _next_midnight_epoch() -> int:
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    return int(datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc).timestamp())


async def _consume_free_search(redis, session_id: str, limit: int) -> int:
    """Return the new daily count, or -1 if the session is already at the limit."""
    script = redis.register_script(_FREE_LIMIT_LUA)
    result = await script(
        keys=[_free_limit_key(session_id)],
        args=[str(limit), str(_next_midnight_epoch())],
    )
    return int(result)


async def _attach_answer(
    redis,
    req: SearchRequest,
    response: SearchResponse,
    source: list[SearchResult],
) -> None:
    """Populate response.answer/citations in place. Never raises.

    `source` is the full ranked list, not the page slice — the answer should
    always be drawn from the strongest chunks regardless of which page the
    caller happens to be on.
    """
    if not rag_service.is_available():
        response.answer_skipped_reason = rag_service.SKIP_DISABLED
        return
    if not await rag_service.consume_credit(redis, req.session_id):
        response.answer_skipped_reason = rag_service.SKIP_LIMIT
        return
    answer, citations, reason = await rag_service.generate(req.query, source)
    response.answer = answer
    response.citations = citations
    response.answer_skipped_reason = reason


async def search(
    session: AsyncSession,
    redis,
    req: SearchRequest,
    user_id: int | None,
) -> SearchResponse:
    start_ms = int(time.time() * 1000)

    # ── 0. Anonymous free-tier gate (no login required) ───────────────────────
    # Logged-in users are handled by the DB plan check below. Anonymous users
    # are capped per browser session id; each search (cached or fresh) consumes
    # one credit so the counter the user sees is deterministic.
    free_limit = settings.free_daily_search_limit
    free_used: int | None = None
    free_remaining: int | None = None
    gate_active = settings.billing_enabled and user_id is None and bool(req.session_id)
    if gate_active:
        new_count = await _consume_free_search(redis, req.session_id, free_limit)
        if new_count < 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="You've used all your free searches for today. Upgrade to Pro for unlimited searches.",
            )
        free_used = new_count
        free_remaining = max(0, free_limit - new_count)

    def _with_free_usage(resp: SearchResponse) -> SearchResponse:
        """Attach anonymous usage counters (only when the billing gate is on)."""
        if gate_active:
            resp.free_searches_used = free_used
            resp.free_searches_limit = free_limit
            resp.free_searches_remaining = free_remaining
        return resp

    # ── 1. Cache check ────────────────────────────────────────────────────────
    provisional_key = _cache_key(req.query, req.industry)
    if not req.no_cache and settings.search_cache_enabled:
        cached_raw = await redis.get(provisional_key)
        if cached_raw:
            logger.debug("Cache hit: %s", req.query)
            cached = SearchResponse(**json.loads(cached_raw))
            # The cache key doesn't include generate_answer, so an entry written
            # by a retrieval-only request can be reused here. Fill in the answer
            # on demand and write it back, so the next asker pays nothing.
            if req.generate_answer and cached.answer is None and cached.answer_skipped_reason is None:
                await _attach_answer(redis, req, cached, cached.results)
                ttl = settings.search_cache_ttl_hours * 3600
                await redis.set(provisional_key, cached.model_dump_json(), ex=ttl)
            return _with_free_usage(cached)

    # ── 2. Plan limit check ───────────────────────────────────────────────────
    if settings.billing_enabled and user_id is not None:
        can_result = await session.execute(
            text("SELECT can_user_search(:uid)"), {"uid": user_id}
        )
        if not can_result.scalar():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Daily search limit reached. Upgrade to Pro for unlimited searches.",
            )

    # ── 3. Embed query ────────────────────────────────────────────────────────
    t0 = int(time.time() * 1000)
    expanded   = _expand_abbreviations(req.query)
    sub_queries = _decompose_query(expanded)

    if len(sub_queries) > 1:
        # Compound query ("X vs Y", "A and B") — embed each part separately
        # and average the vectors so the search space covers all concepts.
        vecs = await embedding_client.embed_batch(
            [_BGE_QUERY_PREFIX + q for q in sub_queries]
        )
        n, d  = len(vecs), len(vecs[0])
        vector = [sum(vecs[j][i] for j in range(n)) / n for i in range(d)]
        logger.info("[search] compound query split into %d parts: %s", n, sub_queries)
    else:
        vector = await embedding_client.embed_one(_BGE_QUERY_PREFIX + expanded)

    vector_str = _to_vector_str(vector)
    logger.info("[timing] grpc_embed=%dms", int(time.time() * 1000) - t0)

    # ── 4. Detect industry ────────────────────────────────────────────────────
    detected_industry_slug: str | None = None
    industry_id: int | None = None

    if req.industry:
        row = (await session.execute(
            text("SELECT id FROM industries WHERE slug = :s AND is_active = TRUE"),
            {"s": req.industry},
        )).fetchone()
        if row:
            industry_id = row[0]
    else:
        row = (await session.execute(
            text("""
                SELECT id, slug, 1 - (embedding <=> CAST(:vec AS vector)) AS sim
                FROM industries
                WHERE is_active = TRUE AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT 1
            """),
            {"vec": vector_str},
        )).fetchone()
        if row and row.sim >= _INDUSTRY_MIN_SIM:
            industry_id = row.id
            detected_industry_slug = row.slug
            logger.info("[search] industry='%s' sim=%.3f", row.slug, row.sim)

    effective_slug = req.industry or detected_industry_slug
    cache_key = _cache_key(req.query, effective_slug)
    logger.info("[timing] detect_industry=%dms", int(time.time() * 1000) - start_ms)

    # ── 5. Find matching topics ───────────────────────────────────────────────
    topic_ids: list[int] = []
    if industry_id is not None:
        t0 = int(time.time() * 1000)
        topic_rows = (await session.execute(
            text(_TOPIC_SQL),
            {"industry_id": industry_id, "vec": vector_str,
             "min_sim": _TOPIC_MIN_SIM, "limit": _TOPIC_LIMIT},
        )).fetchall()
        topic_ids = [r[0] for r in topic_rows]
        logger.info("[timing] topics=%dms  matched=%d", int(time.time() * 1000) - t0, len(topic_ids))

    # ── 6. Collect video IDs ──────────────────────────────────────────────────
    t0 = int(time.time() * 1000)
    video_ids: list[int] = []

    if topic_ids:
        rows = (await session.execute(
            text(_VIDEOS_BY_TOPICS_SQL),
            {"topic_ids": topic_ids},
        )).fetchall()
        video_ids = [r[0] for r in rows]

    # Fallback: use primary_industry_id when video_topics is empty
    if not video_ids and industry_id is not None:
        rows = (await session.execute(
            text(_VIDEOS_BY_INDUSTRY_SQL),
            {"industry_id": industry_id},
        )).fetchall()
        video_ids = [r[0] for r in rows]
        logger.info("[search] topic→video empty; fallback to industry videos count=%d", len(video_ids))

    logger.info("[timing] collect_videos=%dms  count=%d", int(time.time() * 1000) - t0, len(video_ids))

    # ── 6b. Always include unclassified indexed videos ────────────────────────
    unclassified_rows = (await session.execute(
        text("""
            SELECT id FROM videos
            WHERE primary_industry_id IS NULL
              AND indexing_status = 'indexed'
        """)
    )).fetchall()
    if unclassified_rows:
        extra_ids = {r[0] for r in unclassified_rows}
        video_ids = list(set(video_ids) | extra_ids)

    # ── 6c. Global fallback — if industry/topic routing returned nothing, ──────
    # search across ALL indexed videos so no query returns empty-handed.
    if not video_ids:
        fallback_rows = (await session.execute(
            text("SELECT id FROM videos WHERE indexing_status = 'indexed'")
        )).fetchall()
        video_ids = [r[0] for r in fallback_rows]
        logger.info("[search] industry routing empty — global fallback, all indexed videos count=%d", len(video_ids))

    # ── 6d. Video ranking disabled — pass full pool to chunk search ──────────
    # No cap here: chunk search has LIMIT 200 + per_video=3 so cost is bounded
    # regardless of pool size. Capping here would randomly exclude relevant videos.
    if video_ids:
        logger.info("[search] rank_videos disabled — pool=%d videos", len(video_ids))

    # ── 7. Cosine search across ALL indexed videos — bi-encoder retrieves, CE ranks
    # Uses the ALL-videos queries (HNSW index, no video_id filter) so a borderline
    # industry score can never hide a strong chunk. Industry/topic routing above is
    # kept only for response/history metadata — it does NOT restrict results here.
    # Threshold path: chunks >= 0.75; fallback to top-N (min 0.60) if < 20 pass.
    t0 = int(time.time() * 1000)
    threshold_mode = True
    rows = await session.execute(
        text(_CHUNK_THRESHOLD_ALL_SQL),
        {"vector": vector_str,
         "threshold": settings.search_threshold_similarity,
         "hard_cap": settings.search_threshold_hard_cap},
    )
    raw_results = [dict(row) for row in rows.mappings().all()]

    if len(raw_results) < settings.search_threshold_min_results:
        threshold_mode = False
        logger.info(
            "[search] threshold(%.2f) returned %d chunks (<%d) — falling back to top-N",
            settings.search_threshold_similarity, len(raw_results),
            settings.search_threshold_min_results,
        )
        # Raise HNSW recall to match the top-K we pull (default ef_search ~40 is
        # too low for LIMIT 1000). SET LOCAL = transaction-scoped, resets on commit.
        await session.execute(text(f"SET LOCAL hnsw.ef_search = {_FALLBACK_PRELIMIT}"))
        rows = await session.execute(
            text(_CHUNK_SEARCH_ALL_SQL),
            {"vector": vector_str,
             "min_score": settings.search_min_similarity,
             "prelimit": _FALLBACK_PRELIMIT,
             "limit": _CHUNK_LIMIT, "per_video": _CHUNKS_PER_VIDEO},
        )
        raw_results = [dict(row) for row in rows.mappings().all()]

    logger.info(
        "[timing] chunk_search=%dms  hits=%d  mode=%s",
        int(time.time() * 1000) - t0, len(raw_results),
        "threshold" if threshold_mode else "top_n",
    )

    # ── Stage 5: cross-encoder re-ranking with bi-encoder blend ───────────────
    # Gate: skip when fewer than 2 chunks (nothing to compare).
    # When applied: scores each candidate's FULL chunk text with ms-marco-MiniLM,
    # ranks by final = bi_weight*bi_score + (1-bi_weight)*cross_prob, and
    # deduplicates to one chunk per video. Full-context scoring lets the model tell
    # frameworks apart; the bi-blend stops one fragment flipping a strong match.
    t0 = int(time.time() * 1000)
    refined = should_refine(raw_results)  # cross-encoder re-ranks when >= 2 chunks
    results: list[SearchResult] = []

    if refined:
        reranker = CrossEncoderReranker.get()
        loop = asyncio.get_event_loop()
        segments = await loop.run_in_executor(
            None, reranker.rerank, req.query, raw_results, _RERANK_POOL,
            settings.search_bi_blend_weight,
        )
        # Framework keyword boost/penalty — lexical signal the encoders miss. When the
        # query names a framework, lift videos that mention it and demote videos whose
        # title is primarily a rival framework, then keep the top 10.
        q_fw = set(re.split(r"\W+", req.query.lower())) & _FRAMEWORKS
        if q_fw and settings.search_framework_weight > 0:
            rival = _FRAMEWORKS - q_fw
            for seg in segments:
                adj = _framework_adjust(
                    seg["title"], seg["text"], q_fw, rival, settings.search_framework_weight
                )
                seg["final_score"] = min(1.0, max(0.0, seg["final_score"] + adj))
            segments.sort(key=lambda s: s["final_score"], reverse=True)
        segments = segments[:10]
        logger.info("[timing] rerank=%dms  segs=%d", int(time.time() * 1000) - t0, len(segments))
        for seg in segments:
            vid_id      = seg["youtube_video_id"]
            chunk_start = float(seg["parent_chunk_start"])
            chunk_end   = float(seg.get("parent_chunk_end") or seg["end_time"])
            t_sec       = int(chunk_start)
            cross_prob  = _sigmoid(seg["cross_score"])
            display_text = seg.get("chunk_text") or seg["text"]
            results.append(SearchResult(
                video_id=vid_id,
                title=seg["title"],
                thumbnail_url=seg.get("thumbnail_url"),
                channel_name=seg.get("channel_name"),
                text=display_text,             # full chunk content
                start_time=chunk_start,        # video plays from chunk beginning
                end_time=chunk_end,
                view_count=int(seg.get("view_count") or 0),
                similarity=seg["final_score"],   # blended bi+cross score drives ranking
                youtube_url=f"https://www.youtube.com/watch?v={vid_id}&t={t_sec}",
                embed_url=f"https://www.youtube.com/embed/{vid_id}?start={t_sec}&autoplay=1",
                bi_score=seg["bi_score"],
                cross_score=cross_prob,
                parent_chunk_start=chunk_start,
                segment_start=float(seg["start_time"]),  # precise hot zone for fire indicator
                segment_end=float(seg["end_time"]),
                context=ParentContext(
                    text=seg["parent_text"],
                    start_time=float(seg["parent_start_time"]),
                    end_time=float(seg["parent_end_time"]),
                ) if seg.get("parent_text") else None,
            ))
    else:
        for row in raw_results:
            vid_id = row["youtube_video_id"]
            start  = float(row["start_time"])
            t_sec  = int(start)
            results.append(SearchResult(
                video_id=vid_id,
                title=row["title"],
                thumbnail_url=row.get("thumbnail_url"),
                channel_name=row.get("channel_name"),
                text=row["text"],
                start_time=start,
                end_time=float(row["end_time"]),
                view_count=int(row.get("view_count") or 0),
                similarity=float(row["vector_score"]),
                youtube_url=f"https://www.youtube.com/watch?v={vid_id}&t={t_sec}",
                embed_url=f"https://www.youtube.com/embed/{vid_id}?start={t_sec}&autoplay=1",
                bi_score=float(row["vector_score"]),
                context=ParentContext(
                    text=row["parent_text"],
                    start_time=float(row["parent_start_time"]),
                    end_time=float(row["parent_end_time"]),
                ) if row.get("parent_text") else None,
            ))

        # ── 7b. Sort by similarity DESC (bi-encoder path) ───────────────────────
        results.sort(key=lambda r: r.similarity, reverse=True)

    # ── 7c. Final ordering: STRICTLY by accuracy (similarity) DESC ───────────
    # No language reordering / demotion — the cross-encoder already disambiguates
    # java vs javascript by meaning, so we present pure descending accuracy. This
    # guarantees the response is sorted high→low regardless of the path above.
    results.sort(key=lambda r: r.similarity, reverse=True)

    # ── 8. Search.miss ────────────────────────────────────────────────────────
    high_quality = sum(1 for r in results if r.similarity >= settings.search_high_quality_threshold)
    indexing_more = high_quality < settings.search_fallback_threshold
    if indexing_more:
        asyncio.create_task(
            kafka_producer.publish_search_miss(req.query, [r.video_id for r in results])
        )

    # ── 9. Daily usage ────────────────────────────────────────────────────────
    if user_id is not None:
        try:
            await session.execute(text("SELECT increment_daily_usage(:uid)"), {"uid": user_id})
        except Exception as exc:
            logger.warning("increment_daily_usage failed: %s", exc)

    latency_ms = int(time.time() * 1000) - start_ms
    source = "cosine" if not indexing_more else "cosine_miss"

    # ── 10. Persist search event (synchronous — returns ID for click attribution) ─
    search_event_id: int | None = None
    try:
        top_score = results[0].similarity if results else None
        row = (await session.execute(
            text("""
                INSERT INTO search_history
                    (user_id, session_id, query, result_count, source,
                     industry_id, latency_ms, top_score, referrer_clip_slug)
                VALUES
                    (:uid, :session_id, :query, :count, 'indexed',
                     :industry_id, :latency_ms, :top_score, :referrer_clip_slug)
                RETURNING id
            """),
            {
                "uid": user_id,
                "session_id": req.session_id,
                "query": req.query,
                "count": len(results),
                "industry_id": industry_id,
                "latency_ms": latency_ms,
                "top_score": top_score,
                "referrer_clip_slug": req.referrer_clip_slug,
            },
        )).fetchone()
        search_event_id = row[0] if row else None
        await session.commit()
    except Exception as exc:
        logger.warning("save search_history failed: %s", exc)

    # ── 11. Popular searches (background) ────────────────────────────────────
    # Opens its own session inside — must NOT pass the request session to a task
    # that outlives the request (races session.close()).
    asyncio.create_task(
        analytics_service.record_popular_search(req.query, industry_id)
    )

    # ── 12. Cache & return ────────────────────────────────────────────────────
    page_results = results[req.page * req.size: req.page * req.size + req.size]
    response = SearchResponse(
        query=req.query,
        total_results=len(results),
        source=source,
        latency_ms=latency_ms,
        indexing_more=indexing_more,
        detected_industry=detected_industry_slug,
        results=page_results,
        search_event_id=search_event_id,
        refined=refined,
    )

    # ── 12b. RAG answer ───────────────────────────────────────────────────────
    # Runs on the paged results the caller will actually see, and before the
    # cache write so the answer is cached alongside them. Soft-fails to
    # answer=None — retrieval results are never withheld because generation broke.
    if req.generate_answer:
        await _attach_answer(redis, req, response, results)

    if not req.no_cache and settings.search_cache_enabled:
        ttl = settings.search_cache_ttl_hours * 3600
        await redis.set(cache_key, response.model_dump_json(), ex=ttl)
    # Attach per-session counters AFTER caching so they aren't persisted for
    # the next visitor (whose counts differ).
    return _with_free_usage(response)
