"""
Fetches transcripts for a YouTube video using a priority-ordered fallback chain:

  1. youtube-transcript-api — manual captions (highest quality)
  2. youtube-transcript-api — auto-generated captions
  3. yt-dlp subtitle download (parallel-friendly, handles edge cases)
  4. faster-whisper         — audio download + local transcription for videos
                             with no captions at all (requires faster-whisper
                             and ffmpeg; gracefully skipped if not installed)
"""
import asyncio
import json
import logging
import secrets as _secrets
import subprocess
import sys
import tempfile
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from pathlib import Path
from xml.etree.ElementTree import ParseError

from curl_cffi import requests as _cffi_requests
from http.cookiejar import MozillaCookieJar
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)
from youtube_transcript_api._errors import IpBlocked, RequestBlocked

from config import settings

logger = logging.getLogger(__name__)

# Segments returned by youtube-transcript-api: {"text", "start", "duration"}
Segments = list[dict]

# yt-dlp binary: prefer the one next to this Python executable (venv), fall back to PATH
_YT_DLP_EXE = str(Path(sys.executable).parent / ("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"))
if not Path(_YT_DLP_EXE).exists():
    _YT_DLP_EXE = "yt-dlp"

# ── Global rate limiter ───────────────────────────────────────────────────────
# One request per second, enforced across ALL Celery threads via a threading.Lock.
# Uses time.sleep (not asyncio.sleep) so it works correctly in any thread
# without touching the asyncio event loop.
#
# Both _try_youtube_api and _try_yt_dlp call _global_rate_wait() before their
# HTTP request, so every YouTube-facing call — including @retry retries and
# yt-dlp fallbacks — is throttled through this single chokepoint.
_GLOBAL_RATE_LOCK = threading.Lock()
_GLOBAL_LAST_REQUEST_AT: float = 0.0
_RATE_INTERVAL: float = 2.0   # seconds between consecutive YouTube requests (global, ~0.5 req/s)

# Set when YouTube returns 429 — causes all queued requests to abort immediately
# so only one "probe" request is made per retry cycle instead of the full batch.
# Cleared by the worker before each retry attempt.
_RATE_BANNED = threading.Event()


def _global_rate_wait() -> None:
    """Block the calling thread until 2s has elapsed since the last request.
    Raises TranscriptRateLimitError immediately if the IP is currently banned,
    so queued jobs skip the request without wasting the rate budget.

    With a rotating residential proxy (_PROXY set) every request exits a
    DIFFERENT IP, so there is no shared IP to protect: we skip the global 2s
    delay AND the stop-the-batch ban flag entirely. Concurrency is then bounded
    only by the per-thread semaphore (transcript_concurrent_limit), which is the
    intended throttle in proxy mode. A 429 on one exit IP says nothing about the
    next request, so it must not pause the whole batch."""
    global _GLOBAL_LAST_REQUEST_AT
    if _PROXY:
        return
    if _RATE_BANNED.is_set():
        raise TranscriptRateLimitError("ip-banned — skipping request")
    with _GLOBAL_RATE_LOCK:
        if _RATE_BANNED.is_set():   # re-check inside lock before sleeping
            raise TranscriptRateLimitError("ip-banned — skipping request")
        now = _time.monotonic()
        wait = _GLOBAL_LAST_REQUEST_AT + _RATE_INTERVAL - now
        if wait > 0:
            logger.debug("rate-limiter: sleeping %.2fs", wait)
            _time.sleep(wait)
        if _RATE_BANNED.is_set():   # re-check after sleep — ban may have been set while we waited
            raise TranscriptRateLimitError("ip-banned — skipping request")
        _GLOBAL_LAST_REQUEST_AT = _time.monotonic()
        logger.debug("rate-limiter: slot released at %.3f", _GLOBAL_LAST_REQUEST_AT)


