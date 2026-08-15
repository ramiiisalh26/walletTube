# YTSearch Cron Jobs

## Indexing Worker (run_indexing.py)

Directly processes `indexing_jobs` table — fetches transcripts, chunks, embeds, and writes to DB.
This is the **main pipeline** that does the actual video indexing work.

```powershell
# Process 5 pending jobs then exit (default)
python run_indexing.py

# Process up to 50 jobs then exit
python run_indexing.py --limit 50

# Index one specific YouTube video by ID
python run_indexing.py --video-id dQw4w9WgXcQ

# Watch mode — loop forever, drain queue continuously (recommended for production)
python run_indexing.py --watch

# Watch mode with custom batch size and sleep interval
python run_indexing.py --watch --batch 20 --interval 60
```

> **This must be running** for videos to actually get indexed. The Celery scheduler queues jobs into `indexing_jobs`, but `run_indexing.py` is what processes them.

---

## Celery Scheduler (cron jobs)

### Development (Windows) — both in one terminal
```powershell
celery -A workers.scheduler worker --beat --loglevel=info --pool=solo
```

### Production (Linux) — two separate terminals
```bash
# Terminal 1 — Worker (executes tasks)
celery -A workers.scheduler worker --loglevel=info --concurrency=4

# Terminal 2 — Beat (fires tasks on schedule)
celery -A workers.scheduler beat --loglevel=info
```

> **Note:** Always use `--pool=solo` on Windows. Remove it on Linux production servers.

---

## Cron Schedule

| Task | Schedule | What it does |
|------|----------|--------------|
| `drain_queue` | Every 5 min | Picks up pending `indexing_jobs` and indexes videos (transcript → chunk → embed) |
| `fetch_trending` | Every hour | Fetches trending videos from YouTube (US, GB, IN) in parallel |
| `scan_monitored_channels` | Every 6 hours | Checks all 160 monitored channels for new uploads (5 channels in parallel) |
| `discover_channels` | Daily at midnight UTC | Searches YouTube for new programming channels by topic rotation |
| `whisper_retry` | Every 4 hours | Transcribes videos with no YouTube captions using faster-whisper (2 in parallel) |
| `bulk_discover_channels` | Every Saturday 2 AM UTC | Discovers new channels via yt-dlp search + GitHub curated lists, queues all their videos |
| `reclassify_uncategorised` | Every Sunday 4 AM UTC | Re-classifies videos with no industry assigned |

---

## Manually Trigger a Task

Use these while the worker is running to fire a task immediately without waiting for the schedule.

```powershell
# Drain the indexing queue now
celery -A workers.scheduler call indexing.drain_queue

# Fetch trending videos now
celery -A workers.scheduler call workers.scheduler.fetch_trending

# Scan all monitored channels for new videos now
celery -A workers.scheduler call workers.scheduler.scan_monitored_channels

# Discover new channels by topic now
celery -A workers.scheduler call workers.scheduler.discover_channels

# Run Whisper retry on no-transcript videos now
celery -A workers.scheduler call workers.scheduler.whisper_retry

# Discover new channels via yt-dlp + GitHub now
celery -A workers.scheduler call workers.scheduler.bulk_discover_channels

# Re-classify uncategorised videos now
celery -A workers.scheduler call workers.scheduler.reclassify_uncategorised
```

---

## Manually Trigger with Arguments

```powershell
# Drain queue with custom batch size
celery -A workers.scheduler call indexing.drain_queue --args="[50]"

# Whisper retry with custom batch size
celery -A workers.scheduler call workers.scheduler.whisper_retry --kwargs='{"batch_size": 5}'
```

---

## Monitor Tasks (Flower UI)

```powershell
celery -A workers.scheduler flower --port=5555
```

Open `http://localhost:5555` in your browser to see task history, retries, and worker status.

---

## Useful Maintenance Commands

### Check queue depth
```powershell
celery -A workers.scheduler inspect reserved
```

### Purge all pending tasks from the queue
```powershell
celery -A workers.scheduler purge
```

### Check worker status
```powershell
celery -A workers.scheduler status
```

### Reset no-transcript jobs so Whisper retries them
```powershell
python -m scripts._reset_whisper_jobs
```

### Check pending Whisper jobs count
```powershell
python -c "
import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal
async def check():
    async with AsyncSessionLocal() as s:
        n = (await s.execute(text(\"SELECT COUNT(*) FROM indexing_retry_jobs WHERE error_message = 'No transcript available' AND retry_exhausted = FALSE AND status = 'pending'\"))).scalar()
        print(f'Pending whisper jobs: {n}')
asyncio.run(check())
"
```

---

## GPU Production Setup

On a GPU server, add these to `.env` before starting workers:

```env
DEVICE=cuda
WHISPER_MODEL_SIZE=large-v3
WHISPER_COMPUTE_TYPE=float16
```

This automatically applies to both the embedding model (SentenceTransformer) and faster-whisper — no code changes needed.
