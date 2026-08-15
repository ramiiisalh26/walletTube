"""
Bulk channel discovery — finds new channels from two sources then queues
all their videos into indexing_jobs exactly like the admin API endpoint.

Sources
-------
1. yt-dlp search  — searches YouTube without using any API quota.
   Extracts channel IDs from video search results across many queries.

2. GitHub curated list — fetches ErikCH/DevYouTubeList (500+ dev channels)
   and JoseDeFreitas/awesome-youtubers from GitHub, parses YouTube handles/IDs.

Usage
-----
    python -m scripts.discover_channels_bulk                  # both sources
    python -m scripts.discover_channels_bulk --source ytdlp   # yt-dlp only
    python -m scripts.discover_channels_bulk --source github   # GitHub only
    python -m scripts.discover_channels_bulk --dry-run         # preview, no DB writes
    python -m scripts.discover_channels_bulk --limit 20        # max channels to add
"""
import argparse
import asyncio
import logging
import re
import subprocess
import sys
import time

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# ── yt-dlp search queries ─────────────────────────────────────────────────────
# Each query returns up to 50 videos — unique channel IDs are extracted.
# Add more queries to discover more channels.
_YTDLP_QUERIES = [
    "python programming tutorial",
    "javascript web development tutorial",
    "machine learning deep learning tutorial",
    "react nextjs tutorial",
    "docker kubernetes devops tutorial",
    "system design interview",
    "java spring boot tutorial",
    "data structures algorithms tutorial",
    "rust programming tutorial",
    "golang tutorial",
    "aws cloud tutorial",
    "linux command line tutorial",
    "git github tutorial",
    "sql database tutorial",
    "computer science lecture",
    "software engineering interview",
    "typescript tutorial",
    "flutter mobile development",
    "cybersecurity ethical hacking tutorial",
    "open source contributing tutorial",
]

# GitHub repos that contain curated lists of YouTube channels
_GITHUB_SOURCES = [
    "https://raw.githubusercontent.com/ErikCH/DevYouTubeList/master/README.md",
    "https://raw.githubusercontent.com/JoseDeFreitas/awesome-youtubers/main/README.md",
]

# yt-dlp binary
from services.transcript.fetcher import _YT_DLP_EXE


# ── Source 1: yt-dlp search ───────────────────────────────────────────────────