def clear_rate_ban() -> None:
    """Allow the next request through after a 429 wait period.
    Called by the worker before each retry attempt."""
    _RATE_BANNED.clear()


def proxy_enabled() -> bool:
    """True when a residential proxy is configured. The worker uses this to
    skip the drain-level 30/60/90 min ban window: with rotating IPs a 429 on
    one video just retries on a fresh IP next drain — no batch-wide pause."""
    return _PROXY is not None


# Per-request timeout (seconds), configurable. With rotating IPs a slow exit
# should fail fast and retry on a fresh IP instead of holding a slot.
_REQUEST_TIMEOUT = settings.transcript_request_timeout

# Hard ceiling for the WHOLE fetch chain (youtube-api + yt-dlp) per video. A
# backstop above the per-request timeout so one stuck connection can't freeze a
# concurrency slot forever. Exceeding it = treat as a transient proxy error.
_TOTAL_FETCH_TIMEOUT = _REQUEST_TIMEOUT * 2 + 15

# Dedicated thread pool for the BLOCKING fetch calls (youtube-transcript-api is
# sync, yt-dlp is a subprocess). asyncio's default executor caps at ~32 threads,
# which would bottleneck high concurrency through the proxy. Size it to the
# transcript concurrency so every parallel fetch gets its own thread.
_FETCH_EXECUTOR = _ThreadPoolExecutor(
    max_workers=max(32, settings.transcript_concurrent_limit + 8),
    thread_name_prefix="ytfetch",
)


def _is_transient_proxy_error(exc) -> bool:
    """True for connection/proxy/timeout failures that should be RETRIED on a
    fresh IP, not treated as 'no transcript'. Covers curl transport errors:
    timeouts (28), proxy auth/tunnel failures (407 / 56), resets, DNS. Accepts
    an exception or a raw stderr string."""
    s = str(exc).lower()
    return any(k in s for k in (
        "failed to perform",    # curl_cffi's wrapper for any transport error
        "curl: (",              # any libcurl error code, e.g. (28) (56) (7) (35)
        "timed out", "timeout",
        "tunnel", "407",        # proxy CONNECT auth failure
        "proxyerror", "proxy error", "unable to connect to proxy",
        "connection reset", "couldn't connect", "could not resolve",
    ))


# ── Per-thread asyncio semaphore ──────────────────────────────────────────────
# Each Celery thread creates a fresh event loop via asyncio.run().
# asyncio.Semaphore is bound to the loop it was created on — sharing it across
# threads raises "bound to a different event loop".
# threading.local() gives every thread its own isolated semaphore.
_tl = threading.local()

def _get_sem() -> asyncio.Semaphore:
    if getattr(_tl, "sem", None) is None:
        _tl.sem = asyncio.Semaphore(settings.transcript_concurrent_limit)
    return _tl.sem


def reset_semaphore() -> None:
    """Clear this thread's asyncio semaphore so it is recreated on the current loop.
    Called at the start of each Celery drain_queue task."""
    _tl.sem = None


class TranscriptRateLimitError(Exception):
    """YouTube returned 429 — job stays pending and retries after backoff.
    retry_after: seconds from the Retry-After header, or None if not present."""
    def __init__(self, video_id: str, retry_after: int | None = None):
        super().__init__(video_id)
        self.retry_after = retry_after


def _extract_retry_after(exc: Exception) -> int | None:
    """Walk the exception chain looking for a Retry-After header value."""
    candidate: Exception | None = exc
    while candidate is not None:
        response = getattr(candidate, "response", None)
        if response is not None:
            header = getattr(response, "headers", {}).get("Retry-After")
            if header:
                try:
                    return int(header)
                except (ValueError, TypeError):
                    pass
        candidate = getattr(candidate, "__cause__", None) or getattr(candidate, "__context__", None)
    return None


