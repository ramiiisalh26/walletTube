"""Backfill industry_id on indexing_jobs rows that have a channel_id but no industry_id."""
import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        r = await session.execute(text(
            "UPDATE indexing_jobs j "
            "SET industry_id = ch.primary_industry_id "
            "FROM channels ch "
            "WHERE j.channel_id = ch.id "
            "  AND j.industry_id IS NULL "
            "  AND ch.primary_industry_id IS NOT NULL"
        ))
        await session.commit()
        print(f"Backfilled industry_id on {r.rowcount} indexing_jobs rows")

asyncio.run(main())
