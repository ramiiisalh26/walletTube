import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        r = await session.execute(text(
            "UPDATE indexing_retry_jobs "
            "SET retry_exhausted = TRUE, status = 'permanently_failed' "
            "WHERE error_message = 'No transcript available'"
        ))
        await session.commit()
        print(f"Marked {r.rowcount} retry jobs as permanently_failed")

asyncio.run(main())
