from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_HERE = Path(__file__).parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_HERE / ".env"), extra="ignore")

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://ytsearch:ytsearch_dev@localhost:5433/ytsearch"

    # ── Redis (Celery broker + result backend) ────────────────────────────────
    
    redis_url: str = "redis://:redis_dev@localhost:6380/0"

    # ── Kafka ─────────────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = "localhost:9094"
    kafka_consumer_group: str = "python-indexer"
    kafka_auto_offset_reset: str = "earliest"

    # ── YouTube Data API ──────────────────────────────────────────────────────
    youtube_api_key: str = ""
    youtube_quota_daily_limit: int = 10_000

    # ── Embedding model (BAAI/bge-base-en-v1.5 → 768 dims) ───────────────────
    embedding_model_name: str = "BAAI/bge-base-en-v1.5"
    embedding_grpc_host: str = "0.0.0.0"
    embedding_grpc_port: int = 50051
    embedding_batch_size: int = 64

    # ── Device ────────────────────────────────────────────────────────────────
    # dev default: "cpu"
    # production GPU server: set DEVICE=cuda in .env
    # Applies to: SentenceTransformer embedding + faster-whisper transcription
    device: str = "cpu"

    # ── Whisper ───────────────────────────────────────────────────────────────
    # whisper_model_size: tiny (75 MB) | base | small | medium | large-v3 (3 GB)
    # whisper_compute_type:
    #   CPU  → "int8"
    #   GPU  → "float16"  (fastest, requires VRAM)
    #   GPU  → "int8_float16"  (saves VRAM, minimal quality loss)
    whisper_model_size: str = "tiny"
    whisper_compute_type: str = "int8"

    # ── Indexing pipeline ─────────────────────────────────────────────────────
    indexing_workers: int = 4
    chunk_duration_seconds: int = 30
    chunk_overlap_seconds: int = 5
    similarity_threshold: float = 0.60

    # ── Transcript fetching ───────────────────────────────────────────────────
    # Max concurrent YouTube transcript requests across all parallel workers.
    # With a rotating residential proxy this is the PRIMARY throughput knob —
    # each request exits a different IP, so raise it to your proxy plan's
    # concurrent-connection limit (e.g. 30–50). Capped in practice by the fetch
    # thread pool (sized from this value) and the embedding step.
    transcript_concurrent_limit: int = 3
    # Per-request timeout (seconds). With rotating IPs a slow exit should fail
    # FAST and retry on a fresh IP rather than holding a concurrency slot. 30s
    # suits filter-medium; raise toward 60 for the slower filter-high pool.
    transcript_request_timeout: int = 30
    # Delay range when NO cookies configured (conservative, ~1.5 req/s global rate)
    transcript_request_delay_min: float = 1.5
    transcript_request_delay_max: float = 2.5
    # Delay range when cookies ARE configured (YouTube more lenient, ~2 req/s global rate)
    transcript_request_delay_cookies_min: float = 1.0
    transcript_request_delay_cookies_max: float = 2.0
    # Path to a Netscape-format cookies.txt exported from your browser while
    # logged into YouTube — bypasses IP-level 429 rate limits
    youtube_cookies_path: str = ""
    # Residential proxy URL for YouTube requests, e.g.
    #   http://user:pass@host:port   (rotating residential endpoint)
    # Each request exits from a different residential IP, so YouTube's per-IP /
    # per-range caption throttle no longer accumulates. Must be RESIDENTIAL —
    # datacenter proxies are blocked by YouTube. Applied to BOTH youtube-transcript-api
    # (via the curl_cffi session) and yt-dlp (--proxy). Empty = direct connection.
    youtube_proxy_url: str = ""

    # ── JWT (FastAPI search API) ───────────────────────────────────────────────
    jwt_secret: str = "change-this-to-a-long-random-string-in-production"
    jwt_expiry_minutes: int = 15
    jwt_refresh_expiry_days: int = 7

    # ── gRPC Embedding client (FastAPI calls python-grpc service) ─────────────
    embedding_grpc_client_host: str = "localhost"
    embedding_grpc_client_port: int = 50051

    # ── Search tuning ────────────────────────────────────────────────────────
    search_min_similarity: float = 0.60          # fallback: minimum score for top-N retrieval
    search_threshold_similarity: float = 0.75    # primary: only chunks genuinely relevant to query
    search_threshold_hard_cap: int = 300         # cost ceiling when threshold mode is active
    search_threshold_min_results: int = 20       # fall back to top-N if threshold yields fewer than this
    search_high_quality_threshold: float = 0.65
    search_fallback_threshold: int = 5
    # Reranker score blend: final = bi_weight*bi_score + (1-bi_weight)*cross_prob.
    # 0.0 = pure cross-encoder (old behavior), 1.0 = pure bi-encoder. 0.4 lets the
    # cross-encoder lead while the bi-encoder anchors so one fragment can't flip a
    # strong semantic match (e.g. an Angular video beating a FastAPI one for "fastApi").
    search_bi_blend_weight: float = 0.4
    # Framework keyword signal the encoders miss: when the query names a framework,
    # +weight to videos that mention it, -weight to videos whose title is primarily a
    # rival framework. Symmetric knob; 0 disables. 0.15 is decisive over blend gaps.
    search_framework_weight: float = 0.15
    # Search-RESULT cache (Redis). Disabling it only stops the search from
    # reading/writing cache:search:* keys — Redis itself stays up for Celery.
    # Turn off while tuning search logic so you always see fresh results.
    search_cache_enabled: bool = True
    search_cache_ttl_hours: int = 6
    search_vector_weight: float = 0.7
    search_keyword_weight: float = 0.3

    # ── RAG (LLM answer synthesis over retrieved chunks) ─────────────────────
    # Master switch. When False the /api/search response never carries an
    # `answer` — retrieval-only behaviour, identical to before RAG existed.
    rag_enabled: bool = False
    # Which backend answers: "anthropic" | "openai".
    # "openai" speaks the OpenAI chat-completions protocol, which is also what
    # Groq, Google's compat endpoint, OpenRouter and Ollama serve — so switching
    # provider is a base_url + model + key change, nothing more. See
    # rag_openai_base_url below for the ones with a free tier.
    rag_provider: str = "anthropic"

    # ── Anthropic ─────────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    rag_model: str = "claude-opus-4-8"
    # Thinking depth / token spend: low | medium | high | max. Search is a
    # latency-sensitive path and grounded summarisation is not a hard reasoning
    # task, so "low" is the default — raise it if answers feel shallow.
    rag_effort: str = "low"
    # ── OpenAI-compatible (used when rag_provider="openai") ──────────────────
    # The OpenAI API itself is PAID. These endpoints have a free tier and speak
    # the same protocol — set base_url + model + key to switch:
    #   Groq      https://api.groq.com/openai/v1        llama-3.3-70b-versatile
    #   Gemini    https://generativelanguage.googleapis.com/v1beta/openai/
    #                                                   gemini-2.0-flash
    #   OpenRouter https://openrouter.ai/api/v1         (models suffixed ":free")
    #   Ollama    http://localhost:11434/v1             (local, no key — any
    #                                                    placeholder string works)
    rag_openai_base_url: str = "https://api.openai.com/v1"
    rag_openai_model: str = "gpt-4o-mini"
    openai_api_key: str = ""
    # Ask for a usage frame at the end of a stream so spend is logged on the
    # streaming path too. Real OpenAI supports it; some compatible servers
    # reject the unknown param — set false for those.
    rag_openai_stream_usage: bool = True
    # 0 = deterministic. Answers are cached in Redis, so sampling would let two
    # users asking the same thing get different text depending on cache timing.
    # Set to null/empty to omit the param entirely — OpenAI reasoning models
    # (o-series, gpt-5) reject any temperature other than the default.
    rag_openai_temperature: float | None = 0.0

    # ── Shared generation limits ─────────────────────────────────────────────
    # Ceiling for the whole response — Anthropic adaptive thinking tokens count
    # against it too.
    rag_max_tokens: int = 2000
    # How many reranked chunks to feed the model. The reranker already caps at
    # 10 and dedupes to one chunk per video; 6 keeps the prompt tight and the
    # citation list readable.
    rag_max_context_chunks: int = 6
    # Skip generation entirely when the best result scores below this — better
    # to show raw results than to synthesise confidently from weak matches.
    rag_min_top_score: float = 0.65
    # Hard ceiling on generation calls per browser session per day (Redis,
    # resets at midnight UTC). Independent of the search limit below because
    # generation is the expensive part. 0 disables the cap.
    rag_daily_limit: int = 20
    # Per-request timeout (seconds) for the Anthropic call. On timeout the
    # search still returns its results with answer=None.
    rag_timeout_seconds: float = 60.0

    # ── Billing / upgrade gate ────────────────────────────────────────────────
    # Master switch for monetization. When False: no search limits are enforced
    # (anonymous or logged-in) and the upgrade UI is hidden — the product is
    # fully open. Flip to True (here AND NEXT_PUBLIC_BILLING_ENABLED in web/) when
    # ready to charge.
    billing_enabled: bool = False

    # ── Free tier (anonymous, no login required) ──────────────────────────────
    # Daily search cap for users without an account, tracked per browser session
    # id in Redis (resets at midnight UTC). Only enforced when billing_enabled.
    free_daily_search_limit: int = 15

    # ── YouTube ingestion (Java-side logic now in Python) ────────────────────
    youtube_quota_per_day: int = 10_000
    youtube_search_cost_units: int = 100
    youtube_max_results_per_search: int = 50

    # ── Stripe ────────────────────────────────────────────────────────────────
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_success_url: str = "http://localhost:3000/billing/success"
    stripe_cancel_url: str = "http://localhost:3000/billing/cancel"
    stripe_pro_price_id: str = ""
    stripe_team_price_id: str = ""

    # ── Admin ─────────────────────────────────────────────────────────────────
    # Set ADMIN_API_KEY in .env to call admin endpoints without a JWT token.
    # Leave empty to require JWT-based login for all admin routes.
    admin_api_key: str = ""
    admin_emails: str = ""   # comma-separated list of admin user emails

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()