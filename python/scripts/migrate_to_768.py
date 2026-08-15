"""
Migration: upgrade all embedding columns from vector(384) to vector(768).

What this does
--------------
1. Drops all HNSW / IVFFlat indexes on vector columns (required before ALTER TYPE).
2. NULLs existing 384-dim vectors — they are incompatible with the new dimension.
3. ALTERs every vector(384) column to vector(768).
4. Recreates HNSW indexes for the new dimension.
5. Re-embeds industries and topics inline (small tables, fast).
6. Marks all indexed videos back to 'pending' so drain_queue re-embeds their chunks.

Usage
-----
    python -m scripts.migrate_to_768 --dry-run    # show what would change, no writes
    python -m scripts.migrate_to_768 --execute    # apply migration

Run once after deploying the bge-base model change.  Restart the gRPC embedding
service (or any process that holds an EmbeddingModel singleton) BEFORE running
so it loads bge-base-en-v1.5 and produces 768-dim vectors.
"""
import argparse
import asyncio
import logging

from sqlalchemy import text

from db.session import AsyncSessionLocal
from services.embedding.model import EmbeddingModel

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

_NEW_DIM = 768

# Tables / columns that need altering
_VECTOR_COLUMNS = [
    ("industries",        "embedding"),
    ("topics",            "embedding"),
    ("videos",            "embedding"),
    ("transcript_chunks", "embedding"),
    ("search_history",    "query_embedding"),
    ("popular_searches",  "query_embedding"),
]


