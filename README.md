# YTSearch — YouTube AI Semantic Search Engine

A full-stack AI-powered search engine that indexes YouTube video transcripts and enables semantic search over them. The entire backend is **Python + FastAPI** — Java has been retired.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client / Frontend                        │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP  :8080
┌────────────────────────────▼────────────────────────────────────┐
│           python-search  (FastAPI — api/main.py)                │
│  /api/auth   /api/search   /api/user   /api/billing             │
│  /api/ingestion  (admin)                                        │
└──┬───────────┬──────────────┬──────────────┬────────────────────┘
   │ asyncpg   │ redis.asyncio │ grpc         │ confluent-kafka
   ▼           ▼               ▼              ▼
PostgreSQL   Redis 7        python-grpc    Kafka (KRaft)
+ pgvector   (cache +       (BAAI/bge      (event bus)
             rate limit +   -small-en)
             ingest queue)
                                ▲
                python-worker ──┘  (Celery indexing pipeline)
                python-beat        (Celery Beat scheduler)
                python-kafka       (Kafka consumer)
                python-api  :8001  (admin endpoints)
```

---

## Services

| Service | Port | Description |
|---------|------|-------------|
| `python-search` | **8080** | User-facing FastAPI API (auth, search, user, billing, ingestion) |
| `python-api` | 8001 | Internal admin API (indexing queue management) |
| `python-grpc` | 50051 | gRPC embedding server (BAAI/bge-small-en-v1.5, 384 dims) |
| `python-worker` | — | Celery workers — transcript fetch → chunk → embed → store |
| `python-beat` | — | Celery Beat cron scheduler |
| `python-kafka` | — | Kafka consumer (search.miss, video.index.request, etc.) |
| `postgres` | 5432 | PostgreSQL 16 + pgvector |
| `redis` | 6379 | Cache + Celery broker + ingestion queue + quota tracking |
| `kafka` | 9094 | Kafka 3.7 KRaft (no Zookeeper) |

---

## Quick Start

### Prerequisites
- Docker and Docker Compose
- (Optional) YouTube Data API v3 key for video discovery

### 1. Configure environment

Create `python/.env`:

```env
DATABASE_URL=postgresql+asyncpg://ytsearch:ytsearch_dev@postgres:5432/ytsearch
REDIS_URL=redis://:redis_dev@redis:6379/0
KAFKA_BOOTSTRAP_SERVERS=kafka:9092

JWT_SECRET=change-this-to-a-long-random-string-in-production
JWT_EXPIRY_MINUTES=15
JWT_REFRESH_EXPIRY_DAYS=7

EMBEDDING_GRPC_CLIENT_HOST=python-grpc
EMBEDDING_GRPC_CLIENT_PORT=50051

YOUTUBE_API_KEY=your-key-here

STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRO_PRICE_ID=price_...
STRIPE_TEAM_PRICE_ID=price_...
```

### 2. Start all services

```bash
docker compose up -d
```

### 3. Apply database schema (first run)

```bash
docker compose exec postgres \
  psql -U ytsearch -d ytsearch -f /docker-entrypoint-initdb.d/schema.sql
```

### 4. Verify

```bash
curl http://localhost:8080/health
# {"status":"ok","db":"ok","version":"2.0.0"}

# Interactive API docs
open http://localhost:8080/docs
```

---

## API Reference

### Authentication — `/api/auth`

| Method | Endpoint | Auth | Body |
|--------|----------|------|------|
| `POST` | `/api/auth/register` | Public | `{email, password, name}` |
| `POST` | `/api/auth/login` | Public | `{email, password}` |
| `POST` | `/api/auth/refresh` | Header: `X-Refresh-Token: <token>` | — |

**Response:**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "email": "user@example.com",
  "name": "Alice"
}
```

---