def _discover_via_ytdlp(queries: list[str], results_per_query: int = 50) -> set[str]:
    """
    Run yt-dlp searches and extract unique channel IDs.
    No YouTube API quota used — yt-dlp scrapes search results directly.
    """
    channel_ids: set[str] = set()

    for i, query in enumerate(queries, 1):
        logger.info("[yt-dlp %d/%d] searching: %r", i, len(queries), query)
        cmd = [
            _YT_DLP_EXE,
            f"ytsearch{results_per_query}:{query}",
            "--flat-playlist",
            "--print", "%(channel_id)s",
            "--no-download",
            "--quiet",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            new_ids = {
                line.strip() for line in proc.stdout.splitlines()
                if line.strip() and line.strip().startswith("UC")
            }
            logger.info("  → %d channel IDs (%d new)", len(new_ids),
                        len(new_ids - channel_ids))
            channel_ids |= new_ids
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("  yt-dlp failed for %r: %s", query, exc)

        # Small delay between queries to avoid hammering YouTube
        if i < len(queries):
            time.sleep(1)

    logger.info("yt-dlp total unique channel IDs: %d", len(channel_ids))
    return channel_ids


# ── Source 2: GitHub curated lists ───────────────────────────────────────────

def _extract_yt_ids_from_markdown(text: str) -> set[str]:
    """
    Parse YouTube channel URLs from markdown text.
    Handles all known formats:
      https://www.youtube.com/@handle
      https://www.youtube.com/c/channelname
      https://www.youtube.com/channel/UCxxxxxxxx
      https://www.youtube.com/user/username
      https://www.youtube.com/channelname   (bare slug, older format)
    """
    # YouTube paths that are NOT channel identifiers
    _NOT_CHANNELS = {
        "watch", "playlist", "results", "feed", "trending", "shorts",
        "live", "explore", "gaming", "movies", "news", "sports",
        "learning", "fashion", "beauty", "about", "howyoutubeworks",
        "account", "premium", "music", "kids", "studio",
    }

    ids: set[str] = set()

    # Direct UC channel IDs  e.g. /channel/UCxxxxxxxx
    for m in re.finditer(r"youtube\.com/channel/(UC[\w-]{20,})", text):
        ids.add(m.group(1))

    # @handles  e.g. /@coreyms
    for m in re.finditer(r"youtube\.com/(@[\w.-]+)", text):
        ids.add(m.group(1))

    # /c/ and /user/ slugs  e.g. /c/TraversyMedia
    for m in re.finditer(r"youtube\.com/(?:c|user)/([\w.-]+)", text):
        slug = m.group(1).rstrip("/")
        if slug.lower() not in _NOT_CHANNELS:
            ids.add(f"@{slug}")

    # Bare slugs  e.g. youtube.com/TechWorldwithNana  (no /c/, /user/, /@ prefix)
    for m in re.finditer(r"youtube\.com/([\w.-]+)(?:[/\)\]\"'\s]|$)", text):
        slug = m.group(1).rstrip("/")
        if (slug.lower() not in _NOT_CHANNELS
                and not slug.startswith("UC")
                and len(slug) >= 3):
            ids.add(f"@{slug}")

    return ids


def _discover_via_github() -> set[str]:
    """Fetch curated channel lists from GitHub and extract YouTube channel IDs/handles."""
    all_ids: set[str] = set()

    for url in _GITHUB_SOURCES:
        logger.info("[GitHub] fetching %s", url)
        try:
            resp = httpx.get(url, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            ids = _extract_yt_ids_from_markdown(resp.text)
            logger.info("  → %d channel IDs/handles extracted", len(ids))
            all_ids |= ids
        except Exception as exc:
            logger.warning("  failed to fetch %s: %s", url, exc)

    logger.info("GitHub total unique IDs/handles: %d", len(all_ids))
    return all_ids


# ── DB: filter out channels already in DB ─────────────────────────────────────

async def _filter_known(channel_ids: set[str]) -> set[str]:
    """Remove channel IDs already in the channels table."""
    from sqlalchemy import text
    from db.session import AsyncSessionLocal

    uc_ids = {c for c in channel_ids if c.startswith("UC")}
    handles = channel_ids - uc_ids

    known: set[str] = set()
    async with AsyncSessionLocal() as session:
        if uc_ids:
            rows = (await session.execute(
                text("SELECT youtube_channel_id FROM channels "
                     "WHERE youtube_channel_id = ANY(:ids)"),
                {"ids": list(uc_ids)},
            )).fetchall()
            known |= {r[0] for r in rows}

    new_ids = channel_ids - known
    logger.info("After filtering known: %d new (of %d total)",
                len(new_ids), len(channel_ids))
    return new_ids


# ── Add channels (same logic as /api/ingestion/channel endpoint) ──────────────

async def _add_channels(channel_ids: set[str], dry_run: bool, limit: int) -> dict:
    from api.services.ingestion_service import crawl_channel
    from db.session import AsyncSessionLocal

    ids_list = sorted(channel_ids)[:limit]
    logger.info("Adding %d channels (dry_run=%s)", len(ids_list), dry_run)

    added = 0
    queued = 0
    failed = 0

    for i, cid in enumerate(ids_list, 1):
        logger.info("[%d/%d] %s", i, len(ids_list), cid)
        if dry_run:
            logger.info("  [DRY RUN] would call crawl_channel(%r)", cid)
            continue
        try:
            async with AsyncSessionLocal() as session:
                result = await crawl_channel(session, cid)
            if "error" in result:
                logger.warning("  SKIP: %s", result["error"])
                failed += 1
            else:
                logger.info("  %s — fetched=%d queued=%d",
                            result["channel_name"],
                            result["total_fetched"],
                            result["queued"])
                added += 1
                queued += result["queued"]
        except Exception as exc:
            logger.warning("  ERROR: %s", exc)
            failed += 1

        # Gentle delay between YouTube API calls
        if i < len(ids_list):
            time.sleep(1.5)

    return {"added": added, "queued": queued, "failed": failed}


# ── Entry point ───────────────────────────────────────────────────────────────

async def main(source: str, dry_run: bool, limit: int) -> None:
    channel_ids: set[str] = set()

    if source in ("ytdlp", "both"):
        channel_ids |= _discover_via_ytdlp(_YTDLP_QUERIES)

    if source in ("github", "both"):
        channel_ids |= _discover_via_github()

    if not channel_ids:
        logger.info("No channel IDs discovered — nothing to do.")
        return

    logger.info("Total discovered: %d unique channel IDs/handles", len(channel_ids))

    new_ids = await _filter_known(channel_ids)
    if not new_ids:
        logger.info("All discovered channels are already in the database.")
        return

    result = await _add_channels(new_ids, dry_run, limit)

    print()
    print("=" * 50)
    print(f"  Channels added   : {result['added']}")
    print(f"  Videos queued    : {result['queued']}")
    print(f"  Failed/skipped   : {result['failed']}")
    print("=" * 50)
    if not dry_run:
        print("  Start the worker to index the queued videos:")
        print("  python run_indexing.py --watch --batch 20 --interval 60")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk channel discovery and import")
    parser.add_argument("--source", choices=["ytdlp", "github", "both"],
                        default="both", help="Discovery source (default: both)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only — no DB writes")
    parser.add_argument("--limit", type=int, default=9999,
                        help="Max new channels to add per run (default: unlimited)")
    args = parser.parse_args()
    asyncio.run(main(args.source, args.dry_run, args.limit))
