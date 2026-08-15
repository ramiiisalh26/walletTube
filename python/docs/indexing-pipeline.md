# Indexing Pipeline — Architecture & Flow

How a video goes from a YouTube video ID to fully searchable transcript chunks.

---

## Overview

```
IndexingJob (DB)
      │
      ▼
drain_queue  (Celery task, every 5 min, batch=600)
      │  asyncio.gather — all 600 jobs in parallel
      │
      ├─► _run_one(job)  ─────────────────────────────────────────────┐
      │       │                                                        │  × 600
      │       ▼                                                        │
      │   IndexingPipeline.run()                                       │
      │       │                                                        │
      │       ├─ 1. get_or_create_video  (DB)                         │
      │       ├─ 2. fetch_transcript     (YouTube — rate-limited sem)  │
      │       ├─ 3. classify_industry    (VideoClassifier)             │
      │       ├─ 4. chunk_transcript_with_parents  (chunker)           │
      │       ├─ 5. resolve_language     (DB)                          │
      │       ├─ 6. write Transcript row (DB flush)                    │
      │       ├─ 7. write ParentChunk rows (DB flush)                  │
      │       ├─ 8. encode_batch         (EmbeddingModel — in thread)  │
      │       ├─ 9. write TranscriptChunk rows (DB)                    │
      │       ├─ 10. compute video-level mean embedding (DB update)    │
      │       └─ 11. mark job done       (DB commit)                   │
      │                                                                │
      └────────────────────────────────────────────────────────────────┘
```

---

## Step-by-step Detail

### 1. Job Dequeue — `worker.py`

`drain_queue(batch_size=600)` is triggered by Celery Beat every 300 seconds.

```python
# Fetch 600 pending jobs ordered by priority DESC, created_at ASC
SELECT ... FROM indexing_jobs
WHERE status = 'pending' AND attempts < max_attempts
ORDER BY priority DESC, created_at ASC
LIMIT 600
FOR UPDATE SKIP LOCKED   -- safe for multiple concurrent workers
```

All 600 jobs are then launched simultaneously via `asyncio.gather`. A DB semaphore
(`asyncio.Semaphore(settings.indexing_workers)`, default 4) caps open DB sessions to
avoid exhausting the connection pool.

### 2. Transcript Fetch — `services/transcript/fetcher.py`

Before any YouTube request, the coroutine acquires the **global rate-limit semaphore**
(`_TRANSCRIPT_SEM = asyncio.Semaphore(3)`). This is shared across all 600 parallel
coroutines in the process, so at most 3 YouTube requests are in-flight at once.

Fallback chain inside the semaphore slot:

| Priority | Source | How |
|---|---|---|
| 1 | `youtube-transcript-api` — manual captions | HTTP scrape |
| 2 | `youtube-transcript-api` — auto-generated | HTTP scrape |
| 3 | `yt-dlp` — subtitle download | CLI subprocess |
| 4 | `faster-whisper` — audio transcription | local model (optional) |

A random delay is applied **inside** the semaphore slot before the first request:
- With `youtube_cookies_path` set → **1.0–2.0 s**
- Without cookies → **1.5–2.5 s**

On `429 Too Many Requests` → raises `TranscriptRateLimitError` → job stays `pending`
(attempt counter not incremented) and will be retried in the next drain window.

### 3. Classification — `services/classifier/classifier.py`

Skipped if `video.primary_industry_id` is already set (e.g. from channel-level tag).
Otherwise runs a 4-level classifier:
- Level 1: title keyword match
- Level 2: description keyword match
- Level 3: channel slug heuristic
- Level 4: cosine similarity against industry embeddings (loaded from DB at worker start)

### 4. Chunking — `services/transcript/chunker.py`

Two-level hierarchy:

```
Parent chunks  (~120s, non-overlapping, no embedding)
  └─ Child chunks  (~30s, 5s overlap, embedded for search)
```

`chunk_transcript_with_parents(segments)` returns `(parents, children)`.

Each child carries `prev_text` (last 200 chars of previous child) and `next_text`
(first 200 chars of next child) for cross-boundary context at query time.

### 5–7. DB Writes (transcript + parent rows)

`Transcript` row is inserted first and flushed to get its `id`.
`TranscriptParentChunk` rows are inserted next and flushed to get their `id`s.
A `parent_id_map` is built: `{parent.index: parent_row.id}`.

### 8. Embedding — `services/embedding/model.py`

```python
vectors = await asyncio.to_thread(self._model.encode_batch, embed_texts)
```

Runs in a thread pool so the event loop remains free for the other ~597 parallel
coroutines while this CPU-bound call executes (~1–3 s on CPU, <0.5 s on GPU).

Each text sent for embedding is pre-pended with the last 80 chars of the previous
chunk to preserve cross-boundary semantics:

```python
embed_text = (chunk.prev_text[-80:] + " " + chunk.text) if chunk.prev_text else chunk.text
```

Model: `BAAI/bge-small-en-v1.5` — 384-dimensional, max_seq_length=256.

### 9–11. Write & Commit

`TranscriptChunk` rows (with embeddings + `parent_chunk_id` FK) are bulk-inserted via
`session.add_all()`. The video-level embedding is set to the L2-normalised mean of all
child vectors. Job status set to `done`. Single `session.commit()` covers everything.

---

## Key Files

| File | Role |
|---|---|
| `workers/scheduler.py` | Celery Beat schedule — triggers `drain_queue` every 5 min |
| `services/indexing/worker.py` | `drain_queue` task + `asyncio.gather` parallel loop |
| `services/indexing/pipeline.py` | `IndexingPipeline.run()` — full per-video logic |
| `services/transcript/fetcher.py` | Transcript fetch with global rate-limit semaphore |
| `services/transcript/chunker.py` | Two-level chunker (parents + children) |
| `services/embedding/model.py` | `EmbeddingModel` singleton — `encode_batch` / `encode_one` |
| `config.py` | All tunable parameters |

---

## Config Reference (indexing-related)

```ini
# How many jobs drain_queue processes per 5-min window
# batch_size = rate × 300s
#   with cookies  → 2.0/s × 300 = 600
#   no cookies    → 1.5/s × 300 = 450
# Set to 600 to saturate the window when cookies are configured.
# Without cookies the semaphore self-throttles to 1.5/s.
batch_size = 600   (set in scheduler.py beat_schedule kwargs)

indexing_workers = 4                  # DB connection pool cap (semaphore in worker.py)
embedding_batch_size = 64             # tokens per encode_batch call
chunk_duration_seconds = 30           # child chunk target window
chunk_overlap_seconds = 5             # child chunk overlap
transcript_concurrent_limit = 3       # global semaphore size
transcript_request_delay_min = 1.5    # no-cookies delay floor (s)
transcript_request_delay_max = 2.5    # no-cookies delay ceiling (s)
transcript_request_delay_cookies_min = 1.0   # cookies delay floor (s)
transcript_request_delay_cookies_max = 2.0   # cookies delay ceiling (s)
youtube_cookies_path = ""             # path to Netscape cookies.txt (leave empty = no cookies)
```
