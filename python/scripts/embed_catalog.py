"""
One-shot backfill: generate and store embeddings for all industries and topics.

Usage:
    python scripts/embed_catalog.py
    python scripts/embed_catalog.py --industries-only
    python scripts/embed_catalog.py --topics-only
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
logger = logging.getLogger("embed_catalog")


async def main(industries: bool, topics: bool) -> None:
    from db.session import AsyncSessionLocal
    from services.embedding.catalog import embed_all_industries, embed_all_topics

    logger.info("Loading embedding model…")
    from services.embedding.model import EmbeddingModel
    EmbeddingModel.get()
    logger.info("Model ready")

    async with AsyncSessionLocal() as session:
        if industries:
            n = await embed_all_industries(session)
            logger.info("Embedded %d industries", n)
        if topics:
            n = await embed_all_topics(session)
            logger.info("Embedded %d topics", n)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--industries-only", action="store_true")
    parser.add_argument("--topics-only",     action="store_true")
    args = parser.parse_args()

    run_industries = not args.topics_only
    run_topics     = not args.industries_only

    asyncio.run(main(run_industries, run_topics))
