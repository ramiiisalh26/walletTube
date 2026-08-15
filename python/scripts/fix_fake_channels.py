"""Fix duplicate/fake channels created by browser extension submissions."""
import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal


async def main():
    async with AsyncSessionLocal() as session:

        # Step 1: find fake channels that have a real UC-prefixed counterpart
        pairs = (await session.execute(text(
            "SELECT fake.id AS fake_id, real.id AS real_id, fake.name "
            "FROM channels fake "
            "JOIN channels real ON real.name = fake.name "
            "  AND real.youtube_channel_id LIKE 'UC%' "
            "WHERE fake.youtube_channel_id NOT LIKE 'UC%'"
        ))).fetchall()

        for p in pairs:
            r1 = await session.execute(
                text("UPDATE videos SET channel_id = :real WHERE channel_id = :fake"),
                {"real": p.real_id, "fake": p.fake_id},
            )
            r2 = await session.execute(
                text("UPDATE indexing_jobs SET channel_id = :real WHERE channel_id = :fake"),
                {"real": p.real_id, "fake": p.fake_id},
            )
            print(f"  Merged {p.name!r}: {r1.rowcount} videos, {r2.rowcount} jobs -> real channel {p.real_id}")

        await session.commit()

        # Step 2: delete fake channels that now have no videos
        deleted = await session.execute(text(
            "DELETE FROM channels "
            "WHERE youtube_channel_id NOT LIKE 'UC%' "
            "  AND NOT EXISTS (SELECT 1 FROM videos WHERE channel_id = channels.id)"
        ))
        await session.commit()
        print(f"  Deleted {deleted.rowcount} empty fake channels")

        # Step 3: report remaining fakes that still hold videos (no real counterpart)
        remaining = (await session.execute(text(
            "SELECT ch.name, ch.youtube_channel_id, COUNT(v.id) AS videos "
            "FROM channels ch "
            "LEFT JOIN videos v ON v.channel_id = ch.id "
            "WHERE ch.youtube_channel_id NOT LIKE 'UC%' "
            "GROUP BY ch.id, ch.name, ch.youtube_channel_id "
            "ORDER BY videos DESC"
        ))).fetchall()

        print(f"\nRemaining fake channels (no real counterpart — OK to leave): {len(remaining)}")
        for r in remaining:
            print(f"  {r.name!r}  {r.youtube_channel_id}  videos={r.videos}")


asyncio.run(main())
