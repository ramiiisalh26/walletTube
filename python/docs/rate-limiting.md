# Rate Limiting — Transcript Fetching Strategy

How the system avoids YouTube IP bans while maximising throughput.

---

## What gets rate-limited and by what

There are **two separate rate-limit systems** in this project. Do not confuse them:

| System | What it guards | Quota source |
|---|---|---|
| YouTube Data API quota | `/discover`, `/channel` crawl (search + metadata) | Per-day API key quota (10,000 units/day) |
| YouTube IP throttling (429) | Transcript fetches via `youtube-transcript-api` and `yt-dlp` | Per-IP, no official limit documented |

**Transcript fetching does NOT use the YouTube Data API key.**
Both `youtube-transcript-api` and `yt-dlp` scrape YouTube's internal caption endpoint
(`https://www.youtube.com/...`) directly. YouTube throttles this at the IP level.

---

## The global semaphore

`services/transcript/fetcher.py` holds a process-wide semaphore:

```python
_TRANSCRIPT_SEM = asyncio.Semaphore(settings.transcript_concurrent_limit)  # default 3
```

All parallel pipeline coroutines share this single semaphore. At most
`transcript_concurrent_limit` (3) coroutines can be inside a YouTube request at once,
regardless of how many jobs `asyncio.gather` is running in parallel.

### Why a global semaphore and not just a per-job delay?

A per-job random delay (the old approach) works fine when jobs are sequential.
With `asyncio.gather` running 600 jobs in parallel, all 600 would sleep their delay
simultaneously then fire 600 requests at once — immediate 429.

The semaphore serialises access to the delay+request block, so requests are spaced out
even when hundreds of coroutines are waiting.

---

## Delay selection (inside the semaphore)

Once a coroutine acquires the semaphore slot, it sleeps a random delay before the
first network call:

```python
if _COOKIES:
    delay = random.uniform(
        settings.transcript_request_delay_cookies_min,   # 1.0 s
        settings.transcript_request_delay_cookies_max,   # 2.0 s
    )
else:
    delay = random.uniform(
        settings.transcript_request_delay_min,           # 1.5 s
        settings.transcript_request_delay_max,           # 2.5 s
    )
```

The delay is **inside** the semaphore so it counts as holding the slot — this is
intentional. If the delay were outside, multiple coroutines would all finish sleeping
at the same time and pile into the semaphore together.

---

## Effective global request rate

```
global_rate = sem_limit / avg_delay
```

| Configuration | sem | avg delay | Rate | Safe? |
|---|---|---|---|---|
| No cookies (current default) | 3 | 2.0 s | 1.5 req/s | Yes — under ~1.5/s IP limit |
| With cookies | 3 | 1.5 s | 2.0 req/s | Yes — under ~2/s authenticated limit |
| Aggressive (no cookies) | 3 | 1.0 s | 3.0 req/s | Risk of 429 |

Community benchmarks for YouTube's IP-level tolerance:
- **Unauthenticated:** safe up to ~1–1.5 req/s sustained
- **Authenticated (cookies):** safe up to ~2 req/s sustained

---

## Scenarios & timing (71,630 pending jobs)

| Scenario | sem | Delay | Rate | Total time |
|---|---|---|---|---|
| Old sequential (baseline) | — | 2–5 s avg | 0.18/s | ~109 h |
| Scenario 3 — no cookies | 3 | 1.5–2.5 s | 1.5/s | ~13 h |
| Scenario 4 — with cookies | 3 | 1.0–2.0 s | 2.0/s | ~10 h |

---

## Handling 429 responses

When either `youtube-transcript-api` or `yt-dlp` returns a 429:

1. `TranscriptRateLimitError` is raised in `fetcher.py`
2. `IndexingPipeline._rate_limit_job()` sets `job.status = 'pending'` **without**
   incrementing `job.attempts` — the job is not penalised
3. The job will be picked up again in the next `drain_queue` window (5 minutes later)
4. Tenacity retry (`stop_after_attempt(3), wait_exponential(min=2, max=10)`) handles
   transient errors before surfacing as a 429

---

## Cookies setup (to reach Scenario 4 / 10h)

1. Open Chrome, log into YouTube
2. Install a cookie export extension (e.g. "Get cookies.txt LOCALLY")
3. Export cookies for `youtube.com` in **Netscape format**
4. Save the file anywhere (e.g. `cookies.txt` in the project root — already gitignored)
5. Set in `.env`:
   ```
   YOUTUBE_COOKIES_PATH=cookies.txt
   ```
6. Restart the worker — `fetcher.py` picks up `_COOKIES` at module load time and
   automatically switches to the 1–2 s delay range

Cookies expire after ~1 week. Re-export and replace the file when you see 429s
returning more frequently than before.

---

## Tuning guide

| Goal | Change |
|---|---|
| Safer (fewer 429s) | Increase `transcript_request_delay_min/max` or decrease `transcript_concurrent_limit` |
| Faster (within safe rate) | Set `youtube_cookies_path`, keeps sem=3 but cuts delays to 1–2s |
| More DB parallelism | Increase `indexing_workers` (must also grow DB pool in `db/session.py`) |
| Bigger batches per window | Increase `batch_size` in `scheduler.py` beat_schedule kwargs |
