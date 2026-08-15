"""
Re-embed ALL transcript_chunks with bge-base-en-v1.5 (768-dim).

Works on existing chunks that already have embeddings (overwrites them)
AND on chunks that are already NULL.  Safe to run whether or not you ran
migrate_to_768 first.

What it does
------------
Step 0 — Reset:
  NULLs every existing chunk embedding and video embedding so the script
  starts from a clean slate and nothing is left with a stale 384-dim vector.

Phase 1 — Chunks:
  Reads chunks in batches where embedding IS NULL, encodes with the local
  EmbeddingModel (bge-base-en-v1.5, 768-dim), and writes vectors back.

Phase 2 — Videos:
  For every video whose chunks are now fully embedded, computes the mean
  chunk vector, normalises it, writes it to videos.embedding, and sets
  indexing_status = 'indexed'.

Resumable: Ctrl-C then re-run with --skip-reset to continue from where
you left off (chunks that already have a 768-dim embedding are skipped).

Usage
-----
    python -m scripts.reembed_chunks                     # full run (reset + embed all)
    python -m scripts.reembed_chunks --batch-size 512    # larger batches (faster on GPU)
    python -m scripts.reembed_chunks --skip-reset        # resume after interruption
    python -m scripts.reembed_chunks --phase2-only       # only recompute video embeddings
"""
import argparse
import asyncio
import logging
import time

import numpy as np
from sqlalchemy import text

from db.session import AsyncSessionLocal
from services.embedding.model import EmbeddingModel

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


def _vec_str(vec: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vec) + "]"


def _parse_vec(emb) -> list[float]:
    """Convert pgvector result to a plain float list regardless of return type."""
    if isinstance(emb, (list, tuple)):
        return [float(x) for x in emb]
    # asyncpg may return vector as string "[0.1,0.2,...]"
    return [float(x) for x in str(emb).strip("[]").split(",")]


# ── Step 0: reset existing embeddings ────────────────────────────────────────

async def reset_embeddings() -> None:
    """NULL out all existing chunk and video embeddings before re-embedding."""
    async with AsyncSessionLocal() as session:
        chunk_result = await session.execute(
            text("UPDATE transcript_chunks SET embedding = NULL WHERE embedding IS NOT NULL")
        )
        video_result = await session.execute(
            text("UPDATE videos SET embedding = NULL, indexing_status = 'pending' WHERE embedding IS NOT NULL")
        )
        await session.commit()
    logger.info(
        "Reset: %d chunk embeddings and %d video embeddings cleared.",
        chunk_result.rowcount, video_result.rowcount,
    )


# ── Phase 1: re-embed chunks ──────────────────────────────────────────────────

async def reembed_chunks(batch_size: int) -> None:
    model = EmbeddingModel.get()

    async with AsyncSessionLocal() as session:
        total_null = (await session.execute(
            text("SELECT COUNT(*) FROM transcript_chunks WHERE embedding IS NULL")
        )).scalar()
    logger.info("Chunks to embed: %d", total_null)

    if total_null == 0:
        logger.info("All chunks already embedded — skipping Phase 1.")
        return

    done = 0
    t_start = time.monotonic()

    while True:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(text("""
                SELECT id, text
                FROM transcript_chunks
                WHERE embedding IS NULL
                ORDER BY video_id, chunk_index
                LIMIT :lim
            """), {"lim": batch_size})).fetchall()

        if not rows:
            break

        ids   = [r.id   for r in rows]
        texts = [r.text for r in rows]

        vectors = await asyncio.to_thread(model.encode_batch, texts)

        async with AsyncSessionLocal() as session:
            for chunk_id, vec in zip(ids, vectors):
                await session.execute(
                    text("UPDATE transcript_chunks SET embedding = CAST(:v AS vector) WHERE id = :id"),
                    {"v": _vec_str(vec), "id": chunk_id},
                )
            await session.commit()

        done += len(rows)
        elapsed = time.monotonic() - t_start
        rate = done / elapsed if elapsed > 0 else 0
        eta_s = (total_null - done) / rate if rate > 0 else 0
        logger.info(
            "Phase 1: %d / %d  (%.1f chunks/s  ETA %.0fm)",
            done, total_null, rate, eta_s / 60,
        )

    logger.info("Phase 1 complete — %d chunks embedded.", done)


# ── Phase 2: recompute video-level embeddings ─────────────────────────────────

async def reembed_videos() -> None:
    async with AsyncSessionLocal() as session:
        video_rows = (await session.execute(text("""
            SELECT DISTINCT v.id
            FROM videos v
            WHERE v.indexing_status = 'pending'
              AND NOT EXISTS (
                  SELECT 1 FROM transcript_chunks tc
                  WHERE tc.video_id = v.id AND tc.embedding IS NULL
              )
              AND EXISTS (
                  SELECT 1 FROM transcript_chunks tc
                  WHERE tc.video_id = v.id
              )
        """))).fetchall()

    video_ids = [r.id for r in video_rows]
    logger.info("Phase 2: %d videos ready for video-level embedding", len(video_ids))

    done = 0
    for vid_id in video_ids:
        async with AsyncSessionLocal() as session:
            chunk_rows = (await session.execute(text("""
                SELECT embedding FROM transcript_chunks
                WHERE video_id = :vid AND embedding IS NOT NULL
            """), {"vid": vid_id})).fetchall()

            if not chunk_rows:
                continue

            parsed = [_parse_vec(r.embedding) for r in chunk_rows]
            # Only include 768-dim vectors — skip any leftover wrong-dim embeddings
            parsed = [v for v in parsed if len(v) == 768]
            if not parsed:
                continue

            vecs = np.array(parsed, dtype=np.float32)
            mean = vecs.mean(axis=0)
            norm = np.linalg.norm(mean)
            if norm > 0:
                mean = mean / norm

            await session.execute(
                text("""
                    UPDATE videos
                    SET embedding = CAST(:v AS vector),
                        indexing_status = 'indexed'
                    WHERE id = :id
                """),
                {"v": _vec_str(mean.tolist()), "id": vid_id},
            )
            await session.commit()

        done += 1
        if done % 100 == 0:
            logger.info("Phase 2: %d / %d videos updated", done, len(video_ids))

    logger.info("Phase 2 complete — %d video embeddings updated.", done)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main(batch_size: int, skip_reset: bool, phase2_only: bool) -> None:
    if not phase2_only:
        if not skip_reset:
            await reset_embeddings()
        await reembed_chunks(batch_size)
    await reembed_videos()
    logger.info("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-embed all chunks with 768-dim model")
    parser.add_argument("--batch-size", type=int, default=256,
                        help="Chunks per encode batch (default 256)")
    parser.add_argument("--skip-reset", action="store_true",
                        help="Skip the NULL-out step — use to resume after interruption")
    parser.add_argument("--phase2-only", action="store_true",
                        help="Skip chunk re-embedding, only recompute video embeddings")
    args = parser.parse_args()
    asyncio.run(main(args.batch_size, args.skip_reset, args.phase2_only))
