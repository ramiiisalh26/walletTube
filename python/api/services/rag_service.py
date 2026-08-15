"""RAG — synthesise an answer from the chunks Stage 5 already reranked.

This runs *after* retrieval. It never selects or reorders results; it reads the
top-N reranked chunks and writes a grounded answer that cites them by number.
Every failure path is soft: the caller still returns its results, just with
`answer=None`.
"""

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone

import anthropic
import openai

from api.schemas.search import Citation, SearchResult
from config import settings

logger = logging.getLogger(__name__)

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"

_anthropic_client: anthropic.AsyncAnthropic | None = None
_openai_client: openai.AsyncOpenAI | None = None


class _RagError(Exception):
    """Any provider failure worth soft-failing on."""

# Reasons surfaced on SearchResponse.answer_skipped_reason.
SKIP_DISABLED = "disabled"
SKIP_LOW_CONFIDENCE = "low_confidence"
SKIP_LIMIT = "limit_reached"
SKIP_ERROR = "error"

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")

_SYSTEM = """You answer questions using ONLY the numbered YouTube transcript excerpts provided in the user message.

Rules:
- Ground every claim in the excerpts. Never add facts, numbers, names, or opinions that are not present in them.
- Cite the excerpt you drew each claim from with its bracketed number, e.g. [1] or [2][4]. Every substantive sentence needs at least one citation.
- Only cite numbers that actually appear in the excerpts you were given. Never invent a citation number.
- When the excerpts disagree, say so and attribute each position to its source rather than picking a winner.
- When the excerpts do not answer the question, say plainly that they don't and describe what they do cover. Do not fall back on your own knowledge.
- These are spoken transcripts: they contain filler, false starts, and transcription errors. Read through them for meaning, but do not smooth over a claim into something the speaker did not say.

Write for someone who has not read the excerpts. Lead with the answer in the first sentence, then the supporting detail. Prose, not bullet-point fragments. Keep it under 200 words unless the question genuinely needs more."""


def _provider() -> str:
    return (settings.rag_provider or PROVIDER_ANTHROPIC).strip().lower()


def _anthropic() -> anthropic.AsyncAnthropic:
    """Lazily build the shared async client so import never needs a key."""
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


def _openai() -> openai.AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = openai.AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.rag_openai_base_url,
        )
    return _openai_client


def active_model() -> str:
    return (
        settings.rag_openai_model
        if _provider() == PROVIDER_OPENAI
        else settings.rag_model
    )


def is_available() -> bool:
    if not settings.rag_enabled:
        return False
    if _provider() == PROVIDER_OPENAI:
        return bool(settings.openai_api_key)
    return bool(settings.anthropic_api_key)


# ── Per-session daily cap ─────────────────────────────────────────────────────
# Mirrors the free-search gate in search_service: atomic check-and-increment so
# a burst of concurrent requests can't overshoot the limit.
_RAG_LIMIT_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current >= tonumber(ARGV[1]) then
    return -1
end
local newVal = redis.call('INCR', KEYS[1])
if newVal == 1 then
    redis.call('EXPIREAT', KEYS[1], tonumber(ARGV[2]))
