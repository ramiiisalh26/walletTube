r"""Measure proxy bandwidth + throughput on a sample of pending videos.

Runs a known number of videos through the REAL transcript-fetch path, so you can
read the "GB used" off your proxy provider's dashboard and divide by the count to
get your true per-video bandwidth. Multiply by your backlog size to pick a plan.

    # 1. note GB used on the proxy dashboard
    # 2. run a sample:
    $env:PYTHONPATH="."; .venv\Scripts\python scripts\measure_proxy_bandwidth.py --count 200 --concurrency 10
    # 3. read GB used again on the dashboard; (after - before) / count = GB per video

Notes:
- Fetch-only: does NOT index or change job status, so it's safe to re-run.
- Effective concurrency is bumped to at least --concurrency for this run.
- Works with or without a proxy (shows which mode it's in).
"""
import argparse
import asyncio
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from config import settings
from db.session import AsyncSessionLocal
from services.transcript.fetcher import (
    TranscriptRateLimitError,
    fetch_transcript,
    proxy_enabled,
    reset_semaphore,
)


async def _load_pending(count: int) -> list[str]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text(
            "SELECT youtube_video_id FROM indexing_jobs WHERE status='pending' "
            "ORDER BY priority DESC, created_at ASC LIMIT :n"), {"n": count})).fetchall()
    return [r[0] for r in rows]


async def main(count: int, concurrency: int) -> None:
    # Raise the internal per-thread semaphore so the requested concurrency
    # actually happens (otherwise it's capped at transcript_concurrent_limit).
    settings.transcript_concurrent_limit = max(settings.transcript_concurrent_limit, concurrency)
    reset_semaphore()

    vids = await _load_pending(count)
    print(f"proxy mode: {'ON (rotating residential)' if proxy_enabled() else 'OFF (direct connection)'}")
    print(f"testing {len(vids)} videos at concurrency={concurrency}\n")
    if not vids:
        print("no pending videos found — nothing to measure")
        return

    sem = asyncio.Semaphore(concurrency)
    stats = {"ok": 0, "blocked": 0, "none": 0, "error": 0, "segments": 0}

    async def _one(vid: str) -> None:
        async with sem:
            try:
                r = await fetch_transcript(vid)
                if r:
                    stats["ok"] += 1
                    stats["segments"] += len(r.segments)
                else:
                    stats["none"] += 1
            except TranscriptRateLimitError:
                stats["blocked"] += 1
            except Exception:
                stats["error"] += 1

    t0 = time.monotonic()
    await asyncio.gather(*[_one(v) for v in vids])
    dt = time.monotonic() - t0

    print(f"done in {dt:.1f}s  ({len(vids) / dt:.1f} videos/sec)\n")
    print(f"  got transcript : {stats['ok']}  ({stats['segments']} segments total)")
    print(f"  blocked (429)  : {stats['blocked']}")
    print(f"  no transcript  : {stats['none']}")
    print(f"  errored        : {stats['error']}")
    print("\n" + "─" * 60)
    print(f"Now read GB used on your proxy dashboard, then:")
    print(f"   GB_per_video = (GB_after − GB_before) / {len(vids)}")
    print(f"   backlog_GB   = GB_per_video × 450000")
    print("─" * 60)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Measure proxy bandwidth on pending videos")
    p.add_argument("--count", type=int, default=200, help="videos to sample (default 200)")
    p.add_argument("--concurrency", type=int, default=10, help="parallel fetches (default 10)")
    args = p.parse_args()
    asyncio.run(main(args.count, args.concurrency))
