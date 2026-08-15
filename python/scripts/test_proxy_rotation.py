r"""Confirm the residential proxy rotates IPs (a different IP per request).

Reads YOUTUBE_PROXY_URL from your .env and makes a few requests through the SAME
path the fetcher uses (curl_cffi + Chrome impersonation + per-request rotating
sid). Each line should show a DIFFERENT exit IP if rotation works.

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\test_proxy_rotation.py
"""
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from curl_cffi import requests as cffi

from services.transcript.fetcher import _IMPERSONATE_TARGET, _get_proxy, proxy_enabled

if not proxy_enabled():
    print("YOUTUBE_PROXY_URL is not set in .env — nothing to test.")
    sys.exit(1)

print("Making 5 requests through the proxy — exit IPs should DIFFER:\n")
seen: set[str] = set()
for i in range(5):
    proxy = _get_proxy()
    try:
        ip = cffi.get(
            "https://api.ipify.org",
            impersonate=_IMPERSONATE_TARGET,
            proxies={"http": proxy, "https": proxy},
            timeout=30,
        ).text.strip()
        seen.add(ip)
        print(f"  request {i + 1}: exit IP = {ip}")
    except Exception as e:
        print(f"  request {i + 1}: ERROR {type(e).__name__}: {e}")

print(f"\n{len(seen)} distinct IP(s) across 5 requests — "
      + ("rotation WORKING" if len(seen) > 1 else "NOT rotating (check the {SID} placeholder)"))
