"""Flush cached search responses (Redis cache:search:*).

Needed after changing search logic — old cached results (e.g. the pre-fix
'0 results') are served until their 6h TTL expires otherwise.

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_clear_search_cache.py
"""
import asyncio

import redis.asyncio as aioredis

from config import settings


async def main() -> None:
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    keys = [k async for k in r.scan_iter("cache:search:*")]
    if keys:
        await r.delete(*keys)
    print(f"cleared {len(keys)} cached search entries")
    try:
        await r.aclose()
    except Exception:
        pass


asyncio.run(main())