end
return newVal
"""


def _rag_limit_key(session_id: str) -> str:
    return f"raglimit:{session_id}:{date.today().isoformat()}"


def _next_midnight_epoch() -> int:
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    return int(datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc).timestamp())


async def consume_credit(redis, session_id: str | None) -> bool:
    """Claim one generation for this session. True when allowed."""
    if settings.rag_daily_limit <= 0 or not session_id:
        return True
    try:
        script = redis.register_script(_RAG_LIMIT_LUA)
        result = await script(
            keys=[_rag_limit_key(session_id)],
            args=[str(settings.rag_daily_limit), str(_next_midnight_epoch())],
        )
        return int(result) >= 0
    except Exception as exc:
        # Redis trouble shouldn't block answers — fail open, the Anthropic-side
        # rate limit is the real backstop.
        logger.warning("rag credit check failed, allowing: %s", exc)
        return True


# ── Prompt assembly ───────────────────────────────────────────────────────────

def select_context(results: list[SearchResult]) -> list[SearchResult]:
    """Take the strongest reranked chunks, best-first."""
    return results[: settings.rag_max_context_chunks]


def _build_prompt(query: str, context: list[SearchResult]) -> str:
    parts = []
    for i, r in enumerate(context, start=1):
        # The parent span carries ~2 minutes of surrounding talk (small-to-big
        # retrieval), which reads far better than the bare hit. Fall back to the
        # chunk itself for legacy rows that have no parent.
        body = (r.context.text if r.context else None) or r.text
        channel = r.channel_name or "unknown channel"
        parts.append(
            f"[{i}] {r.title} — {channel} (at {int(r.start_time)}s)\n{body.strip()}"
        )
    excerpts = "\n\n".join(parts)
    return (
        f"<excerpts>\n{excerpts}\n</excerpts>\n\n"
        f"Question: {query}\n\n"
        f"Answer using only the excerpts above, citing them by number."
    )


def _resolve_citations(answer: str, context: list[SearchResult]) -> tuple[str, list[Citation]]:
    """Keep only citations that point at a real excerpt; drop the rest.

    A model that invents `[9]` when it was given six excerpts would otherwise
    render as a dead link, so out-of-range markers are stripped from the text.
    """
    valid = range(1, len(context) + 1)
    seen: dict[int, Citation] = {}

    def _strip(match: re.Match) -> str:
        n = int(match.group(1))
        if n not in valid:
            logger.warning("rag: model cited [%d] with only %d excerpts", n, len(context))
            return ""
        if n not in seen:
            r = context[n - 1]
            seen[n] = Citation(
                marker=n,
                video_id=r.video_id,
                title=r.title,
                channel_name=r.channel_name,
                start_time=r.start_time,
                youtube_url=r.youtube_url,
            )
        return match.group(0)

    cleaned = _CITATION_RE.sub(_strip, answer)
    return cleaned.strip(), [seen[n] for n in sorted(seen)]


def _anthropic_kwargs(query: str, context: list[SearchResult]) -> dict:
    return {
        "model": settings.rag_model,
        "max_tokens": settings.rag_max_tokens,
        "system": _SYSTEM,
        # Adaptive lets the model decide how much to reason about conflicting or
        # partial excerpts; effort caps the spend. No `budget_tokens` — it is
        # rejected on Opus 4.7+.
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": settings.rag_effort},
        # No temperature here on purpose — sampling params are rejected
        # alongside extended thinking. Determinism is an OpenAI-path knob only.
        "messages": [{"role": "user", "content": _build_prompt(query, context)}],
    }


def _openai_kwargs(query: str, context: list[SearchResult]) -> dict:
    kwargs = {
        "model": settings.rag_openai_model,
        # max_tokens, not max_completion_tokens: every OpenAI-compatible server
        # (Groq, Ollama, OpenRouter) accepts it. OpenAI reasoning models are the
        # exception and want max_completion_tokens instead.
        "max_tokens": settings.rag_max_tokens,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_prompt(query, context)},
        ],
    }
    if settings.rag_openai_temperature is not None:
        kwargs["temperature"] = settings.rag_openai_temperature
    return kwargs


# ── Provider calls — each returns plain answer text or raises _RagError ───────

async def _call_anthropic(query: str, context: list[SearchResult]) -> str:
    client = _anthropic().with_options(timeout=settings.rag_timeout_seconds)
    try:
        resp = await client.messages.create(**_anthropic_kwargs(query, context))
    except anthropic.APITimeoutError:
        raise _RagError(f"timed out after {settings.rag_timeout_seconds:.0f}s")
    except anthropic.RateLimitError:
        raise _RagError("rate limited")
    except anthropic.APIStatusError as exc:
        raise _RagError(f"api error {exc.status_code} — {exc.message}")
    except anthropic.APIConnectionError as exc:
        raise _RagError(f"connection error — {exc}")

    if resp.stop_reason == "refusal":
        raise _RagError("model refused")
    logger.info(
        "rag[anthropic]: in=%d out=%d model=%s",
        resp.usage.input_tokens, resp.usage.output_tokens, resp.model,
    )
    # content carries thinking blocks too — take the text ones only.
    return "".join(b.text for b in resp.content if b.type == "text").strip()


async def _call_openai(query: str, context: list[SearchResult]) -> str:
    client = _openai().with_options(timeout=settings.rag_timeout_seconds)
    try:
        resp = await client.chat.completions.create(**_openai_kwargs(query, context))
    except openai.APITimeoutError:
        raise _RagError(f"timed out after {settings.rag_timeout_seconds:.0f}s")
    except openai.RateLimitError:
        raise _RagError("rate limited / quota exhausted")
    except openai.APIStatusError as exc:
        raise _RagError(f"api error {exc.status_code} — {exc.message}")
    except openai.APIConnectionError as exc:
        raise _RagError(f"connection error — {exc}")

    usage = resp.usage
    logger.info(
        "rag[openai]: in=%s out=%s model=%s",
        getattr(usage, "prompt_tokens", "?"), getattr(usage, "completion_tokens", "?"),
        resp.model,
    )
    if not resp.choices:
        raise _RagError("no choices returned")
    return (resp.choices[0].message.content or "").strip()


# ── Generation ────────────────────────────────────────────────────────────────

def _gate(results: list[SearchResult]) -> str | None:
    """Return a skip reason when we shouldn't generate at all."""
    if not is_available():
        return SKIP_DISABLED
    if not results:
        return SKIP_LOW_CONFIDENCE
    if results[0].similarity < settings.rag_min_top_score:
        logger.info(
            "rag: skipping, top score %.3f < %.2f",
            results[0].similarity, settings.rag_min_top_score,
        )
        return SKIP_LOW_CONFIDENCE
    return None


