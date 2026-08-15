# Project Tracker — YTSearch Python Backend

All changes made by Claude across every session, in chronological order.

---

## Session 1 — Extension Fixes & Indexing Performance

### Chrome Extension
- Fixed `cardCount` TDZ error (`let cardCount` declared after first use in `clearResults()`) — moved to top-of-file state block in `chrome-extension/content.js`
- Fixed extension stuck at "Checking…" — MV3 service worker terminates before async fetch completes; fixed with `sendResponse` + `return true` pattern in background service worker
- Fixed double "Preparing video record…" step — `startIndexing()` was called twice; added `let indexing = false` guard

### gRPC / Embedding Server
- Fixed gRPC port 50051 already in use (stale PID) — killed with `Stop-Process`
- Changed `max_workers` from 4 → 8 in `services/embedding/server.py` ThreadPoolExecutor
- Changed `embedding_batch_size` from 32 → 64 in `config.py`

### Indexing Pipeline
- Moved "Preparing video record…" yield BEFORE the first DB query in `api/routers/video.py` (was after)
- Added adaptive chunk windows (60s / 90s / 120s based on segment count) in `api/routers/video.py`
- Added bulk SQL insert via PostgreSQL `unnest()` arrays for transcript chunks
- Added `fast=True` parameter to `fetch_transcript()` in `services/transcript/fetcher.py` to skip random delay for extension use
- Added heartbeat keep-alive loop (`asyncio.create_task`) so SSE connection doesn't drop during long transcript fetch

### Notes File
- Created `NOTES.md` with local vs production performance notes

---

## Session 2 — Search Accuracy (Tier 1 + Tier 2)

### BGE Query Prefix (Tier 1 — no re-index required)
- Added `_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "` constant
- Applied prefix in `api/services/search_service.py` step 3 (embed query)
- Applied prefix in `api/routers/video.py` per-video search stream

### Fallback Threshold Fix (Tier 1)
- Changed fallback condition in `api/routers/video.py` from count-based (`< 3`) to quality-based (`best_sim < 0.60`) so one good result doesn't trigger a noisy global fallback

### min_score Raise (Tier 1)
- Raised `min_score` default from `0.50` → `0.55` in `api/routers/video.py`

### max_seq_length (Tier 2 — requires re-index)
- Changed `max_seq_length` from `128` → `256` in `services/embedding/model.py`
- 256 tokens covers ~1 500 chars, capturing ~99% of 60–120s chunks fully

### Boundary Context Embedding (Tier 2)
- Added `prev_text[-80:] + " " + text` stitching in the embed batch call so cross-boundary sentences carry semantic context
- Applied in both `api/routers/video.py` (fast-index path) and `services/indexing/pipeline.py`

### Tech Keyword Re-ranking
- Added `_TECH_KEYWORDS` frozenset and `_keyword_bonus()` function in `api/services/search_service.py`
- Results sorted by `similarity + keyword_bonus` when query contains tech keywords

---

## Session 3 — Whisper Fallback + Source Fix

### faster-whisper Audio Fallback
- Added Source 4 (Whisper) in `services/transcript/fetcher.py`
- Lazy-loaded `WhisperModel("tiny", device="cpu", compute_type="int8")` singleton
- Downloads audio via yt-dlp (`--extract-audio --audio-format mp3 --audio-quality 9`), transcribes, converts to segment format
- Updated `requirements.txt`: replaced `openai-whisper` comment with `faster-whisper==1.1.1`

### DB Constraint Fix
- `_try_yt_dlp()` was returning `source="yt_dlp"` which violated `transcripts_source_check` CHECK constraint
- Fixed: changed return to `TranscriptResult(segments, "youtube_auto", lang)`

---

## Session 4 — Small-to-Big (Parent–Child) Retrieval

### New DB Table — `transcript_parent_chunks`
- ORM model added to `db/models.py`: `TranscriptParentChunk` (id, transcript_id, video_id, chunk_index, text, start_time, end_time, word_count, created_at)
- Added `parent_chunk_id` nullable FK column to `TranscriptChunk`
- Added `parent_chunks` relationship to `Transcript`
- Migration: `db/migrations/versions/001_add_parent_chunks.py` — creates table + adds FK column + indexes

