# System Architecture — YouTube AI Search Engine

## Overview

The system is split into two independent sides connected by **Apache Kafka**:

| Side | Language | Responsibility |
|---|---|---|
| **Python** | Python 3.11+ | Data ingestion — crawl, transcribe, embed, classify, write to DB |
| **Java** | Java 21 + Spring Boot | Data serving — search API, auth, billing, user features |
| **Kafka** | Apache Kafka | Event bus linking both sides for hybrid real-time indexing |

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              CLIENTS                                             │
│           Chrome Extension    ·    Web App    ·    API Consumers                 │
└─────────────────────────────────┬────────────────────────────────────────────────┘
                                  │ HTTP / REST
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                          JAVA SIDE — Spring Boot                                 │
│                         (Read / Serve / User-Facing)                             │
│                                                                                  │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│   │  Search      │  │  Auth        │  │  User        │  │  Billing     │        │
│   │  Module      │  │  Module      │  │  Module      │  │  Module      │        │
│   │              │  │              │  │              │  │              │        │
│   │ Hybrid search│  │ JWT / OAuth  │  │ Prefs/History│  │ Stripe       │        │
│   │ Re-ranking   │  │ Sessions     │  │ Saves        │  │ Plan limits  │        │
│   │ Cache lookup │  │ RBAC         │  │ Daily usage  │  │ API keys     │        │
│   └──────┬───────┘  └──────────────┘  └──────────────┘  └──────────────┘        │
│          │                                                                       │
│          │ gRPC (query embedding only)                                           │
│          ▼                                                                       │
│   ┌──────────────┐                                                               │
│   │  Embedding   │◄── calls Python Embedding Service for query vectors           │
│   │  Client      │                                                               │
│   └──────┬───────┘                                                               │
│          │ Kafka Producer                                                        │
│          │ (search.miss, user.video.watched, channel.discover.request)           │
└──────────┼───────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                          SHARED INFRASTRUCTURE                                   │
│                                                                                  │
│  ┌────────────────────────────┐   ┌──────────────────────────────────────────┐   │
│  │   PostgreSQL + pgvector    │   │               Apache Kafka               │   │
│  │                            │   │                                          │   │
│  │  · videos                  │   │  Topics:                                 │   │
│  │  · channels                │   │  → video.index.request  (Java → Python) │   │
│  │  · transcripts             │   │  → video.index.complete (Python → Java) │   │
│  │  · transcript_chunks       │   │  → search.miss          (Java → Python) │   │
│  │  · users / sessions        │   │  → user.video.watched   (Java → Python) │   │
│  │  · search_history          │   │  → channel.discover     (Java → Python) │   │
│  │  · indexing_jobs           │   │  → index.error          (Python → Java) │   │
│  │  · industries / topics     │   │                                          │   │
│  └────────────────────────────┘   └──────────────────────────────────────────┘   │
│                                                                                  │
│  ┌────────────────────────────┐                                                  │
│  │         Redis              │                                                  │
│  │                            │                                                  │
│  │  · Query result cache      │                                                  │
│  │  · Rate limiting           │                                                  │
│  │  · Session tokens          │                                                  │
│  │  · Indexing job state      │                                                  │
│  └────────────────────────────┘                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                         PYTHON SIDE — FastAPI + Workers                          │
│                        (Write / Ingest / AI Processing)                          │
│                                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │  Crawler     │  │  Transcript  │  │  Embedding   │  │  Classifier  │         │
│  │  Service     │  │  Fetcher     │  │  Service     │  │  Service     │         │
│  │              │  │              │  │              │  │              │         │
│  │ YouTube API  │  │ youtube-     │  │ BAAI/bge-    │  │ Industry     │         │
│  │ Playlist API │  │ transcript-  │  │ small-en-v1.5│  │ detection    │         │
│  │ yt-dlp       │  │ api / yt-dlp │  │ (gRPC server)│  │ (4-level)    │         │
│  └──────────────┘  └──────────────┘  └──────┬───────┘  └──────────────┘         │
│                                             │ exposes gRPC to Java               │
│  ┌──────────────┐  ┌──────────────┐         │                                    │
│  │  Kafka       │  │  Scheduler   │         │                                    │
│  │  Consumer    │  │  (Celery Beat│         │                                    │
│  │              │  │  + APSched.) │         │                                    │
│  │ Listens to   │  │              │         │                                    │
│  │ all topics   │  │ Cron jobs    │         │                                    │
│  │ from Java    │  │ per schedule │         │                                    │
│  └──────────────┘  └──────────────┘         │                                    │
└─────────────────────────────────────────────┴────────────────────────────────────┘
```

---

## Python Side — Data Ingestion Engine

### Responsibility

Everything that touches AI models, YouTube APIs, or writes data to the database.

### Services

#### 1. Crawler Service
Discovers video IDs from YouTube without burning quota.

```
Strategy:
  Primary:   Playlist API (1 unit per 50 videos) — channel uploads
  Secondary: Search API  (100 units per request) — new topic discovery
  Tertiary:  User events from Kafka              — zero quota cost

