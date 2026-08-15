r"""Migration: recover videos that got stuck during the 429-blocking period.

Two operations:
  1. indexing_jobs: status 'skipped' -> 'pending'   (re-attempt blocked videos)
  2. videos:        status 'processing' -> 'pending' (un-stick rows frozen by the
                    old bug where video status wasn't reset on skip/fail)

After this, start the worker (proxy ON) and it will re-index them. Videos that
genuinely have no captions will skip again — cleanly this time (bug is fixed).

IMPORTANT: run with the Celery worker STOPPED to avoid racing in-flight jobs.

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\migrate_reindex_stuck.py
"""
import asyncio

from sqlalchemy import text

from db.session import AsyncSessionLocal


async def scalar(s, sql):
    return (await s.execute(text(sql))).scalar()


async def main():
    async with AsyncSessionLocal() as s:
        # ── before ──
        skipped = await scalar(s, "SELECT count(*) FROM indexing_jobs WHERE status='skipped'")
        proc_vid = await scalar(s, "SELECT count(*) FROM videos WHERE indexing_status='processing'")
        pending_before = await scalar(s, "SELECT count(*) FROM indexing_jobs WHERE status='pending'")
        print("BEFORE:")
        print(f"  skipped jobs            : {skipped}")
        print(f"  'processing' videos     : {proc_vid}")
        print(f"  pending jobs            : {pending_before}")

        # ── 1. skipped jobs -> pending ──
        r1 = await s.execute(text(
            "UPDATE indexing_jobs SET status='pending', attempts=0, error_message=NULL "
            "WHERE status='skipped'"))

        # ── 2. stuck 'processing' videos -> pending ──
        r2 = await s.execute(text(
            "UPDATE videos SET indexing_status='pending' WHERE indexing_status='processing'"))

        await s.commit()

        pending_after = await scalar(s, "SELECT count(*) FROM indexing_jobs WHERE status='pending'")
        print("\nDONE:")
        print(f"  skipped jobs   -> pending : {r1.rowcount}")
        print(f"  videos un-stuck -> pending: {r2.rowcount}")
        print(f"  pending jobs now          : {pending_after}  (was {pending_before})")
        print("\nNext: start the worker (proxy ON) and it will re-index these.")


asyncio.run(main())
