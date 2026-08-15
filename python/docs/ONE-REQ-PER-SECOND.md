# YOUTUBE RATE CONTRACT

The entire system makes **one YouTube-facing request per 2 seconds**, globally, across all
Celery threads. On any 429 the **whole batch stops** and all drains pause for 30/60/90 minutes.
This document is the single source of truth for how that guarantee is implemented.

---

## The Problems (history)

1. **Per-thread rate lock** — each Celery thread's `asyncio.Lock` was independent → 3 req/s instead of 1.
2. **yt-dlp had no rate wait** — fired immediately after youtube-transcript-api.
3. **@retry bypassed the limiter** — and worse, applied 5-min waits to ALL exceptions, not just 429.
4. **Duplicate job processing** — `drain_queue` selected `status IN ('pending','processing')` while
   beat fired every 5 min. A 600-job drain takes 20+ min, so each new tick re-picked the SAME jobs
   the previous drain was still working on (`FOR UPDATE` locks release at commit). With
   `--concurrency=3`, up to 3 drains processed the same jobs in parallel → doubled/tripled the
   real request rate → 429.
5. **In-task retry sleeps** — `_run_one` slept 30–90 min per 429 holding the drain alive for hours,
   guaranteeing the overlap in (4) compounded every 5 minutes.

---

## The Contract

```
Global rate:   1 request per 2 seconds (0.5 req/s), enforced across all threads
On 429:        stop the entire batch (ban flag) — queued requests abort without HTTP
Ban window:    Retry-After header if present, else 30 → 60 → 90 min per consecutive strike
Jobs on 429:   reset to 'pending' (no attempt consumed) — first drain after the ban retries them
yt-dlp:        same global rate gate; NEVER tried as fallback after a 429 (same IP, same ban)
fast=True:     Chrome extension queries skip the rate wait (single video, user is waiting)
```

---

## Implementation

### `services/transcript/fetcher.py`

```python
_GLOBAL_RATE_LOCK = threading.Lock()
_GLOBAL_LAST_REQUEST_AT: float = 0.0
_RATE_INTERVAL: float = 2.0            # seconds between requests
_RATE_BANNED = threading.Event()       # stop-the-batch flag, set on first 429

def _global_rate_wait() -> None:
    # raises TranscriptRateLimitError immediately if _RATE_BANNED is set
    # (checked before the lock, inside the lock, and after the sleep)
    ...
```

- `_global_rate_wait()` is called at the top of `_try_youtube_api` and `_try_yt_dlp`
  (skipped when `fast=True`).
- On 429 detection anywhere: `_RATE_BANNED.set()` — every queued request aborts instantly
  with `TranscriptRateLimitError` instead of hitting YouTube.
- `_extract_retry_after(exc)` walks the exception chain looking for a `Retry-After` response
  header; the value rides on `TranscriptRateLimitError.retry_after`.
- **No @retry decorator** — non-429 errors propagate immediately; retries happen at job level.
- 429 from youtube-transcript-api is re-raised immediately in `_fetch_sources` —
  yt-dlp is NOT tried (same banned IP).
- `clear_rate_ban()` re-arms requests; called only after a ban window has passed.

### `services/indexing/worker.py`

```python
_BAN_DURATIONS = (1800, 3600, 5400)   # 30/60/90 min when no Retry-After header
_BAN_UNTIL: float = 0.0               # monotonic; drains before this skip instantly
_BAN_STRIKES: int = 0                 # consecutive rate-limited drains → escalation
_DRAIN_LOCK = threading.Lock()        # one drain at a time, ticks skip if busy
_STALE_PROCESSING_AFTER = timedelta(hours=2)
```

