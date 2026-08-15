r"""Print a window of a video's transcript around a keyword (to read one section).

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_extract_section.py R8h_gpSpEVU "text to video"
"""
import asyncio
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from db.session import AsyncSessionLocal

VID = sys.argv[1] if len(sys.argv) > 1 else "R8h_gpSpEVU"
KW = (sys.argv[2] if len(sys.argv) > 2 else "text to video").lower()
WINDOW = int(sys.argv[3]) if len(sys.argv) > 3 else 3500


async def main() -> None:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text(
            "SELECT tc.text FROM transcript_chunks tc JOIN videos v ON tc.video_id=v.id "
            "WHERE v.youtube_video_id=:v ORDER BY tc.chunk_index"), {"v": VID})).fetchall()
    full = " ".join(r[0] for r in rows)
    i = full.lower().find(KW)
    if i < 0:
        print(f"'{KW}' not found")
        return
    print(full[max(0, i - 500): i + WINDOW])


asyncio.run(main())
