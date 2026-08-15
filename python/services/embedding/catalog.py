"""
Embed and persist catalog items (industries, topics) using the local model.

Call embed_industry() / embed_topic() whenever a new row is created so its
embedding is set immediately. embed_all_*() are used for the initial backfill.
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from services.embedding.model import EmbeddingModel

logger = logging.getLogger(__name__)


def _vec_str(vec: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vec) + "]"


async def embed_industry(session: AsyncSession, industry_id: int) -> None:
    row = (await session.execute(
        text("SELECT name, description FROM industries WHERE id = :id"),
        {"id": industry_id},
    )).fetchone()
    if not row:
        return
    text_to_embed = f"{row.name}. {row.description or ''}"
    vec = EmbeddingModel.get().encode_one(text_to_embed)
    await session.execute(
        text("UPDATE industries SET embedding = CAST(:vec AS vector) WHERE id = :id"),
        {"vec": _vec_str(vec), "id": industry_id},
    )
    logger.info("Embedded industry id=%d (%s)", industry_id, row.name)


async def embed_topic(session: AsyncSession, topic_id: int) -> None:
    row = (await session.execute(
        text("SELECT name, keywords FROM topics WHERE id = :id"),
        {"id": topic_id},
    )).fetchone()
    if not row:
        return
    keywords_str = " ".join(row.keywords) if row.keywords else ""
    text_to_embed = f"{row.name}. {keywords_str}"
    vec = EmbeddingModel.get().encode_one(text_to_embed)
    await session.execute(
        text("UPDATE topics SET embedding = CAST(:vec AS vector) WHERE id = :id"),
        {"vec": _vec_str(vec), "id": topic_id},
    )
    logger.info("Embedded topic id=%d (%s)", topic_id, row.name)


async def embed_all_industries(session: AsyncSession) -> int:
    rows = (await session.execute(
        text("SELECT id FROM industries ORDER BY id")
    )).fetchall()
    model = EmbeddingModel.get()
    for (ind_id,) in rows:
        await embed_industry(session, ind_id)
    await session.commit()
    return len(rows)


async def embed_all_topics(session: AsyncSession) -> int:
    rows = (await session.execute(
        text("SELECT id FROM topics ORDER BY id")
    )).fetchall()
    for (topic_id,) in rows:
        await embed_topic(session, topic_id)
    await session.commit()
    return len(rows)