### Chunker Rewrite — `services/transcript/chunker.py`
- Added `parent_index: int = 0` field to existing `Chunk` dataclass (backward-compatible)
- Added new `ParentChunk(index, text, start_time, end_time, word_count)` dataclass
- Added `chunk_transcript_with_parents(segments, parent_seconds=120, child_seconds=None, child_overlap=None)` → `tuple[list[ParentChunk], list[Chunk]]`
- Parents: non-overlapping ~120s windows; children: 30–90s chunks within each parent

### Indexing Pipeline — `services/indexing/pipeline.py`
- Switched to `chunk_transcript_with_parents()`
- Inserts `TranscriptParentChunk` rows via ORM `add_all()` + `flush()` to get IDs
- Builds `parent_id_map`, inserts children with `parent_chunk_id`
- Added `"whisper": "whisper"` to `_map_source()`

### Fast-Index Path — `api/routers/video.py`
- Switched to `chunk_transcript_with_parents()` with adaptive child window (capped at 90s)
- Inserts parents via CTE + RETURNING to get ordered IDs
- Inserts children with `parent_chunk_id` in unnest SQL

### Search SQL Updated — `api/services/search_service.py` + `api/routers/video.py`
- `_CHUNK_SEARCH_SQL` and `_CHUNK_SEARCH_ALL_SQL`: added `LEFT JOIN transcript_parent_chunks pc ON tc.parent_chunk_id = pc.id`
- SELECT includes `pc.text AS parent_text, pc.start_time AS parent_start_time, pc.end_time AS parent_end_time`
- Result builder creates `ParentContext(...)` when `parent_text` is present

### Schema Update — `api/schemas/search.py`
- Added `ParentContext(text, start_time, end_time)` Pydantic model
- Added `context: ParentContext | None = None` field to `SearchResult`

### Backfill Script
- Created `scripts/backfill_parent_chunks.py` for re-grouping existing child chunks into parent windows
- Idempotent: skips videos that already have parent chunks; supports `--video-id` for single-video runs

### Tests Added
- `tests/test_chunker.py` — 14 tests covering single-level and two-level chunking (no DB needed)
- `tests/test_search_parent_context.py` — 9 schema/serialization tests for `ParentContext` + `SearchResult`
- `tests/__init__.py`

---

## Session 5 — Extension UI: Parent Context Display

### `chrome-extension/content.js`
- Added `ctxRange` computation (`fmtTime(start) – fmtTime(end)`) per result card
- Added optional `⬡ Context` button in card actions (only rendered when `r.context` exists)
- Added `.yts-context` div with `yts-ctx-range` timestamp and `yts-ctx-text` body (collapsed by default)
- Added `ctxBtn.onclick` toggle handler — expands/collapses `.yts-context` and relabels button
- Fixed pre-existing Clip toggle bug: changed `this.nextElementSibling` → `card.querySelector('.yts-embed')` (the `.yts-context` div was inserted between `.yts-actions` and `.yts-embed`, breaking the old selector)

### `chrome-extension/content.css`
- Added `.yts-btn-ctx` styles (indigo tint, matching badge palette)
- Added `.yts-context` card styles (indigo border, subtle background, max-height 160px with scroll)
- Added `.yts-ctx-range` label styles (small indigo text)
- Added `.yts-ctx-text` body styles (pre-wrap, scrollable)

---

## Session 6 — MVP Analytics Instrumentation + Shareable Clips

### Migration — `db/migrations/versions/002_analytics_and_clips.py`
**search_history** (existing table extended):
- Added `session_id TEXT` — anonymous session tracker, indexed
- Added `top_score FLOAT` — highest similarity score in this search
- Added `referrer_clip_slug TEXT` — set when search originates from a shared clip page
- Added index on `created_at`

**search_clicks** (existing table extended):
- Added `user_id BIGINT` FK → users (nullable, ON DELETE SET NULL)
- Added `session_id TEXT`
- Added `clicked_timestamp FLOAT` — the child chunk start_time that was clicked
- Added index on `search_id`

