import asyncio
import concurrent.futures
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

from services.transcript.fetcher import (
    TranscriptRateLimitError,
    TranscriptUnavailableError,
    fetch_transcript,
    reset_semaphore,
)

VIDEO_IDS = [
    "dQw4w9WgXcQ",  # Rick Astley — manual EN captions
    "jNQXAC9IVRw",  # First YouTube video — auto captions
    "9bZkp7q19f0",  # Gangnam Style — popular, has captions
    "kJQP7kiw5Fk",  # Despacito — has captions
]


def fetch_in_thread(video_id):
    reset_semaphore()
    t0 = time.monotonic()
    try:
        result = asyncio.run(fetch_transcript(video_id))
        src = (
            f"{result.source} ({result.language_code}) {len(result.segments)} segs"
            if result
            else "no transcript"
        )
    except TranscriptRateLimitError:
        src = "429 RATE LIMITED"
    except TranscriptUnavailableError:
        src = "UNAVAILABLE (yt-dlp fallback pending)"
    elapsed = time.monotonic() - t0
    return video_id, src, elapsed


t_start = time.monotonic()
print(f"Starting {len(VIDEO_IDS)} fetches across 3 threads ...\n")

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    futures = [pool.submit(fetch_in_thread, vid) for vid in VIDEO_IDS]
    for f in concurrent.futures.as_completed(futures):
        vid, src, elapsed = f.result()
        wall = time.monotonic() - t_start
        print(f"  [{wall:5.1f}s wall | {elapsed:4.1f}s job]  {vid} → {src}")

total = time.monotonic() - t_start
print(f"\nDone in {total:.1f}s — expect ~{len(VIDEO_IDS)}s if rate limiter is working")