Output: video IDs saved to `indexing_jobs` table with status = 'pending'
```

#### 2. Transcript Fetcher
Pulls transcripts for videos in the indexing queue.

```
Priority order:
  1. youtube-transcript-api (manual captions)    — free, fastest
  2. youtube-transcript-api (auto-generated)     — free, good quality
  3. yt-dlp subtitle download                    — free, parallel-friendly
  4. Whisper AI (local)                          — free, slow (3–5 min/video)
                                                   only for high-value videos
                                                   without captions
```

#### 3. Embedding Service (gRPC Server)
The only AI model in the system. Exposed via gRPC so Java can embed search queries without duplicating the model.

```
Model:    BAAI/bge-small-en-v1.5 (384 dimensions)
Endpoint: grpc://embedding-service:50051

Methods:
  EmbedOne(text) → vector[384]
  EmbedBatch(texts[]) → vector[][384]

Used by:
  · Python workers (embed transcript chunks during indexing)
  · Java Search Module (embed user query at search time)
```

> **Why gRPC not REST?** Lower latency for the query embedding call that happens on every search request. Binary protocol, streaming support, strong typing.

#### 4. Classifier Service
Determines which industry a video belongs to using a 4-level cascade:

```
Level 1: Channel inheritance    → 95% accuracy, 80% of videos
Level 2: YouTube categoryId     → 70% accuracy, 15% of videos
Level 3: Keyword matching       → 60% accuracy, 4% of videos
Level 4: Embedding similarity   → 80% accuracy, fallback
```

#### 5. Indexing Worker (Core Pipeline)
Consumes from the `indexing_jobs` queue and runs the full pipeline per video:

```
For each video:
  1. Fetch transcript
  2. Classify industry (if unknown)
  3. Chunk transcript (30-sec windows, 5-sec overlap)
  4. Batch embed all chunks (32 at a time)
  5. Write to PostgreSQL (transcripts + transcript_chunks)
  6. Publish to Kafka topic: video.index.complete
  7. Update indexing_jobs status = 'done'
```

#### 6. Scheduler (Celery Beat + APScheduler)

```
Schedule:
  Every 1 hour:    Fetch trending videos → add to indexing queue
  Every 6 hours:   Scan all monitored channels for new uploads
  Every day 00:00: Discover new channels by topic rotation
  Every Sunday:    Re-classify uncategorized videos
  Continuous:      4 parallel indexing workers running 24/7