### Search — `/api/search`

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/search` | Optional JWT | Semantic search over transcripts |
| `POST` | `/api/search/click` | Optional JWT | Record click (re-ranking signal) |

**Request:**
```json
{
  "query": "how to implement binary search tree",
  "industry": "programming",
  "page": 0,
  "size": 10
}
```

**Response:**
```json
{
  "query": "how to implement binary search tree",
  "total_results": 14,
  "source": "indexed",
  "latency_ms": 87,
  "indexing_more": false,
  "results": [
    {
      "video_id": "dQw4w9WgXcQ",
      "title": "Binary Search Trees Explained",
      "thumbnail_url": "https://i.ytimg.com/vi/...",
      "channel_name": "CS Dojo",
      "text": "...so the left child is always smaller than the parent...",
      "start_time": 142.5,
      "end_time": 172.5,
      "view_count": 850000,
      "similarity": 0.823,
      "youtube_url": "https://youtu.be/dQw4w9WgXcQ?t=142"
    }
  ]
}
```

**Industry values:** `programming` · `business` · `education` · `creative` · `fitness`

**Hybrid scoring formula:**
```
score = (0.7 × cosine_similarity) + (0.3 × ts_rank)
boost = +0.05 if view_count > 100,000
```

---

### User — `/api/user`

All endpoints require `Authorization: Bearer <access_token>`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/user/me` | Profile + active plan details |
| `GET` | `/api/user/history?page=0&size=20` | Paginated search history |
| `POST` | `/api/user/saves/{youtubeVideoId}` | Save a video |
| `DELETE` | `/api/user/saves/{youtubeVideoId}` | Unsave a video |

---

### Billing — `/api/billing`

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/billing/checkout` | JWT | Create Stripe checkout session |
| `POST` | `/api/billing/webhook` | Stripe-Signature header | Handle subscription lifecycle events |

**Checkout:**
```json
{ "price_id": "price_pro_monthly" }
// → { "url": "https://checkout.stripe.com/..." }
```

**Subscription plans:**

| Plan | Price | Searches/day | API access |
|------|-------|--------------|------------|
| Free | $0    | 10           | No         |
| Pro  | $8/mo | Unlimited    | No         |
| Team | $29/mo| Unlimited    | Yes        |

---

### Ingestion — `/api/ingestion` (Admin)

Requires Team plan.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/ingestion/discover?query=...` | Search YouTube + enqueue new videos |
| `GET` | `/api/ingestion/status` | Job counts + Redis queue size + quota remaining |

```json
// GET /api/ingestion/status
{
  "jobs_pending": 12,
  "jobs_processing": 3,
  "jobs_done": 4821,
  "jobs_failed": 7,
  "redis_queue_size": 48,
  "quota_remaining": 8200
}
```

---

## Project Structure

```
YT/
├── python/
│   ├── api/                        # FastAPI user-facing API (port 8080)
│   │   ├── main.py                 # App entry point + CORS + error handlers
│   │   ├── dependencies.py         # DB session, Redis, JWT auth DI
│   │   ├── schemas/
│   │   │   ├── auth.py             # RegisterRequest, LoginRequest, AuthResponse
│   │   │   ├── search.py           # SearchRequest, SearchResponse, SearchResult
│   │   │   ├── user.py             # UserProfile, PaginatedHistory, ClickRequest
│   │   │   └── billing.py          # CheckoutRequest, CheckoutResponse
│   │   ├── services/
│   │   │   ├── jwt_service.py      # HS256 token create/decode/hash
│   │   │   ├── auth_service.py     # register, login, refresh (BCrypt + JWT)
│   │   │   ├── search_service.py   # 12-step search orchestration pipeline
│   │   │   ├── embedding_client.py # gRPC EmbedOne client (asyncio.to_thread)
│   │   │   ├── user_service.py     # profile, saves, history, interest update
│   │   │   ├── billing_service.py  # Stripe checkout session + webhook handler
│   │   │   ├── analytics_service.py# Async popular-search / click / interaction
│   │   │   ├── ingestion_service.py# YouTube discovery, Redis queue, quota Lua
│   │   │   └── kafka_producer.py   # publish search.miss / index.request / etc.
│   │   └── routers/
│   │       ├── auth.py             # POST /api/auth/{register,login,refresh}
│   │       ├── search.py           # POST /api/search, /api/search/click
│   │       ├── user.py             # /api/user/{me,history,saves}
│   │       ├── billing.py          # /api/billing/{checkout,webhook}
│   │       └── ingestion.py        # /api/ingestion/{discover,status}
│   │
│   ├── db/
│   │   ├── session.py              # Async SQLAlchemy engine + session factory
│   │   └── models.py               # All ORM models (users, videos, transcripts…)
│   │
│   ├── services/
│   │   ├── embedding/              # gRPC server (python-grpc container)
│   │   │   ├── model.py            # BAAI/bge-small-en-v1.5 singleton
│   │   │   └── server.py           # EmbedOne / EmbedBatch RPC handlers
│   │   ├── transcript/
│   │   │   ├── fetcher.py          # youtube-transcript-api + yt-dlp fallback
│   │   │   └── chunker.py          # 30-second overlapping chunks
│   │   ├── classifier/
│   │   │   └── classifier.py       # 4-level industry classification cascade
│   │   ├── indexing/
│   │   │   ├── pipeline.py         # 10-step indexing pipeline
│   │   │   └── worker.py           # Celery tasks
│   │   └── crawler/
│   │       └── channel_crawler.py  # YouTube Data API channel/trending crawler
│   │
│   ├── workers/
│   │   ├── scheduler.py            # Celery Beat cron jobs
│   │   └── kafka_consumer.py       # Event consumer (search.miss, index.request…)
│   │
│   ├── proto/
│   │   └── embedding.proto         # gRPC contract (EmbedOne / EmbedBatch)
│   │
│   ├── main.py                     # Admin FastAPI app (port 8001)
│   ├── config.py                   # Pydantic Settings (all env vars)
│   └── requirements.txt
│
├── infra/
│   ├── db/schema.sql               # PostgreSQL schema + functions + seed data
│   └── postgres/init.sql           # Extension setup (pgvector, pg_trgm)
│
└── docker-compose.yml              # All services orchestration
```