async def main(execute: bool) -> None:
    async with AsyncSessionLocal() as session:

        # ── 1. Find all vector indexes to drop ───────────────────────────────
        index_rows = (await session.execute(text("""
            SELECT indexname, tablename
            FROM pg_indexes
            WHERE (indexdef ILIKE '%hnsw%' OR indexdef ILIKE '%ivfflat%')
              AND tablename = ANY(ARRAY[
                  'industries','topics','videos',
                  'transcript_chunks','search_history','popular_searches'
              ])
        """))).fetchall()

        logger.info("Vector indexes found: %d", len(index_rows))
        for row in index_rows:
            logger.info("  %s.%s", row.tablename, row.indexname)

        # ── 2. Count rows affected ────────────────────────────────────────────
        for table, col in _VECTOR_COLUMNS:
            count = (await session.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL")
            )).scalar()
            logger.info("%-25s %-20s  %6d rows with existing embedding", table, col, count)

        if not execute:
            logger.info("")
            logger.info("Dry-run — no changes made.  Pass --execute to apply.")
            return

        # ── 3. Find and save views that depend on embedding columns ──────────
        # PostgreSQL blocks ALTER TYPE on columns referenced by views.
        # Column-level dependency tracking misses views that use SELECT *
        # (the dependency is recorded at table level, not column level).
        # Safest fix: save and drop ALL user views, recreate after ALTER TYPE.
        view_rows = (await session.execute(text("""
            SELECT viewname, definition
            FROM pg_views
            WHERE schemaname = 'public'
            ORDER BY viewname
        """))).fetchall()

        logger.info("Dependent views found: %d", len(view_rows))
        for v in view_rows:
            logger.info("  %s", v.viewname)

        for v in view_rows:
            logger.info("Dropping view %s ...", v.viewname)
            await session.execute(text(f"DROP VIEW IF EXISTS {v.viewname} CASCADE"))

        # ── 4. Drop vector indexes ────────────────────────────────────────────
        for row in index_rows:
            logger.info("Dropping index %s ...", row.indexname)
            await session.execute(text(f"DROP INDEX IF EXISTS {row.indexname}"))

        # ── 5. NULL + ALTER TYPE for each column ─────────────────────────────
        # transcript_chunks.embedding is NOT NULL in the schema — drop the
        # constraint first so we can clear the old 384-dim vectors.
        # drain_queue will refill them with 768-dim vectors during re-indexing.
        logger.info("Dropping NOT NULL constraint on transcript_chunks.embedding ...")
        await session.execute(text(
            "ALTER TABLE transcript_chunks ALTER COLUMN embedding DROP NOT NULL"
        ))

        for table, col in _VECTOR_COLUMNS:
            logger.info("Migrating %s.%s → vector(%d) ...", table, col, _NEW_DIM)
            await session.execute(text(f"UPDATE {table} SET {col} = NULL"))
            await session.execute(
                text(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE vector({_NEW_DIM})")
            )

        # ── 5b. Recreate HNSW indexes ────────────────────────────────────────
        hnsw_defs = [
            ("idx_transcript_chunks_embedding_hnsw",
             "transcript_chunks", "embedding"),
            ("idx_videos_embedding_hnsw",
             "videos", "embedding"),
            ("idx_topics_embedding_hnsw",
             "topics", "embedding"),
            ("idx_industries_embedding_hnsw",
             "industries", "embedding"),
        ]
        for idx_name, table, col in hnsw_defs:
            logger.info("Creating HNSW index %s ...", idx_name)
            await session.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} "
                f"ON {table} USING hnsw ({col} vector_cosine_ops) "
                f"WITH (m = 16, ef_construction = 64)"
            ))

        # ── 6. Recreate dropped views ─────────────────────────────────────────
        for v in view_rows:
            logger.info("Recreating view %s ...", v.viewname)
            await session.execute(text(f"CREATE OR REPLACE VIEW {v.viewname} AS {v.definition}"))

        # ── 7. Re-embed industries ────────────────────────────────────────────
        model = EmbeddingModel.get()
        industry_rows = (await session.execute(text(
            "SELECT id, name, description FROM industries"
        ))).fetchall()
        logger.info("Re-embedding %d industries ...", len(industry_rows))
        for row in industry_rows:
            text_to_embed = row.name
            if row.description:
                text_to_embed += " " + row.description
            vec = model.encode_one(text_to_embed)
            await session.execute(
                text("UPDATE industries SET embedding = CAST(:v AS vector) WHERE id = :id"),
                {"v": "[" + ",".join(str(x) for x in vec) + "]", "id": row.id},
            )
        logger.info("Industries re-embedded.")

        # ── 7. Re-embed topics ────────────────────────────────────────────────
        topic_rows = (await session.execute(text(
            "SELECT id, name, keywords FROM topics"
        ))).fetchall()
        logger.info("Re-embedding %d topics ...", len(topic_rows))

        # Batch encode for speed
        topic_ids   = [r.id for r in topic_rows]
        topic_texts = [
            r.name + (" " + " ".join(r.keywords) if r.keywords else "")
            for r in topic_rows
        ]
        topic_vecs = model.encode_batch(topic_texts)
        for tid, vec in zip(topic_ids, topic_vecs):
            await session.execute(
                text("UPDATE topics SET embedding = CAST(:v AS vector) WHERE id = :id"),
                {"v": "[" + ",".join(str(x) for x in vec) + "]", "id": tid},
            )
        logger.info("Topics re-embedded.")

        # ── 8. Re-queue all videos for chunk re-indexing ─────────────────────
        result = await session.execute(text("""
            UPDATE videos
            SET indexing_status = 'pending', embedding = NULL
            WHERE indexing_status = 'indexed'
        """))
        logger.info("Videos reset to pending: %d", result.rowcount)

        await session.commit()
        logger.info("")
        logger.info("Migration complete.")
        logger.info("  - All vector columns are now vector(%d).", _NEW_DIM)
        logger.info("  - Industries and topics are re-embedded.")
        logger.info("  - %d videos queued for re-indexing via drain_queue.", result.rowcount)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate embeddings from 384-dim to 768-dim")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="execute", action="store_false", default=False)
    mode.add_argument("--execute", dest="execute", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(execute=args.execute))