```

#### 7. Kafka Consumer
Listens for events published by the Java side:

| Topic | Action |
|---|---|
| `search.miss` | Fetch + index the missed video immediately (real-time fallback) |
| `user.video.watched` | Queue video for background indexing |
| `video.index.request` | Explicit index request (e.g., user submits a URL) |
| `channel.discover.request` | Add a new channel to the crawler list |

### Python Tech Stack

| Component | Library / Tool |
|---|---|
| Web framework (admin/internal) | FastAPI |
| Task queue | Celery + Redis broker |
| Scheduler | Celery Beat |
| gRPC server | `grpcio` + `grpcio-tools` |
| YouTube API | `google-api-python-client` |
| Transcript fetcher | `youtube-transcript-api`, `yt-dlp` |
| Whisper AI | `openai-whisper` (optional) |
| Embedding model | `sentence-transformers` |
| Database ORM | SQLAlchemy (async) + `asyncpg` |
| Kafka client | `confluent-kafka` |
| Validation | Pydantic v2 |

---

## Java Side — Data Serving Layer

### Responsibility

Every user-facing operation: search, auth, billing, user preferences. Never writes raw video/transcript data directly — that is Python's job.

### Modules (Spring Boot)

#### 1. Search Module

```
Flow per search request:
  1. Check Redis cache (key = MD5 of normalized query)
     → Cache hit: return in <5ms
  2. Embed query by calling Python gRPC Embedding Service
     → Returns vector[384]
  3. Run Hybrid Search against PostgreSQL:
     → Vector search (pgvector cosine distance)
     → Keyword search (PostgreSQL full-text, plainto_tsquery)
     → Merge: score = (0.7 × vector) + (0.3 × keyword)
  4. Re-rank top 20 results:
     → Apply view count, quality score, recency, user interest boosts
  5. Enforce plan limits (daily_usage table via Redis counter)
  6. If DB result count < 5:
     → Publish to Kafka topic: search.miss
     → Return partial results + flag "more results indexing..."
  7. Log to search_history and popular_searches
  8. Cache result in Redis (TTL = 6 hours)
  9. Return top 10 to client
```

#### 2. Auth Module
- JWT access tokens (15-minute expiry) + refresh tokens (7 days)
- OAuth2 (Google, GitHub)
- Session stored in `user_sessions` table + Redis
- RBAC: free / pro / team / admin roles

#### 3. User Module
- Search history with pagination
- Saved videos
- Industry/topic preferences (auto-learned from click behavior)
- User interest score updates after each `search_clicks` event

#### 4. Billing Module
- Stripe integration (webhooks for subscription events)
- Plan enforcement delegated to Redis daily counters
- API key management for Team plan

#### 5. Analytics Module
- Records `search_history`, `search_clicks`, `video_interactions`
- Updates `popular_searches` for autocomplete
- Publishes `user.video.watched` to Kafka when user clicks a result

#### 6. Kafka Producer
Java publishes events to Kafka when:

| Event | Topic | Payload |
|---|---|---|
| Search returns < 5 results | `search.miss` | `{query, top_youtube_video_ids}` |
| User clicks a video result | `user.video.watched` | `{user_id, video_id, timestamp}` |
| User submits a video URL | `video.index.request` | `{video_id, priority: high}` |
| Admin adds a new channel | `channel.discover.request` | `{channel_id, industry_id}` |

### Java Tech Stack

| Component | Library / Tool |
|---|---|
| Framework | Spring Boot 3.x |
| REST API | Spring Web (MVC) |
| Database | Spring Data JPA + HikariCP connection pool |
| pgvector | `pgvector-spring` / native JDBC for vector queries |
| Cache | Spring Cache + Lettuce (Redis client) |
| Kafka | Spring Kafka |
| gRPC client | `grpc-java` (calls Python Embedding Service) |
| Auth | Spring Security + JWT (`jjwt`) |
| Billing | Stripe Java SDK |
| Validation | Jakarta Bean Validation |
| Testing | JUnit 5, Testcontainers |

---

## Kafka — The Bridge

### Why Kafka Here

| Scenario | Without Kafka | With Kafka |
|---|---|---|
| Search miss (no DB results) | Java calls Python directly — tight coupling | Java publishes event, Python indexes async — decoupled |
| User watches a video | Java writes to DB then calls Python — two responsibilities | Java fires event, Python handles indexing — clean separation |
| Python worker crashes | Java hangs waiting | Kafka retains the event, Python retries on recovery |
| Scale Python workers | Redeploy both sides | Scale Python consumers independently |

### Topics and Retention

| Topic | Direction | Retention | Partitions |
|---|---|---|---|
| `video.index.request` | Java → Python | 7 days | 6 |
| `video.index.complete` | Python → Java | 1 day | 6 |
| `search.miss` | Java → Python | 3 days | 12 |
| `user.video.watched` | Java → Python | 3 days | 12 |
| `channel.discover.request` | Java → Python | 7 days | 3 |
| `index.error` | Python → Java | 7 days | 3 |

### Real-Time Fallback Flow (Hybrid Approach)

This is the hybrid moment where both sides collaborate via Kafka:

```
User searches: "golang goroutines explained"
                          │
                          ▼
                   Java Search Module
                          │
                 DB results < 5 found?
                          │
                    YES (cache miss)
                          │
           ┌──────────────┴──────────────┐
           │                             │
           ▼                             ▼
  Return partial results          Publish to Kafka
  + "Searching more..."           topic: search.miss
  (non-blocking response)         { query, youtube_ids }
                                         │
                                         ▼
                                  Python Kafka Consumer
                                         │
                               Fetch transcripts (async)
                               Generate embeddings
                               Save to PostgreSQL
                                         │
                                         ▼
                                  Publish to Kafka
                                  topic: video.index.complete
                                  { video_ids, query }
                                         │
                                         ▼
                                  Java Kafka Consumer
                                         │
                               Invalidate Redis cache for query
                               (Next user search hits fresh DB results)