async def generate(
    query: str, results: list[SearchResult]
) -> tuple[str | None, list[Citation] | None, str | None]:
    """Return (answer, citations, skip_reason). Never raises."""
    reason = _gate(results)
    if reason:
        return None, None, reason

    context = select_context(results)
    call = _call_openai if _provider() == PROVIDER_OPENAI else _call_anthropic
    try:
        raw = await call(query, context)
    except _RagError as exc:
        logger.warning("rag: %s", exc)
        return None, None, SKIP_ERROR
    except Exception:
        logger.exception("rag: unexpected generation failure")
        return None, None, SKIP_ERROR

    if not raw:
        return None, None, SKIP_ERROR

    answer, citations = _resolve_citations(raw, context)
    return answer, citations, None


async def _stream_anthropic(query: str, context: list[SearchResult]):
    client = _anthropic().with_options(timeout=settings.rag_timeout_seconds)
    async with client.messages.stream(**_anthropic_kwargs(query, context)) as resp:
        async for text in resp.text_stream:
            yield text
        final = await resp.get_final_message()
    if final.stop_reason == "refusal":
        raise _RagError("model refused mid-stream")
    logger.info(
        "rag[anthropic]: streamed in=%d out=%d model=%s",
        final.usage.input_tokens, final.usage.output_tokens, final.model,
    )


async def _stream_openai(query: str, context: list[SearchResult]):
    client = _openai().with_options(timeout=settings.rag_timeout_seconds)
    extra = (
        {"stream_options": {"include_usage": True}}
        if settings.rag_openai_stream_usage
        else {}
    )
    resp = await client.chat.completions.create(
        **_openai_kwargs(query, context), stream=True, **extra
    )
    usage = None
    async for chunk in resp:
        if chunk.usage:
            usage = chunk.usage           # arrives on the final, choice-less frame
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
    logger.info(
        "rag[openai]: streamed in=%s out=%s model=%s",
        getattr(usage, "prompt_tokens", "?"), getattr(usage, "completion_tokens", "?"),
        settings.rag_openai_model,
    )


async def stream(query: str, results: list[SearchResult]):
    """Yield ("token", text) as the answer is written, then one terminal event.

    Terminal event is ("done", {"answer", "citations"}) or ("skip", reason).
    Citations can only be resolved once the full text exists, so they arrive at
    the end — the client renders markers inline and links them on "done".
    """
    reason = _gate(results)
    if reason:
        yield "skip", reason
        return

    context = select_context(results)
    tokens = _stream_openai if _provider() == PROVIDER_OPENAI else _stream_anthropic
    buffer: list[str] = []
    try:
        async for text in tokens(query, context):
            buffer.append(text)
            yield "token", text
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("rag: stream failed")
        # Partial text may already have reached the client; tell it to stop
        # expecting more rather than leaving a cursor blinking forever.
        yield "skip", SKIP_ERROR
        return

    answer, citations = _resolve_citations("".join(buffer).strip(), context)
    yield "done", {"answer": answer, "citations": [c.model_dump() for c in citations]}