`drain_queue` (beat fires every 5 min, `batch_size=600`):
1. `_DRAIN_LOCK.acquire(blocking=False)` — if a previous drain still runs, **skip this tick**.
2. If `now < _BAN_UNTIL` — ban window active, **skip this tick** (logs remaining time).
3. `clear_rate_ban()`, recover `processing` jobs older than 2 h (crashed workers) → `pending`.
4. Claim **only `status='pending'`** rows with `FOR UPDATE SKIP LOCKED`, mark `processing`,
   commit. Pending-only selection is what makes concurrent processes (another drain,
   `run_indexing.py`) unable to double-claim.
5. Run jobs through the pipeline (`INDEXING_WORKERS` caps parallelism — currently 1).
6. On `TranscriptRateLimitError`: the pipeline already reset the job to `pending`;
   `_run_one` returns the exception (no sleep, no retry in-task).
7. After the batch: if any 429s — `_BAN_UNTIL = now + (Retry-After or _BAN_DURATIONS[strike])`,
   `_BAN_STRIKES += 1`. A clean batch resets strikes to 0.

The retry schedule is therefore unchanged (30/60/90 min, Retry-After preferred) but lives at
the **drain level**: jobs wait in the DB as `pending`, not in sleeping tasks.

### `run_indexing.py --watch`

Same behavior standalone: when a cycle reports rate-limited jobs, the loop pauses
(Retry-After or 30/60/90 min escalation), then calls `clear_rate_ban()` and continues.

---

## Request Flow

```
beat tick (every 5 min)
  └─ drain_queue
       ├─ previous drain running?  → skip tick
       ├─ ban window active?       → skip tick
       ├─ recover stale 'processing' (>2h) → 'pending'
       ├─ claim ≤600 'pending' jobs (FOR UPDATE SKIP LOCKED → 'processing')
       └─ for each job (INDEXING_WORKERS at a time):
            └─ fetch_transcript(video_id)
                 └─ _try_youtube_api
                      ├─ _global_rate_wait()   ← 2s gate; aborts if banned
                      └─ 429? → ban flag set → batch stops → jobs → 'pending'
                                 → drain sets _BAN_UNTIL → next ticks skip
```

---

## Thread Safety

| Primitive | Type | Scope | Why |
|-----------|------|-------|-----|
| `_GLOBAL_RATE_LOCK` | `threading.Lock` | process-wide | serialises all threads into 1 req/2s |
| `_RATE_BANNED` | `threading.Event` | process-wide | stop-the-batch on first 429 |
| `_DRAIN_LOCK` | `threading.Lock` | process-wide | one drain at a time across Celery threads |
| `_tl.sem` | `asyncio.Semaphore` | per-thread | event-loop bound; `threading.local` avoids cross-loop sharing |
| `EmbeddingModel._encode_lock` | `threading.Lock` | process-wide | prevents CPU contention in PyTorch |

`threading.Lock` + `time.sleep` is the correct primitive for cross-thread synchronization.
`asyncio.Lock` / `asyncio.Semaphore` must NOT be shared across threads — each `asyncio.run()`
call creates a new event loop and the primitive becomes "bound to a different event loop".

Cross-**process** safety (Celery worker + `run_indexing.py` at once) comes from the
pending-only claim: a row turns `processing` inside the claiming transaction, so the other
process's `WHERE status='pending'` can never see it. Don't run both routinely — they share
the IP budget.

---

## Tuning

| Setting | Default | Notes |
|---------|---------|-------|
| `_RATE_INTERVAL` | `2.0 s` | raise to `3.0` if 429s persist |
| `_BAN_DURATIONS` | `1800/3600/5400 s` | drain-level pause per consecutive 429 strike |
| `_STALE_PROCESSING_AFTER` | `2 h` | orphaned 'processing' job recovery |
| `INDEXING_WORKERS` (.env) | `1` | per-drain parallelism; raise only when 429-free for days |
| `batch_size` | `600` | max claim per drain; drains overlap-guarded so size is safe |

---

*Rebuilt 2026-06-12 after the duplicate-processing incident (overlapping drains re-claiming
'processing' jobs). Revisit if YouTube tightens quotas or if a cookie strategy is added.*