```

---

## Shared Infrastructure

### PostgreSQL + pgvector

- **Single database**, shared by both sides
- Python: writes (INSERT/UPDATE transcripts, chunks, videos)
- Java: reads (SELECT for search, user data, billing)
- **Indexing strategy for `transcript_chunks`:**

```sql
-- HNSW index (best for high-throughput search)
CREATE INDEX ON transcript_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Full-text index for keyword search (hybrid)
CREATE INDEX ON transcript_chunks
USING gin (to_tsvector('english', text));
```

- **When to add partitioning:** Once `transcript_chunks` exceeds 50M rows, partition by `industry_id`

### Redis

| Usage | Key Pattern | TTL |
|---|---|---|
| Query result cache | `cache:search:{md5(query)}` | 6 hours |
| Daily search counter | `usage:{user_id}:{date}` | 24 hours |
| Session tokens | `session:{token}` | 7 days |
| Indexing job state | `job:{video_id}` | 1 hour |
| Rate limit counter | `rl:{ip}:{minute}` | 1 minute |

### gRPC — Query Embedding Bridge

Python runs a gRPC server for embedding. Java calls it on every search.

```protobuf
// embedding.proto

service EmbeddingService {
  rpc EmbedOne (EmbedRequest) returns (EmbedResponse);
  rpc EmbedBatch (EmbedBatchRequest) returns (EmbedBatchResponse);
}

message EmbedRequest {
  string text = 1;
}

message EmbedResponse {
  repeated float vector = 1;  // 384 dimensions
}
```

**Latency target:** < 20ms per query embedding call (model kept in memory, warm).

---

## Scalability Plan

### Phase 1 — MVP (Single Server, < 5k users)

```
One VPS ($50/month):
  · Python workers: 4 parallel processes
  · Java Spring Boot: 1 instance
  · PostgreSQL: same server
  · Redis: same server
  · Kafka: single broker (or use Redis Streams as substitute)

Capacity: ~100k videos indexed, ~5k daily active users
```

### Phase 2 — Growth (Separate Servers, 5k–50k users)

```
Python server ($40/month):
  · 4–8 indexing workers
  · Crawler + scheduler

Java server ($40/month):
  · Spring Boot (2 instances behind API gateway)

Database server ($50/month):
  · PostgreSQL primary (writes)
  · 1 read replica (Java search queries)

Kafka: Confluent Cloud free tier → standard ($50/month)
Redis: Managed Redis ($20/month)

Total: ~$200/month
Capacity: ~1M videos, ~50k daily active users
```

### Phase 3 — Scale (50k–500k users)

```
Python side:
  · Auto-scaling worker pool (scale by Kafka lag)
  · 10–20 workers during peak indexing

Java side:
  · 3–5 instances behind API gateway
  · Read replicas for DB: 2–3

