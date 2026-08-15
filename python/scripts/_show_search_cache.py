"""Show what's currently in the search cache (Redis cache:search:*).

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_show_search_cache.py
"""
import asyncio
import json

import redis.asyncio as aioredis

from config import settings


async def main() -> None:
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    keys = [k async for k in r.scan_iter("cache:search:*")]
    print(f"{len(keys)} cached search entries:\n")
    for k in keys:
        ttl = await r.ttl(k)
        try:
            d = json.loads(await r.get(k))
            print(f"  {k}")
            print(f"     query={d.get('query')!r}  results={d.get('total_results')}  "
                  f"refined={d.get('refined')}  expires in {ttl}s ({ttl // 60}m)")
        except Exception as e:
            print(f"  {k}: {e}")
    try:
        await r.aclose()
    except Exception:
        pass


asyncio.run(main())