class TranscriptUnavailableError(Exception):
    """YouTube says this video has no transcripts (disabled, deleted, empty XML).

    definitive=True means YouTube gave a CONCLUSIVE 'no captions' answer
    (subtitles disabled / video unavailable) — yt-dlp can't get them either, so
    we skip the slow yt-dlp fallback and go straight to 'skipped'. definitive=False
    (e.g. an empty/garbled XML parse) still tries yt-dlp, which sometimes succeeds."""
    def __init__(self, video_id: str, definitive: bool = False):
        super().__init__(video_id)
        self.definitive = definitive


# Seconds to wait before the yt-dlp fallback when youtube-transcript-api
# reports the video as unavailable (disabled, deleted, empty XML).
_FALLBACK_DELAY = 60


def _resolve_cookies() -> str | None:
    raw = settings.youtube_cookies_path
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = Path(__file__).parent.parent.parent / raw
    return str(p) if p.exists() else None


_COOKIES: str | None = _resolve_cookies()
_cookies_valid: bool = _COOKIES is not None
logger.info("Transcript cookies: %s", _COOKIES or "none (unauthenticated)")

# Residential proxy (rotating). When set, every YouTube request exits from a
# different residential IP, which is what actually defeats the per-IP/per-range
# caption throttle on this ISP. Used by both youtube-transcript-api (curl_cffi
# session) and yt-dlp (--proxy).
_PROXY: str | None = (settings.youtube_proxy_url or "").strip() or None
if _PROXY:
    # Hide credentials in logs — show only the host:port tail.
    _safe = _PROXY.rsplit("@", 1)[-1]
    _rotates = "{SID}" in _PROXY
    logger.info("YouTube proxy: enabled (%s) rotation=%s", _safe,
                "per-request" if _rotates else "STATIC — same IP every request!")
else:
    logger.info("YouTube proxy: none (direct connection)")


def _get_proxy() -> str | None:
    """Return the proxy URL for ONE request.

    Providers like NodeMaven use sticky sessions: a fixed session id (sid) keeps
    the SAME exit IP for the session's ttl. To rotate — a different residential IP
    per request, which is what spreads load and avoids per-IP blocks — put a
    `{SID}` placeholder in the session-id slot of YOUTUBE_PROXY_URL, e.g.
        ...-sid-{SID}-ttl-24h-...
    and we substitute a fresh random id on every call. Without the placeholder the
    URL is returned unchanged (static IP — fine for a provider that rotates server-side)."""
    if not _PROXY:
        return None
    if "{SID}" in _PROXY:
        return _PROXY.replace("{SID}", _secrets.token_hex(8))
    return _PROXY

# Browser to impersonate via curl_cffi. This sends a real Chrome TLS/HTTP2
# fingerprint, which is what gets requests past YouTube's bot detection — plain
# `requests` traffic is flagged and 429'd within a handful of calls regardless
# of how slowly we pace it. Used by BOTH youtube-transcript-api (http_client)
# and yt-dlp (--impersonate). Keep in sync with a target curl_cffi supports.
_IMPERSONATE_TARGET = "chrome"


class TranscriptResult:
    __slots__ = ("segments", "source", "language_code")

    def __init__(self, segments: Segments, source: str, language_code: str) -> None:
        self.segments = segments
        self.source = source
        self.language_code = language_code


