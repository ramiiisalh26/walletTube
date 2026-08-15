import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        r = await session.execute(text(
            "UPDATE indexing_retry_jobs "
            "SET retry_exhausted = FALSE, status = 'pending', attempts = 0 "
            "WHERE error_message = 'No transcript available' "
            "  AND status = 'permanently_failed'"
        ))
        await session.commit()
        print(f"Reset {r.rowcount} jobs back to pending — whisper cron will now process them")

asyncio.run(main())
