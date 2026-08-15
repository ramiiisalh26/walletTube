import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(
            "SELECT t.id AS transcript_id, t.video_id, l.code AS lang, "
            "COUNT(tc.id) AS segment_count "
            "FROM transcripts t "
            "JOIN languages l ON l.id = t.language_id "
            "LEFT JOIN transcript_chunks tc ON tc.transcript_id = t.id "
            "WHERE t.source = 'whisper' "
            "GROUP BY t.id, t.video_id, l.code"
        ))).fetchall()

        flagged = 0
        for r in rows:
            segs = r.segment_count or 0
            reason = "low_segments" if segs < 20 else "whisper_transcribed"
            exists = (await session.execute(
                text("SELECT 1 FROM video_language_review WHERE transcript_id = :tid"),
                {"tid": r.transcript_id},
            )).fetchone()
            if not exists:
                await session.execute(text(
                    "INSERT INTO video_language_review "
                    "(video_id, transcript_id, detected_lang, segment_count, reason) "
                    "VALUES (:vid, :tid, :lang, :segs, :reason)"
                ), {"vid": r.video_id, "tid": r.transcript_id,
                    "lang": r.lang, "segs": segs, "reason": reason})
                flagged += 1

        await session.commit()
        low = sum(1 for r in rows if (r.segment_count or 0) < 20)
        print(f"Total whisper transcripts: {len(rows)}")
        print(f"Low segment count (<20):   {low}")
        print(f"Flagged for review:        {flagged}")

asyncio.run(main())
