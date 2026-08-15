r"""Fetch one video's transcript (DB first, else live via the proxy) and print it.

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_get_transcript.py R8h_gpSpEVU
"""
import asyncio
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from db.session import AsyncSessionLocal
from services.transcript.fetcher import fetch_transcript

VID = sys.argv[1] if len(sys.argv) > 1 else "R8h_gpSpEVU"


async def main() -> None:
    async with AsyncSessionLocal() as s:
        title = (await s.execute(text(
            "SELECT title FROM videos WHERE youtube_video_id=:v"), {"v": VID})).scalar()
        rows = (await s.execute(text(
            "SELECT tc.text FROM transcript_chunks tc JOIN videos v ON tc.video_id=v.id "
            "WHERE v.youtube_video_id=:v ORDER BY tc.chunk_index"), {"v": VID})).fetchall()

    if rows:
        full = " ".join(r[0] for r in rows)
        print(f"TITLE: {title}")
        print(f"[from DB] {len(full.split())} words\n")
        print(full)
    else:
        print(f"[not indexed] fetching {VID} live via proxy...")
        r = await fetch_transcript(VID)
        if r:
            full = " ".join(seg["text"] for seg in r.segments)
            print(f"[fetched] source={r.source}  {len(full.split())} words\n")
            print(full)
        else:
            print("no transcript available for this video")


asyncio.run(main())