Database:
  · Add table partitioning on transcript_chunks (by industry_id)
  · pgvector HNSW index tuning

Kafka:
  · 3-broker cluster
  · Increase partitions on high-volume topics

Total: ~$500–800/month
```

### Scaling Independently

Because Python and Java are decoupled via Kafka:

| Need | Action | Affects |
|---|---|---|
| Spike in indexing demand | Scale Python workers up | Python only |
| Spike in user search traffic | Scale Java instances up | Java only |
| Large batch indexing job | Add temporary Python workers | Python only |
| Search latency issue | Tune pgvector index or add read replica | DB only |

---

## Project Folder Structure

```
youtube-search-engine/
│
├── python/                         # Python side
│   ├── services/
│   │   ├── crawler/                # YouTube API, channel discovery
│   │   ├── transcript/             # youtube-transcript-api, yt-dlp
│   │   ├── embedding/              # gRPC server, BAAI model
│   │   ├── classifier/             # Industry detection
│   │   └── indexing/               # Main pipeline worker
│   ├── workers/
│   │   ├── kafka_consumer.py       # Listens to Java events
│   │   └── scheduler.py            # Cron job definitions
│   ├── db/
│   │   ├── models.py               # SQLAlchemy models
│   │   └── migrations/             # Alembic migrations
│   ├── proto/
│   │   └── embedding.proto         # gRPC contract
│   ├── config.py
│   ├── main.py                     # FastAPI (admin/internal endpoints)
│   └── requirements.txt
│
├── java/                           # Java side
│   └── src/main/java/com/ytsearch/
│       ├── search/                 # Hybrid search, re-ranking
│       ├── auth/                   # JWT, OAuth, sessions
│       ├── user/                   # Preferences, history, saves
│       ├── billing/                # Stripe, plans
│       ├── analytics/              # Tracking, popular searches
│       ├── kafka/
│       │   ├── producer/           # Publishes events
│       │   └── consumer/           # Listens for index.complete
│       ├── grpc/
│       │   └── EmbeddingClient.java  # Calls Python gRPC
│       └── config/
│
├── infra/
│   ├── docker-compose.yml          # Local dev: Postgres, Redis, Kafka
│   ├── kafka/
│   │   └── topics.sh               # Topic creation script
│   └── db/
│       └── schema.sql              # Shared DB schema (v2)
│
└── proto/
    └── embedding.proto             # Shared gRPC contract (both sides use this)
```

---

## Data Flow Summary

### Write Path (Python owns this)

```
YouTube API / Kafka event
        ↓
  Crawler (discover video IDs)
        ↓
  Transcript Fetcher (youtube-transcript-api)
        ↓
  Classifier (determine industry)
        ↓
  Chunker (30-sec windows, 5-sec overlap)
        ↓
  Embedding Service (batch embed chunks)
        ↓
  PostgreSQL (write transcripts + chunks)
        ↓
  Kafka: publish video.index.complete
```

### Read Path (Java owns this)

```
Client HTTP request
        ↓
  Redis cache check
        ↓ (miss)
  gRPC → Python Embedding Service (embed query)
        ↓
  PostgreSQL: vector search + keyword search
        ↓
  Merge scores (0.7 vector + 0.3 keyword)
        ↓
  Re-rank (quality, recency, user preferences)
        ↓
  Enforce plan limits (Redis counter)
        ↓
  Cache result (Redis, 6-hour TTL)
        ↓
  Return to client
  + (if results < 5) Kafka: publish search.miss
```

---

## Key Design Principles

| Principle | How It's Applied |
|---|---|
| **Separation of concerns** | Python writes, Java reads — never cross the line |
| **Loose coupling** | Kafka between sides; neither calls the other directly except gRPC for embedding |
| **Single embedding model** | One gRPC server, both sides use it — no duplication |
| **Async by default** | Python workers are async; Kafka decouples all cross-side events |
| **Scale what needs scaling** | Workers and API instances scale independently |
| **Cache aggressively** | Redis for query results, rate limits, and sessions |
| **One schema** | Single PostgreSQL instance; migrations owned by Python (Alembic) |