**shared_clips** (new table):
- `id`, `slug` (UNIQUE, indexed), `video_id` FK, `start_time`, `end_time`, `transcript_text`
- `created_by_user_id` FK (nullable), `view_count` (default 0), `created_at`

### ORM Models — `db/models.py`
- `SearchHistory`: added `session_id`, `top_score`, `referrer_clip_slug`, `created_at` index
- `SearchClicks`: added `user_id`, `session_id`, `clicked_timestamp`; added index on `search_id`
- New `SharedClip` model with `video` and `created_by` relationships

### Schemas
- `api/schemas/search.py`: `SearchRequest` gains `session_id` + `referrer_clip_slug` (both optional); `SearchResponse` gains `search_event_id: int | None`
- `api/schemas/analytics.py` (new): `ClickEventRequest` (search_event_id, video_id, result_position, clicked_timestamp, session_id)
- `api/schemas/clips.py` (new): `CreateClipRequest`, `CreateClipResponse`, `ClipData`

### Search Service — `api/services/search_service.py`
- Replaced background `_save_history()` task with synchronous `INSERT … RETURNING id`
- Writes all new columns (`session_id`, `top_score`, `referrer_clip_slug`) on every live search
- Fixed silent `source IN (...)` CHECK constraint violation — now always writes `'indexed'`
- Returns `search_event_id` in `SearchResponse`
- `SearchRequest.session_id` + `SearchRequest.referrer_clip_slug` wired through

### Search Stream — `api/routers/search.py`
- Added `session_id` + `referrer_clip_slug` query params to `/api/search/stream`
- `meta` SSE event now includes `search_event_id` so the extension can attribute clicks

### Analytics Service — `api/services/analytics_service.py`
- Added `record_click_event(session, search_event_id, user_id, session_id, youtube_video_id, result_position, clicked_timestamp)` — resolves youtube_video_id → internal PK, writes to `search_clicks`

### Clips Service — `api/services/clips_service.py` (new)
- `create_clip()` — resolves youtube_video_id, generates 8-char URL-safe slug via `secrets.token_urlsafe(6)`, retries up to 5× on unique collision
- `get_clip()` — JOINs videos + channels, returns embed URL with start time
- `increment_view_count()` — fire-and-forget UPDATE, swallows errors

### New Routers
- `api/routers/analytics.py` — `POST /api/v1/analytics/click` (fire-and-forget, auth optional)
- `api/routers/clips.py`:
  - `POST /api/v1/clips` — create clip, returns `{slug, share_url}`
  - `GET /api/v1/clips/{slug}` — JSON clip data, increments view_count
  - `GET /clips/{slug}` — public HTML clip page (no auth required)

### Public Clip Page
- Self-contained HTML with OG (`og:title`, `og:description`, `og:image`, `og:url`) and Twitter Card meta tags
- Embedded YouTube player starting at `start_time`
- Transcript snippet panel with timestamp range badge
- Search CTA: pre-filled input + button linking to `/?q=<snippet>&from_clip=<slug>`
- "Watch on YouTube" fallback link
- XSS-safe: all dynamic values escaped via `html.escape()`

### Metrics Module — `api/modules/analytics/metrics.py` (new)
Three async functions (no HTTP endpoint — for admin views):
- `weekly_retention(session)` — % of first-search cohorts (≥14 days old) who returned in days 7–14
- `search_hit_rate(session, days)` — % of searches with ≥1 click over last N days
- `free_to_paid_conversion(session)` — % of free-limit-hitters who upgraded to a paid plan

### App Registration — `api/main.py`
- Registered `analytics.router` and `clips.router`

### Tests
- `tests/test_analytics.py` (11 tests) — schema fields, click-event write, all three metric queries
- `tests/test_clips.py` (13 tests) — schema, create/get/view-count, slug format (8 chars, URL-safe), collision retry, 404 handling, referrer attribution

**Test totals: 50 tests, 50 passed, 0 failures**

---

## Session 7 — Indexing Throughput Optimization (parallel drain + rate-limit semaphore)

**Problem:** 71,630 pending indexing jobs would take ~109 hours sequentially (one video at a time, 2–5s random delay per transcript fetch, batch_size=20 every 5 minutes with 290s idle between batches).