async def fetch_transcript(
    video_id: str,
    preferred_lang: str = "en",
    fast: bool = False,
) -> TranscriptResult | None:
    """
    Tries each source in order, returns the first success.
    Raises TranscriptRateLimitError when YouTube returns 429.

    fast=True skips the rate-limit delay — for single-video extension queries.

    Rate limiting
    -------------
    _global_rate_wait() is a threading.Lock-based guard shared across ALL Celery
    threads.  It enforces exactly 1 YouTube request per second globally regardless
    of how many threads or coroutines are running concurrently.  It lives inside
    _try_youtube_api and _try_yt_dlp so that @retry retries and yt-dlp fallbacks
    are also throttled — not just the initial call.
    """
    sem = _get_sem()
    async with sem:
        try:
            return await asyncio.wait_for(
                _fetch_sources(video_id, preferred_lang, fast=fast),
                timeout=_TOTAL_FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            # Hard backstop: a fetch must NEVER hang its concurrency slot. If the
            # whole chain blows the budget (a stuck proxy connection that ignored
            # the per-request timeout), free the slot and retry the video on a
            # fresh IP. Without this, hung fetches accumulate and throughput dies.
            logger.warning("fetch_transcript: %s exceeded %ds budget — freeing slot, retry on fresh IP",
                           video_id, _TOTAL_FETCH_TIMEOUT)
            raise TranscriptRateLimitError(video_id) from exc


async def _fetch_sources(
    video_id: str,
    preferred_lang: str,
    fast: bool = False,
) -> TranscriptResult | None:
    loop = asyncio.get_event_loop()

    # ── Source 1 & 2: youtube-transcript-api ─────────────────────────────────
    api_rate_limited = False
    api_unavailable = False
    api_result: TranscriptResult | None = None

    try:
        api_result = await loop.run_in_executor(
            _FETCH_EXECUTOR, _try_youtube_api, video_id, preferred_lang, fast
        )
    except TranscriptUnavailableError as exc:
        if exc.definitive:
            # Conclusive "no captions" (subtitles disabled / video unavailable) —
            # yt-dlp can't get them either, so skip the slow proxy round-trip and
            # go straight to skip. This is the big throughput win on a backlog
            # full of caption-less videos.
            logger.info("youtube-api: %s has no captions (definitive) — skipping yt-dlp", video_id)
            return None
        api_unavailable = True
        _delay = 0 if (_PROXY or fast) else _FALLBACK_DELAY
        logger.info("youtube-api: unavailable for %s — trying yt-dlp %s",
                    video_id, "now" if not _delay else f"after {_delay}s")
    except TranscriptRateLimitError:
        raise  # IP is banned — yt-dlp shares the same IP so it will also fail
    except Exception as exc:
        logger.debug("youtube-api gave up on %s: %s", video_id, exc)

    # Return immediately if youtube-api found a transcript
    if api_result:
        return api_result

    # ── Delay before yt-dlp ───────────────────────────────────────────────────
    # Give YouTube time to cool down before hitting a different endpoint.
    # Skipped in fast=True mode (Chrome extension — user is waiting) AND in proxy
    # mode — with rotating IPs the yt-dlp request exits a different IP anyway, so
    # there is nothing to cool down. Without this skip, every captions-disabled
    # video wastes 60s and chokes throughput on a backlog full of them.
    if api_unavailable and not fast and not _PROXY:
        await asyncio.sleep(_FALLBACK_DELAY)

    # ── Source 3: yt-dlp ──────────────────────────────────────────────────────
    try:
        yt_result = await loop.run_in_executor(
            _FETCH_EXECUTOR, _try_yt_dlp, video_id, preferred_lang, fast
        )
        if yt_result:
            logger.info("yt-dlp: found transcript for %s", video_id)
            return yt_result
    except TranscriptRateLimitError:
        raise
    except Exception as exc:
        logger.debug("yt-dlp gave up on %s: %s", video_id, exc)

    if api_rate_limited:
        raise TranscriptRateLimitError(video_id)

    logger.debug("No transcript found for %s", video_id)
    return None


# ─── Source 1 & 2: youtube-transcript-api ────────────────────────────────────

def _build_api(use_cookies: bool) -> YouTubeTranscriptApi:
    # curl_cffi session impersonates Chrome's TLS fingerprint — this is what
    # keeps the primary path from being 429'd. youtube-transcript-api accepts
    # any requests-compatible client via http_client.
    session = _cffi_requests.Session(impersonate=_IMPERSONATE_TARGET, timeout=_REQUEST_TIMEOUT)
    proxy = _get_proxy()   # fresh rotating IP per call
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    if use_cookies and _COOKIES and _cookies_valid:
        try:
            jar = MozillaCookieJar(_COOKIES)
            jar.load(ignore_discard=True, ignore_expires=True)
            for c in jar:
                session.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
        except Exception as exc:
            logger.debug("Could not load cookies: %s", exc)
    return YouTubeTranscriptApi(http_client=session)


def _try_youtube_api(video_id: str, lang: str, fast: bool = False) -> TranscriptResult | None:
    global _cookies_valid

    # Rate-limit every attempt including retries.
    # Skipped in fast=True mode (Chrome extension single-video queries).
    if not fast:
        _global_rate_wait()

    def _list(use_cookies: bool):
        return _build_api(use_cookies).list(video_id)

    try:
        transcript_list = _list(_cookies_valid)
    except (TranscriptsDisabled, VideoUnavailable) as exc:
        logger.warning("Transcript unavailable for %s: %s", video_id, exc)
        raise TranscriptUnavailableError(video_id, definitive=True) from exc
    except (IpBlocked, RequestBlocked) as exc:
        retry_after = _extract_retry_after(exc)
        logger.warning("YouTube IP-blocked for %s — banning batch (Retry-After=%s)", video_id, retry_after)
        _RATE_BANNED.set()
        raise TranscriptRateLimitError(video_id, retry_after=retry_after) from exc
    except ParseError as exc:
        logger.debug("Empty transcript XML for %s: %s", video_id, exc)
        raise TranscriptUnavailableError(video_id) from exc
    except Exception as exc:
        exc_str = str(exc)
        if _cookies_valid and (
            "cookie" in exc_str.lower()
            or "CookieInvalid" in exc_str
            or "CookiePathInvalid" in exc_str
        ):
            _cookies_valid = False
            logger.warning(
                "Cookie file invalid or expired — switching to no-cookie mode. "
                "Export fresh cookies and restart the worker to re-enable."
            )
            try:
                transcript_list = _list(False)
            except (TranscriptsDisabled, VideoUnavailable) as exc2:
                logger.warning("Transcript unavailable for %s: %s", video_id, exc2)
                raise TranscriptUnavailableError(video_id, definitive=True) from exc2
            except (IpBlocked, RequestBlocked) as exc2:
                retry_after = _extract_retry_after(exc2)
                _RATE_BANNED.set()
                raise TranscriptRateLimitError(video_id, retry_after=retry_after) from exc2
            except ParseError as exc2:
                logger.debug("Empty transcript XML for %s: %s", video_id, exc2)
                raise TranscriptUnavailableError(video_id) from exc2
            except Exception as exc2:
                exc_str2 = str(exc2)
                if "429" in exc_str2 or "Too Many Requests" in exc_str2:
                    retry_after = _extract_retry_after(exc2)
                    _RATE_BANNED.set()
                    raise TranscriptRateLimitError(video_id, retry_after=retry_after) from exc2
                logger.warning("youtube-transcript-api error for %s: %s", video_id, exc2)
                raise exc2
        elif "429" in exc_str or "Too Many Requests" in exc_str:
            retry_after = _extract_retry_after(exc)
            _RATE_BANNED.set()
            raise TranscriptRateLimitError(video_id, retry_after=retry_after) from exc
        elif _is_transient_proxy_error(exc):
            # Slow/failed proxy exit IP — NOT a missing transcript. Keep the job
            # pending so it retries on a fresh IP instead of being skipped.
            logger.warning("Proxy/connection error for %s (%s) — retrying on a fresh IP",
                           video_id, exc_str[:80])
            raise TranscriptRateLimitError(video_id) from exc
        else:
            logger.warning("youtube-transcript-api error for %s: %s", video_id, exc)
            raise

    def _to_dicts(fetched) -> list[dict]:
        return [{"text": s.text, "start": s.start, "duration": s.duration} for s in fetched]

    def _fetch_segments(t) -> list[dict]:
        # .fetch() hits the timedtext endpoint — a SEPARATE request from .list()
        # that can be IP-blocked / 429'd on its own (list often succeeds while
        # fetch is blocked). Convert those to a rate-limit error so the job stays
        # pending and the batch stops; otherwise the block bubbles up as a generic
        # failure and the video is wrongly marked 'skipped' (silent data loss)
        # while the rest of the batch keeps hammering the blocked IP.
        try:
            return _to_dicts(t.fetch())
        except (IpBlocked, RequestBlocked) as exc:
            retry_after = _extract_retry_after(exc)
            logger.warning("YouTube IP-blocked fetching captions for %s — banning batch (Retry-After=%s)",
                           video_id, retry_after)
            _RATE_BANNED.set()
            raise TranscriptRateLimitError(video_id, retry_after=retry_after) from exc
        except Exception as exc:
            if "429" in str(exc) or "Too Many Requests" in str(exc):
                retry_after = _extract_retry_after(exc)
                _RATE_BANNED.set()
                raise TranscriptRateLimitError(video_id, retry_after=retry_after) from exc
            if _is_transient_proxy_error(exc):
                logger.warning("Proxy/connection error fetching %s (%s) — retrying on a fresh IP",
                               video_id, str(exc)[:80])
                raise TranscriptRateLimitError(video_id) from exc
            raise

    # Priority 1: manual captions in requested language
    try:
        t = transcript_list.find_manually_created_transcript([lang])
        return TranscriptResult(_fetch_segments(t), "youtube_manual", t.language_code)
    except NoTranscriptFound:
        pass

    # Priority 2: auto-generated in requested language
    try:
        t = transcript_list.find_generated_transcript([lang])
        return TranscriptResult(_fetch_segments(t), "youtube_auto", t.language_code)
    except NoTranscriptFound:
        pass

    # Priority 3: any available transcript (first one)
    for t in transcript_list:
        source = "youtube_manual" if not t.is_generated else "youtube_auto"
        return TranscriptResult(_fetch_segments(t), source, t.language_code)

    return None


# ─── Source 3: yt-dlp ────────────────────────────────────────────────────────

def _try_yt_dlp(video_id: str, lang: str, fast: bool = False) -> TranscriptResult | None:
    # Rate-limit yt-dlp too — it also hits YouTube's servers.
    # Skipped in fast=True mode.
    if not fast:
        _global_rate_wait()

    lang_spec = f"{lang}-{lang},{lang}" if "-" not in lang else lang

    with tempfile.TemporaryDirectory() as tmp:
        out_template = str(Path(tmp) / "%(id)s")
        cmd = [_YT_DLP_EXE, "--write-auto-sub", "--sub-lang", lang_spec,
               "--sub-format", "json3", "--skip-download", "--quiet",
               "--impersonate", _IMPERSONATE_TARGET,   # Chrome TLS fingerprint — defeats 429
               "-o", out_template]
        proxy = _get_proxy()
        if proxy:
            cmd += ["--proxy", proxy]   # fresh rotating residential exit, same as the API path
        if _COOKIES and _cookies_valid:
            cmd += ["--cookies", _COOKIES]
        cmd.append(f"https://www.youtube.com/watch?v={video_id}")
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, timeout=_REQUEST_TIMEOUT)
            stderr = proc.stderr.decode(errors="replace") if proc.stderr else ""
            if proc.returncode != 0:
                if "429" in stderr or "Too Many Requests" in stderr:
                    _RATE_BANNED.set()
                    raise TranscriptRateLimitError(video_id)
                if _PROXY and _is_transient_proxy_error(stderr):
                    logger.warning("yt-dlp proxy/connection error for %s — retrying on a fresh IP", video_id)
                    raise TranscriptRateLimitError(video_id)
                logger.debug("yt-dlp non-zero exit for %s: %s", video_id, stderr[:200])
                return None
        except subprocess.TimeoutExpired as exc:
            # yt-dlp itself stalled (likely a slow proxy exit) — retry, don't skip.
            if _PROXY:
                logger.warning("yt-dlp timed out for %s — retrying on a fresh IP", video_id)
                raise TranscriptRateLimitError(video_id) from exc
            logger.debug("yt-dlp failed for %s: %s", video_id, exc)
            return None
        except FileNotFoundError as exc:
            logger.debug("yt-dlp failed for %s: %s", video_id, exc)
            return None

        sub_files = sorted(Path(tmp).glob(f"{video_id}.{lang}*.json3"))
        if not sub_files:
            return None

        actual_lang = sub_files[0].stem.split(".", 1)[1]
        return _parse_yt_dlp_json3(sub_files[0], actual_lang)


