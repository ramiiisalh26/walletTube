"""Re-queue recently skipped jobs so they retry with the yt-dlp fallback now added."""
import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        r = await session.execute(text(
            "UPDATE indexing_jobs "
            "SET status = 'pending', attempts = 0, error_message = NULL "
            "WHERE status = 'skipped' "
            "  AND error_message = 'No transcript available' "
            "  AND updated_at >= NOW() - INTERVAL '24 hours'"
        ))
        await session.commit()
        print(f"Re-queued {r.rowcount} recently skipped jobs for retry")

asyncio.run(main())