---

## Key Design Decisions

### Two-stage industry search

```sql
-- Stage 1: use videos.embedding (HNSW index) to narrow to top-100 videos
--          in the requested industry — avoids scanning all chunks
WITH candidate_videos AS (
    SELECT id FROM videos
    WHERE primary_industry_id = $industry_id
      AND indexing_status = 'indexed'
    ORDER BY embedding <=> $query_vector LIMIT 100
),
-- Stage 2: best matching chunk within those videos (hybrid scoring)
ranked_chunks AS (
    SELECT tc.*, 1 - (tc.embedding <=> $query_vector) AS similarity
    FROM transcript_chunks tc
    WHERE tc.video_id IN (SELECT id FROM candidate_videos)
)
```

### Search caching

Results cached in Redis for **6 hours** (configurable via `SEARCH_CACHE_TTL_HOURS`). Cache key = `cache:search:{md5(query::industry)}`. Cache is invalidated when new indexed videos arrive (Kafka `video.index.complete` consumer).

### Plan enforcement

SQL function `can_user_search(user_id)` checks `daily_usage` vs `subscription_plans.searches_per_day`. Called before embedding to avoid unnecessary gRPC hops. `increment_daily_usage(user_id)` runs after every successful search.

### Async analytics

All analytics writes (`popular_searches`, `search_clicks`, `video_interactions`) run as background `asyncio.Task`s — never blocking the response path.

### YouTube quota guard

Atomic Lua script in Redis tracks daily API quota. `EXPIREAT` set to next UTC midnight resets it automatically. 10,000 units/day → 100 video-discovery searches/day (100 units each).

### Kafka event contracts (unchanged)

| Topic | Producer | Consumer | Payload |
|-------|----------|----------|---------|
| `search.miss` | python-search | python-kafka | `{query, youtube_video_ids}` |
| `video.index.request` | python-search | python-kafka | `{video_id, priority}` |
| `user.video.watched` | python-search | python-kafka | `{user_id, video_id}` |
| `channel.discover.request` | python-search | python-kafka | `{channel_id, industry_id}` |
| `video.index.complete` | python-worker | python-kafka | `{youtube_video_id}` |

---

## Java Migration Notes

The Java Spring Boot service is retired. `python-search` on port 8080 is a drop-in replacement:

| Aspect | Java | Python |
|--------|------|--------|
| Framework | Spring Boot 3.3 | FastAPI 0.115 |
| Auth | Spring Security + JJWT | python-jose HS256 |
| DB | Spring Data JPA + JDBC | SQLAlchemy asyncio |
| Cache | Spring Cache + Redis | redis.asyncio |
| Kafka | Spring Kafka | confluent-kafka |
| gRPC | grpc-java (blocking stub) | grpcio (to_thread) |
| Billing | stripe-java SDK | stripe Python SDK |

**JWT tokens are not cross-compatible** — users need to re-login after cutover.