**Goal:** reach ~10h without exceeding YouTube's IP-level rate limit.

### `config.py`
- Added `transcript_concurrent_limit: int = 3` — controls the global semaphore size
- Changed `transcript_request_delay_min` from `2.0` → `1.5` s (no-cookies path)
- Changed `transcript_request_delay_max` from `5.0` → `2.5` s (no-cookies path)
- Added `transcript_request_delay_cookies_min: float = 1.0` — tighter delay when cookies are set
- Added `transcript_request_delay_cookies_max: float = 2.0`

### `services/transcript/fetcher.py`
- Added `_TRANSCRIPT_SEM: asyncio.Semaphore | None` process-wide global (lazy-init on first call via `_get_sem()`)
- `fetch_transcript()` now acquires `_TRANSCRIPT_SEM` before any YouTube request — caps concurrent HTTP requests at `transcript_concurrent_limit` (3) regardless of how many parallel pipeline coroutines are running
- Delay is chosen automatically: **1.0–2.0 s** if `youtube_cookies_path` is configured, **1.5–2.5 s** if not
- Refactored: fetch fallback chain moved into `_fetch_sources()` (called inside the semaphore block)

### `services/indexing/worker.py`
- Replaced sequential `for job in jobs: pipeline.run(job)` with `asyncio.gather(*[_run_one(j) for j in jobs])`
- Added `_db_sem = asyncio.Semaphore(settings.indexing_workers)` inside `_drain_queue_async` to cap concurrent open DB sessions (prevents connection pool exhaustion)
- `_run_one()` inner coroutine: acquires `_db_sem`, opens its own `AsyncSessionLocal`, runs pipeline, returns bool
- `asyncio.gather(..., return_exceptions=True)` — one job failure does not abort the rest of the batch

### `services/indexing/pipeline.py`
- `encode_batch` call wrapped in `await asyncio.to_thread(...)` — frees the event loop during the 1–3 s CPU-bound encoding so other parallel coroutines can progress
- `encode_one` in `_assign_topic` also wrapped in `await asyncio.to_thread(...)`

### `workers/scheduler.py`
- `drain-queue-5m` `batch_size` changed from **20 → 600**
- Reasoning: `batch_size = rate × interval = 2.0 req/s × 300s = 600`; this fills the entire 5-minute window so the semaphore has a continuous supply of jobs with no idle gaps

### Effective throughput after changes

| Config | Global rate | 71,630 jobs |
|---|---|---|
| No cookies | 1.5 req/s | ~13 h |
| With cookies (`youtube_cookies_path` set) | 2.0 req/s | ~10 h |

### Documentation added
- `docs/indexing-pipeline.md` — full pipeline architecture, step-by-step, config reference
- `docs/rate-limiting.md` — rate limit strategy, semaphore math, scenarios, cookies setup guide

---

## Current State

| Area | Status |
|---|---|
| DB migrations | 002 applied — all tables up to date |
| Search accuracy | BGE prefix + max_seq_length=256 + boundary context active |
| Small-to-big retrieval | Parent chunks written on new indexes; extension shows Context button |
| Analytics instrumentation | Every search writes a `search_history` row and returns `search_event_id` |
| Shareable clips | Create + fetch + public HTML page live at `/clips/{slug}` |
| Whisper fallback | Requires `pip install faster-whisper` + ffmpeg on PATH |
| Tier 2 re-index | Run DB cleanup SQL + re-index to activate `max_seq_length=256` embeddings |
| Indexing throughput | Parallel drain (asyncio.gather) + global semaphore(3) + batch_size=600 — 71,630 jobs in ~10h (with cookies) or ~13h (no cookies) |

### Pending (not yet run)
```sql
-- Run once after deploying Tier 2 changes to wipe stale 128-token embeddings:
UPDATE videos SET indexing_status = 'pending', embedding = NULL;
DELETE FROM transcript_chunks;
DELETE FROM transcripts;
```
```bash
# Install Whisper fallback dependency:
pip install faster-whisper
# ffmpeg must also be on PATH
```
