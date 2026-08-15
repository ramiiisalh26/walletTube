"""
Recompute video.embedding from title + description + tags.

More accurate than mean-of-chunks for search ranking because title/description
explicitly state the video's topic rather than averaging all content.

Usage:
    python scripts/recompute_video_embeddings.py
    python scripts/recompute_video_embeddings.py --batch-size 100
"""
import asyncio
import argparse
import logging
import sys

sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("recompute_embeddings")


def _vec_str(vec: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vec) + "]"


def _build_text(title: str, description: str | None, tags: list | None) -> str:
    parts = [title or ""]
    if description:
        parts.append(description[:500])
    if tags:
        parts.append(" ".join(tags[:20]))
    return " ".join(p for p in parts if p).strip()


async def run(batch_size: int) -> None:
    from db.session import AsyncSessionLocal
    from services.embedding.model import EmbeddingModel
    from sqlalchemy import text

    logger.info("Loading embedding model…")
    model = EmbeddingModel.get()
    logger.info("Model ready")

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text("""
            SELECT id, title, description, tags
            FROM videos
            WHERE indexing_status = 'indexed'
              AND title != youtube_video_id
            ORDER BY id
        """))).fetchall()

    total = len(rows)
    logger.info("Videos to recompute: %d", total)
    if total == 0:
        logger.info("Nothing to do.")
        return

    updated = 0
    for offset in range(0, total, batch_size):
        batch = rows[offset: offset + batch_size]

        texts = [_build_text(r[1], r[2], r[3]) for r in batch]
        vectors = model.encode_batch(texts)

        async with AsyncSessionLocal() as session:
            for row, vec in zip(batch, vectors):
                await session.execute(
                    text("UPDATE videos SET embedding = CAST(:vec AS vector) WHERE id = :id"),
                    {"vec": _vec_str(vec), "id": row[0]},
                )
            await session.commit()
            updated += len(batch)

        logger.info("Progress %d / %d", min(offset + batch_size, total), total)

    logger.info("Done — updated %d video embeddings", updated)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()
    asyncio.run(run(args.batch_size))