def _parse_yt_dlp_json3(path: Path, lang: str) -> TranscriptResult | None:
    with path.open() as f:
        data = json.load(f)

    segments = []
    for event in data.get("events", []):
        if "segs" not in event:
            continue
        text = "".join(s.get("utf8", "") for s in event["segs"]).strip()
        if not text:
            continue
        start_ms = event.get("tStartMs", 0)
        dur_ms = event.get("dDurationMs", 2000)
        segments.append({
            "text": text,
            "start": start_ms / 1000,
            "duration": dur_ms / 1000,
        })

    if not segments:
        return None

    return TranscriptResult(segments, "youtube_auto", lang)


# ─── Source 4: faster-whisper ─────────────────────────────────────────────────

_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        logger.info("Whisper tiny model loaded (CPU/int8)")
    return _whisper_model


def _try_whisper(video_id: str) -> TranscriptResult | None:
    """Download audio with yt-dlp, transcribe with faster-whisper tiny."""
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError:
        logger.debug("faster-whisper not installed — skipping Whisper fallback")
        return None

    with tempfile.TemporaryDirectory() as tmp:
        out_template = str(Path(tmp) / f"{video_id}.%(ext)s")
        cmd = [
            _YT_DLP_EXE, "--extract-audio", "--audio-format", "mp3",
            "--audio-quality", "9", "--quiet",
            "--impersonate", _IMPERSONATE_TARGET,
            "-o", out_template,
        ]
        proxy = _get_proxy()
        if proxy:
            cmd += ["--proxy", proxy]
        if _COOKIES and _cookies_valid:
            cmd += ["--cookies", _COOKIES]
        cmd.append(f"https://www.youtube.com/watch?v={video_id}")

        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, timeout=300)
            stderr = proc.stderr.decode(errors="replace") if proc.stderr else ""
            if proc.returncode != 0:
                if "429" in stderr or "Too Many Requests" in stderr:
                    raise TranscriptRateLimitError(video_id)
                logger.debug("yt-dlp audio download failed for %s: %s", video_id, stderr[:200])
                return None
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.debug("yt-dlp audio failed for %s: %s", video_id, exc)
            return None

        audio_files = list(Path(tmp).glob(f"{video_id}.*"))
        if not audio_files:
            logger.debug("No audio file found after yt-dlp for %s", video_id)
            return None

        try:
            model = _get_whisper_model()
            seg_iter, info = model.transcribe(str(audio_files[0]), beam_size=5)
            segments = []
            for seg in seg_iter:
                text = seg.text.strip()
                if not text:
                    continue
                segments.append({
                    "text": text,
                    "start": seg.start,
                    "duration": max(0.1, seg.end - seg.start),
                })
        except Exception as exc:
            logger.warning("Whisper transcription failed for %s: %s", video_id, exc)
            return None

    if not segments:
        return None

    lang = getattr(info, "language", None) or "en"
    logger.info("Whisper transcribed %s: %d segments, lang=%s", video_id, len(segments), lang)
    return TranscriptResult(segments, "whisper", lang)
