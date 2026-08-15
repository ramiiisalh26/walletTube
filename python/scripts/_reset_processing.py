r"""One-off: reset stuck 'processing' jobs back to 'pending'.

Run after stopping the Celery worker, so jobs claimed by dead/duplicated
drains become claimable again:
    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_reset_processing.py
"""
import asyncio

from sqlalchemy import text

from db.session import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(text(
            "UPDATE indexing_jobs SET status = 'pending' WHERE status = 'processing'"
        ))
        await session.commit()
        print(f"Reset {result.rowcount} jobs from 'processing' to 'pending'")


asyncio.run(main())
