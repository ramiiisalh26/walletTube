"""
Shared-clips service — create and retrieve public clip snapshots.
"""
import logging
import secrets

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_MAX_SLUG_ATTEMPTS = 5


def _gen_slug() -> str:
    """Return a URL-safe 8-character random slug."""
    return secrets.token_urlsafe(6)  # 6 bytes → 8 base64url chars


async def create_clip(
    session: AsyncSession,
    youtube_video_id: str,
    start_time: float,
    end_time: float,
    transcript_text: str,
    user_id: int | None,
    request: Request,
) -> tuple[str, str]:
    """Insert a shared_clips row and return (slug, share_url)."""
    row = (await session.execute(
        text("SELECT id FROM videos WHERE youtube_video_id = :yt_id"),
        {"yt_id": youtube_video_id},
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")
    video_db_id = row[0]

    base_url = str(request.base_url).rstrip("/")

    for _ in range(_MAX_SLUG_ATTEMPTS):
        slug = _gen_slug()
        try:
            await session.execute(
                text("""
                    INSERT INTO shared_clips
                        (slug, video_id, start_time, end_time,
                         transcript_text, created_by_user_id)
                    VALUES
                        (:slug, :video_id, :start, :end, :text, :user_id)
                """),
                {
                    "slug": slug,
                    "video_id": video_db_id,
                    "start": start_time,
                    "end": end_time,
                    "text": transcript_text,
                    "user_id": user_id,
                },
            )
            await session.commit()
            return slug, f"{base_url}/clips/{slug}"
        except Exception as exc:
            await session.rollback()
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                continue
            raise

    raise HTTPException(status_code=500, detail="Could not generate a unique slug — please retry")


async def get_clip(session: AsyncSession, slug: str) -> dict:
    """Fetch clip data joined with video/channel info."""
    row = (await session.execute(
        text("""
            SELECT
                sc.slug,
                v.youtube_video_id,
                v.title,
                v.thumbnail_url,
                ch.name  AS channel_name,
                sc.start_time,
                sc.end_time,
                sc.transcript_text,
                sc.view_count
            FROM shared_clips sc
            JOIN  videos   v  ON v.id  = sc.video_id
            LEFT JOIN channels ch ON ch.id = v.channel_id
            WHERE sc.slug = :slug
        """),
        {"slug": slug},
    )).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Clip not found")

    t_sec = int(float(row.start_time))
    embed_url = (
        f"https://www.youtube.com/embed/{row.youtube_video_id}"
        f"?start={t_sec}&autoplay=1"
    )
    return {
        "slug": row.slug,
        "youtube_video_id": row.youtube_video_id,
        "title": row.title,
        "channel_name": row.channel_name,
        "thumbnail_url": row.thumbnail_url,
        "embed_url": embed_url,
        "transcript_text": row.transcript_text,
        "start_time": float(row.start_time),
        "end_time": float(row.end_time),
        "view_count": row.view_count,
    }


async def increment_view_count(session: AsyncSession, slug: str) -> None:
    try:
        await session.execute(
            text("UPDATE shared_clips SET view_count = view_count + 1 WHERE slug = :slug"),
            {"slug": slug},
        )
        await session.commit()
    except Exception as exc:
        logger.warning("increment_view_count failed for slug=%s: %s", slug, exc)
