r"""Probe both transcript paths for one video to see who fails and why.

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_test_ytdlp.py TDUrGO9O_ro
"""
import subprocess
import sys

from services.transcript.fetcher import (
    _IMPERSONATE_TARGET,
    _get_proxy,
    _try_youtube_api,
    _try_yt_dlp,
    TranscriptRateLimitError,
    TranscriptUnavailableError,
    proxy_enabled,
    _YT_DLP_EXE,
)

vid = sys.argv[1] if len(sys.argv) > 1 else "TDUrGO9O_ro"
print(f"testing {vid}  (proxy={proxy_enabled()})\n")

print("[1] youtube-transcript-api:")
try:
    r = _try_youtube_api(vid, "en")
    print("   ->", f"{len(r.segments)} segments" if r else "None")
except TranscriptUnavailableError:
    print("   -> UNAVAILABLE (no captions / subtitles disabled)")
except TranscriptRateLimitError:
    print("   -> rate-limited / proxy error")
except Exception as e:
    print("   -> error:", type(e).__name__, str(e)[:120])

print("\n[2] yt-dlp (the code path):")
try:
    r = _try_yt_dlp(vid, "en")
    print("   ->", f"{len(r.segments)} segments" if r else "None (no subs found)")
except TranscriptRateLimitError:
    print("   -> rate-limited / proxy error")
except Exception as e:
    print("   -> error:", type(e).__name__, str(e)[:120])

print("\n[3] yt-dlp --list-subs (ground truth: does the video have ANY subs?):")
proxy = _get_proxy()
cmd = [_YT_DLP_EXE, "--list-subs", "--skip-download", "--impersonate", _IMPERSONATE_TARGET]
if proxy:
    cmd += ["--proxy", proxy]
cmd.append(f"https://www.youtube.com/watch?v={vid}")
out = subprocess.run(cmd, capture_output=True, timeout=90)
text = (out.stdout.decode(errors="replace") + out.stderr.decode(errors="replace"))
for line in text.splitlines():
    low = line.lower()
    if any(k in low for k in ("available", "language", "en ", "no subtitles", "has no", "error", "disabled")):
        print("   ", line.strip()[:100])
