-- =============================================================================
-- YouTube AI Search Engine — Database Schema v2
-- PostgreSQL 16 + pgvector
-- Migrations owned by Python / Alembic
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;    -- vector similarity search
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- trigram fuzzy match (autocomplete)


-- =============================================================================
-- CONFIGURATION
-- =============================================================================

CREATE TABLE industries (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100)    NOT NULL,
    slug            VARCHAR(100)    NOT NULL UNIQUE,
    description     TEXT,
    -- Embedding of the industry description text.
    -- Python Classifier uses cosine similarity against this to classify unknown videos (Level 4).
    embedding       vector(384),
    is_active       BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE languages (
    id              SERIAL PRIMARY KEY,
    code            VARCHAR(10)     NOT NULL UNIQUE,   -- 'en', 'hi', 'es'
    name            VARCHAR(100)    NOT NULL,
    is_active       BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE topics (
    id              SERIAL PRIMARY KEY,
    industry_id     INTEGER         NOT NULL REFERENCES industries(id) ON DELETE CASCADE,
    name            VARCHAR(150)    NOT NULL,
    slug            VARCHAR(150)    NOT NULL,
    keywords        TEXT[]          NOT NULL DEFAULT '{}',
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (industry_id, slug)
);


-- =============================================================================
-- CONTENT
-- =============================================================================

CREATE TABLE channels (
    id                  SERIAL PRIMARY KEY,
    youtube_channel_id  VARCHAR(50)     NOT NULL UNIQUE,
    name                VARCHAR(255)    NOT NULL,
    description         TEXT,
    thumbnail_url       VARCHAR(500),
    subscriber_count    BIGINT          DEFAULT 0,
    primary_industry_id INTEGER         REFERENCES industries(id) ON DELETE SET NULL,
    language_id         INTEGER         REFERENCES languages(id)  ON DELETE SET NULL,
    is_monitored        BOOLEAN         NOT NULL DEFAULT FALSE,
    -- Manually assigned quality score (0–1). Used in re-ranking to boost reputable channels.
    quality_score       NUMERIC(3,2)    DEFAULT 0.50 CHECK (quality_score BETWEEN 0 AND 1),
    last_crawled_at     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- A channel can span multiple industries (e.g. a tech + business channel)
CREATE TABLE channel_industries (
    channel_id      INTEGER NOT NULL REFERENCES channels(id)   ON DELETE CASCADE,
    industry_id     INTEGER NOT NULL REFERENCES industries(id) ON DELETE CASCADE,
    is_primary      BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (channel_id, industry_id)
);

CREATE TABLE videos (
    id                  SERIAL PRIMARY KEY,
    youtube_video_id    VARCHAR(20)     NOT NULL UNIQUE,
    channel_id          INTEGER         REFERENCES channels(id)   ON DELETE SET NULL,
    title               VARCHAR(500)    NOT NULL,
    description         TEXT,
    thumbnail_url       VARCHAR(500),
    duration_seconds    INTEGER,
    view_count          BIGINT          DEFAULT 0,
    published_at        TIMESTAMPTZ,
    primary_industry_id INTEGER         REFERENCES industries(id) ON DELETE SET NULL,
    language_id         INTEGER         REFERENCES languages(id)  ON DELETE SET NULL,
    -- Derived quality signal (0–1). Computed from view count, channel quality, caption confidence.
    quality_score       NUMERIC(3,2)    DEFAULT 0.50 CHECK (quality_score BETWEEN 0 AND 1),

    -- ── INDUSTRY EMBEDDING ────────────────────────────────────────────────────
    -- Mean vector of all transcript_chunks.embedding for this video.
    -- Computed by the Python indexing worker after all chunks are embedded.
    --
    -- Two-stage search pattern (Java Search Module):
    --   Stage 1 — narrow by industry using this column:
    --     SELECT id FROM videos
    --     WHERE primary_industry_id = $industry_id
    --     ORDER BY embedding <=> $query_vector LIMIT 100;
    --   Stage 2 — find the best chunk within those videos:
    --     SELECT * FROM transcript_chunks
    --     WHERE video_id = ANY($stage1_ids)
    --     ORDER BY embedding <=> $query_vector LIMIT 20;
    --
    -- This avoids scanning every chunk across all industries.
    -- ─────────────────────────────────────────────────────────────────────────
    embedding           vector(384),

    indexing_status     VARCHAR(20)     NOT NULL DEFAULT 'pending'
                            CHECK (indexing_status IN ('pending','processing','indexed','failed','skipped')),
    indexed_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE video_topics (
    video_id        INTEGER         NOT NULL REFERENCES videos(id)  ON DELETE CASCADE,
    topic_id        INTEGER         NOT NULL REFERENCES topics(id)  ON DELETE CASCADE,
    confidence      NUMERIC(3,2)    DEFAULT 0.50 CHECK (confidence BETWEEN 0 AND 1),
    PRIMARY KEY (video_id, topic_id)
);


-- =============================================================================
-- TRANSCRIPTS
-- =============================================================================

-- One row per (video × language × source) combination.
-- A video can have multiple transcripts: e.g. YouTube auto-EN + Whisper-AR.
CREATE TABLE transcripts (
    id              SERIAL PRIMARY KEY,
    video_id        INTEGER         NOT NULL REFERENCES videos(id)    ON DELETE CASCADE,
    language_id     INTEGER         NOT NULL REFERENCES languages(id) ON DELETE RESTRICT,
    source          VARCHAR(20)     NOT NULL
                        CHECK (source IN ('youtube_auto','youtube_manual','whisper')),
    full_text       TEXT,                   -- complete raw transcript (for display / re-chunking)
    confidence_score NUMERIC(3,2)          CHECK (confidence_score BETWEEN 0 AND 1),
    word_count      INTEGER,
    is_primary      BOOLEAN         NOT NULL DEFAULT FALSE,   -- main transcript for this video
    model_version   VARCHAR(50),            -- 'bge-small-en-v1.5', 'whisper-base', etc.
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (video_id, language_id, source)
);

-- Core search table. Every row is a searchable 30-second window.
-- Overlap: prev_text / next_text carry context from adjacent chunks so a sentence
-- split across a boundary still has full meaning in at least one chunk.
CREATE TABLE transcript_chunks (
    id              BIGSERIAL PRIMARY KEY,
    transcript_id   INTEGER         NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
    -- Denormalised from transcripts.video_id — avoids a join on every search query.
    video_id        INTEGER         NOT NULL REFERENCES videos(id)      ON DELETE CASCADE,
    chunk_index     INTEGER         NOT NULL,       -- 0-based position within transcript
    text            TEXT            NOT NULL,
    start_time      NUMERIC(10,3)   NOT NULL,       -- seconds from video start
    end_time        NUMERIC(10,3)   NOT NULL,
    -- 5-second overlap context stored alongside the chunk so the embedding captures
    -- the full sentence even when the boundary cuts through it.
    prev_text       TEXT,
    next_text       TEXT,
    embedding       vector(384)     NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (transcript_id, chunk_index)
);


-- =============================================================================
-- USERS
-- =============================================================================

CREATE TABLE subscription_plans (
    id                  SERIAL PRIMARY KEY,
    name                VARCHAR(50)     NOT NULL,
    slug                VARCHAR(50)     NOT NULL UNIQUE,
    price_monthly_usd   NUMERIC(8,2)    NOT NULL DEFAULT 0,
    searches_per_day    INTEGER         NOT NULL DEFAULT 10,    -- -1 = unlimited
    has_api_access      BOOLEAN         NOT NULL DEFAULT FALSE,
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE users (
    id                  BIGSERIAL PRIMARY KEY,
    email               VARCHAR(320)    NOT NULL UNIQUE,
    name                VARCHAR(255),
    avatar_url          VARCHAR(500),
    provider            VARCHAR(20)     NOT NULL DEFAULT 'email'
                            CHECK (provider IN ('email','google','github')),
    provider_id         VARCHAR(255),
    password_hash       VARCHAR(255),   -- NULL for OAuth users
    plan_id             INTEGER         NOT NULL REFERENCES subscription_plans(id),
    is_active           BOOLEAN         NOT NULL DEFAULT TRUE,
    email_verified_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_id)
);

CREATE TABLE user_sessions (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash  VARCHAR(64)     NOT NULL UNIQUE,   -- SHA-256 of token
    ip_address          INET,
    user_agent          TEXT,
    expires_at          TIMESTAMPTZ     NOT NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Auto-learned per-user interest in each industry (0 = not interested, 1 = very interested).
-- Updated by Python's ranking signal after every search_click event.
CREATE TABLE user_industries (
    user_id         BIGINT          NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    industry_id     INTEGER         NOT NULL REFERENCES industries(id) ON DELETE CASCADE,
    interest_score  NUMERIC(5,4)    NOT NULL DEFAULT 0.50 CHECK (interest_score BETWEEN 0 AND 1),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, industry_id)
);

CREATE TABLE user_topics (
    user_id         BIGINT          NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    topic_id        INTEGER         NOT NULL REFERENCES topics(id)  ON DELETE CASCADE,
    interest_score  NUMERIC(5,4)    NOT NULL DEFAULT 0.50 CHECK (interest_score BETWEEN 0 AND 1),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, topic_id)
);


-- =============================================================================
-- PAYMENTS
-- =============================================================================

CREATE TABLE user_subscriptions (
    id                      BIGSERIAL PRIMARY KEY,
    user_id                 BIGINT          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id                 INTEGER         NOT NULL REFERENCES subscription_plans(id),
    stripe_subscription_id  VARCHAR(255)    UNIQUE,
    stripe_customer_id      VARCHAR(255),
    status                  VARCHAR(20)     NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active','cancelled','past_due','trialing')),
    current_period_start    TIMESTAMPTZ,
    current_period_end      TIMESTAMPTZ,
    cancelled_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE payments (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subscription_id             BIGINT          REFERENCES user_subscriptions(id),
    stripe_payment_intent_id    VARCHAR(255)    UNIQUE,
    amount_cents                INTEGER         NOT NULL,
    currency                    CHAR(3)         NOT NULL DEFAULT 'usd',
    status                      VARCHAR(20)     NOT NULL
                                    CHECK (status IN ('pending','succeeded','failed','refunded')),
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE api_keys (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash        VARCHAR(64)     NOT NULL UNIQUE,   -- SHA-256 of the real key
    name            VARCHAR(100)    NOT NULL,
    last_used_at    TIMESTAMPTZ,
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);


-- =============================================================================
-- ANALYTICS
-- =============================================================================

CREATE TABLE search_history (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT          REFERENCES users(id)      ON DELETE SET NULL,
    query           TEXT            NOT NULL,
    -- Stored to power "searches similar to this" and personalisation signals.
    query_embedding vector(384),
    result_count    INTEGER         NOT NULL DEFAULT 0,
    source          VARCHAR(20)     NOT NULL DEFAULT 'indexed'
                        CHECK (source IN ('cache','indexed','hybrid')),
    industry_id     INTEGER         REFERENCES industries(id) ON DELETE SET NULL,
    latency_ms      INTEGER,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Tracks which result position the user actually clicked — the primary re-ranking signal.
CREATE TABLE search_clicks (
    id              BIGSERIAL PRIMARY KEY,
    search_id       BIGINT          NOT NULL REFERENCES search_history(id)     ON DELETE CASCADE,
    video_id        INTEGER         NOT NULL REFERENCES videos(id)             ON DELETE CASCADE,
    chunk_id        BIGINT          REFERENCES transcript_chunks(id)           ON DELETE SET NULL,
    position        SMALLINT        NOT NULL,    -- 1 = top result
    clicked_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE video_interactions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT          REFERENCES users(id)   ON DELETE SET NULL,
    video_id        INTEGER         NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    interaction_type VARCHAR(20)    NOT NULL
                        CHECK (interaction_type IN ('view','save','share','skip')),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE saved_videos (
    user_id         BIGINT          NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    video_id        INTEGER         NOT NULL REFERENCES videos(id)  ON DELETE CASCADE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, video_id)
);

-- Redis is the primary daily counter. This table is the durable backup (synced nightly).
CREATE TABLE daily_usage (
    user_id         BIGINT  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            DATE    NOT NULL,
    search_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, date)
);

-- Drives autocomplete and "trending" features.
CREATE TABLE popular_searches (
    id              BIGSERIAL PRIMARY KEY,
    query           VARCHAR(500)    NOT NULL UNIQUE,
    query_embedding vector(384),
    industry_id     INTEGER         REFERENCES industries(id) ON DELETE SET NULL,
    search_count    BIGINT          NOT NULL DEFAULT 1,
    last_searched_at TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);


-- =============================================================================
-- SYSTEM
-- =============================================================================

CREATE TABLE indexing_jobs (
    id              BIGSERIAL PRIMARY KEY,
    video_id        INTEGER         REFERENCES videos(id)    ON DELETE CASCADE,
    youtube_video_id VARCHAR(20),           -- kept before the videos row exists
    channel_id      INTEGER         REFERENCES channels(id)  ON DELETE SET NULL,
    industry_id     INTEGER         REFERENCES industries(id) ON DELETE SET NULL,
    status          VARCHAR(20)     NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','processing','done','failed','skipped')),
    priority        SMALLINT        NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
    attempts        SMALLINT        NOT NULL DEFAULT 0,
    max_attempts    SMALLINT        NOT NULL DEFAULT 3,
    error_message   TEXT,
    -- Kafka topic / cron name that triggered this job.
    queued_by       VARCHAR(50),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT          REFERENCES users(id) ON DELETE SET NULL,
    action          VARCHAR(100)    NOT NULL,
    entity_type     VARCHAR(50),
    entity_id       BIGINT,
    metadata        JSONB,
    ip_address      INET,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE gdpr_requests (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    request_type    VARCHAR(10)     NOT NULL CHECK (request_type IN ('export','delete')),
    status          VARCHAR(20)     NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','processing','done','failed')),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    processed_at    TIMESTAMPTZ
);


-- =============================================================================
-- INDEXES
-- =============================================================================

-- ── transcript_chunks ────────────────────────────────────────────────────────

-- Primary vector search index (global — Java Search Module, Phase 1 path).
-- HNSW outperforms IVFFlat for high-concurrency read workloads.
CREATE INDEX idx_chunks_embedding_hnsw
    ON transcript_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Full-text index for the keyword side of hybrid search.
CREATE INDEX idx_chunks_fts
    ON transcript_chunks
    USING gin (to_tsvector('english', text));

-- Fast lookup of all chunks belonging to a video (Stage 2 of industry search).
CREATE INDEX idx_chunks_video_id
    ON transcript_chunks (video_id);

-- ── videos ───────────────────────────────────────────────────────────────────

-- Stage 1 of the two-stage industry search: filter by industry, rank by video embedding.
CREATE INDEX idx_videos_industry_embedding_hnsw
    ON videos
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Planner can use this to narrow the HNSW scan to one industry without a full scan.
CREATE INDEX idx_videos_industry_status
    ON videos (primary_industry_id, indexing_status);

-- Crawler: find unprocessed videos quickly.
CREATE INDEX idx_videos_status
    ON videos (indexing_status)
    WHERE indexing_status IN ('pending', 'processing', 'failed');

-- ── indexing_jobs ─────────────────────────────────────────────────────────────

-- Worker queue pattern: fetch next pending jobs ordered by priority then age.
CREATE INDEX idx_jobs_queue
    ON indexing_jobs (status, priority, created_at)
    WHERE status IN ('pending', 'failed');

-- ── search_history ────────────────────────────────────────────────────────────

CREATE INDEX idx_search_history_user
    ON search_history (user_id, created_at DESC);

CREATE INDEX idx_search_history_created
    ON search_history (created_at DESC);

-- ── popular_searches ──────────────────────────────────────────────────────────

-- Trigram index for autocomplete prefix matching.
CREATE INDEX idx_popular_searches_trgm
    ON popular_searches
    USING gin (query gin_trgm_ops);

CREATE INDEX idx_popular_searches_count
    ON popular_searches (search_count DESC);

-- ── user_sessions ─────────────────────────────────────────────────────────────

CREATE INDEX idx_sessions_user
    ON user_sessions (user_id);

CREATE INDEX idx_sessions_expires
    ON user_sessions (expires_at)
    WHERE expires_at > NOW();

-- ── channels ──────────────────────────────────────────────────────────────────

CREATE INDEX idx_channels_monitored
    ON channels (is_monitored, last_crawled_at)
    WHERE is_monitored = TRUE;


-- =============================================================================
-- VIEWS
-- =============================================================================

-- Convenience view used by Java's Search Module to assemble a full result row
-- without writing the same join every time.
CREATE VIEW v_chunk_search_result AS
SELECT
    tc.id               AS chunk_id,
    tc.video_id,
    tc.chunk_index,
    tc.text,
    tc.start_time,
    tc.end_time,
    tc.prev_text,
    tc.next_text,
    tc.embedding,
    v.youtube_video_id,
    v.title,
    v.thumbnail_url,
    v.duration_seconds,
    v.view_count,
    v.quality_score     AS video_quality,
    v.published_at,
    v.primary_industry_id,
    c.name              AS channel_name,
    c.youtube_channel_id,
    c.quality_score     AS channel_quality,
    l.code              AS language_code
FROM transcript_chunks tc
JOIN videos      v  ON tc.video_id      = v.id
LEFT JOIN channels  c  ON v.channel_id      = c.id
LEFT JOIN languages l  ON v.language_id     = l.id;


-- =============================================================================
-- FUNCTIONS
-- =============================================================================

-- ---------------------------------------------------------------------------
-- search_by_industry
-- ---------------------------------------------------------------------------
-- Two-stage industry-scoped semantic search.
--
-- Stage 1: use videos.embedding to find the top `candidate_limit` videos
--          inside the requested industry.
-- Stage 2: within those video IDs, find the best-matching transcript chunks.
--
-- Called by Java Search Module when the user selects an industry filter,
-- or by the default search when the user's primary interest is known.
--
-- Parameters:
--   p_query_embedding  — 384-dim query vector from the Python Embedding Service
--   p_industry_id      — industries.id to scope the search
--   p_min_similarity   — minimum cosine similarity threshold (default 0.60)
--   p_candidate_limit  — how many videos to consider in Stage 1 (default 100)
--   p_result_limit     — final chunk results to return (default 20)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION search_by_industry(
    p_query_embedding   vector(384),
    p_industry_id       INTEGER,
    p_min_similarity    FLOAT   DEFAULT 0.60,
    p_candidate_limit   INTEGER DEFAULT 100,
    p_result_limit      INTEGER DEFAULT 20
)
RETURNS TABLE (
    chunk_id            BIGINT,
    video_id            INTEGER,
    youtube_video_id    VARCHAR(20),
    title               VARCHAR(500),
    thumbnail_url       VARCHAR(500),
    channel_name        VARCHAR(255),
    text                TEXT,
    start_time          NUMERIC(10,3),
    end_time            NUMERIC(10,3),
    view_count          BIGINT,
    quality_score       NUMERIC(3,2),
    published_at        TIMESTAMPTZ,
    similarity          FLOAT
)
LANGUAGE sql STABLE PARALLEL SAFE AS $$

    -- Stage 1: find the most relevant video IDs in this industry
    WITH candidate_videos AS (
        SELECT id
        FROM   videos
        WHERE  primary_industry_id = p_industry_id
          AND  indexing_status     = 'indexed'
          AND  embedding           IS NOT NULL
        ORDER BY embedding <=> p_query_embedding
        LIMIT  p_candidate_limit
    ),

    -- Stage 2: best matching chunk within those videos
    ranked_chunks AS (
        SELECT
            tc.id                                       AS chunk_id,
            tc.video_id,
            v.youtube_video_id,
            v.title,
            v.thumbnail_url,
            c.name                                      AS channel_name,
            tc.text,
            tc.start_time,
            tc.end_time,
            v.view_count,
            v.quality_score,
            v.published_at,
            1 - (tc.embedding <=> p_query_embedding)    AS similarity
        FROM  transcript_chunks  tc
        JOIN  videos             v  ON tc.video_id  = v.id
        LEFT JOIN channels       c  ON v.channel_id = c.id
        WHERE tc.video_id IN (SELECT id FROM candidate_videos)
          AND 1 - (tc.embedding <=> p_query_embedding) >= p_min_similarity
    )

    SELECT *
    FROM   ranked_chunks
    ORDER  BY similarity DESC
    LIMIT  p_result_limit;

$$;


-- ---------------------------------------------------------------------------
-- can_user_search
-- ---------------------------------------------------------------------------
-- Returns TRUE when the user is within their plan's daily search limit.
-- Java Search Module calls this before running any query.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION can_user_search(p_user_id BIGINT)
RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    SELECT
        CASE
            WHEN sp.searches_per_day = -1 THEN TRUE        -- unlimited plan
            ELSE COALESCE(du.search_count, 0) < sp.searches_per_day
        END
    FROM  users               u
    JOIN  subscription_plans  sp ON u.plan_id = sp.id
    LEFT JOIN daily_usage     du ON du.user_id = u.id AND du.date = CURRENT_DATE
    WHERE u.id = p_user_id;
$$;


-- ---------------------------------------------------------------------------
-- increment_daily_usage
-- ---------------------------------------------------------------------------
-- Upserts the daily_usage counter.  Redis is the primary counter; this is
-- called as a durable backup at the end of each request.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION increment_daily_usage(p_user_id BIGINT)
RETURNS VOID
LANGUAGE sql AS $$
    INSERT INTO daily_usage (user_id, date, search_count)
    VALUES (p_user_id, CURRENT_DATE, 1)
    ON CONFLICT (user_id, date)
    DO UPDATE SET search_count = daily_usage.search_count + 1;
$$;


-- ---------------------------------------------------------------------------
-- update_user_interest
-- ---------------------------------------------------------------------------
-- Adjusts the interest score for a user/industry pair after a click signal.
-- Uses exponential moving average: new = old * 0.9 + signal * 0.1
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_user_interest(
    p_user_id       BIGINT,
    p_industry_id   INTEGER,
    p_signal        FLOAT   -- 1.0 for a click, 0.0 for a skip
)
RETURNS VOID
LANGUAGE sql AS $$
    INSERT INTO user_industries (user_id, industry_id, interest_score, updated_at)
    VALUES (p_user_id, p_industry_id, p_signal, NOW())
    ON CONFLICT (user_id, industry_id)
    DO UPDATE SET
        interest_score = LEAST(1.0, GREATEST(0.0,
            user_industries.interest_score * 0.9 + p_signal * 0.1
        )),
        updated_at = NOW();
$$;


-- ---------------------------------------------------------------------------
-- update_video_embedding
-- ---------------------------------------------------------------------------
-- Called by the Python indexing worker after all chunks are embedded.
-- Computes the mean of chunk embeddings and stores it on the video row.
-- This populates videos.embedding which enables Stage 1 of industry search.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_video_embedding(p_video_id INTEGER)
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    v_avg vector(384);
BEGIN
    SELECT avg(embedding)
    INTO   v_avg
    FROM   transcript_chunks
    WHERE  video_id = p_video_id;

    UPDATE videos
    SET    embedding = v_avg,
           updated_at = NOW()
    WHERE  id = p_video_id;
END;
$$;


-- =============================================================================
-- SEED DATA
-- =============================================================================

-- ── Subscription plans ───────────────────────────────────────────────────────
INSERT INTO subscription_plans (name, slug, price_monthly_usd, searches_per_day, has_api_access)
VALUES
    ('Free',  'free',  0.00,  10,  FALSE),
    ('Pro',   'pro',   8.00,  -1,  FALSE),
    ('Team',  'team',  29.00, -1,  TRUE);

-- ── Languages ─────────────────────────────────────────────────────────────────
INSERT INTO languages (code, name, is_active)
VALUES
    ('en', 'English',            TRUE),
    ('hi', 'Hindi',              FALSE),
    ('es', 'Spanish',            FALSE),
    ('pt', 'Portuguese',         FALSE),
    ('ar', 'Arabic',             FALSE),
    ('fr', 'French',             FALSE),
    ('de', 'German',             FALSE),
    ('ja', 'Japanese',           FALSE),
    ('ko', 'Korean',             FALSE),
    ('zh', 'Chinese',            FALSE),
    ('id', 'Indonesian',         FALSE),
    ('tr', 'Turkish',            FALSE),
    ('ru', 'Russian',            FALSE),
    ('it', 'Italian',            FALSE);

-- ── Industries ────────────────────────────────────────────────────────────────
-- Embeddings are left NULL here; Python Classifier fills them at startup
-- by calling the Embedding Service with each industry's description.
INSERT INTO industries (name, slug, description, is_active)
VALUES
    ('Programming & Development', 'programming', 'Software development, coding tutorials, programming languages, web development, APIs, databases, DevOps, machine learning', TRUE),
    ('Business & Entrepreneurship', 'business',   'Marketing, sales, startups, entrepreneurship, finance, investment, e-commerce, productivity',                             FALSE),
    ('Education & Science',        'education',   'Academic subjects, science, mathematics, history, physics, biology, chemistry, university lectures',                      FALSE),
    ('Creative & Design',          'creative',    'Graphic design, video editing, music production, photography, animation, UI/UX, Blender, Adobe tools',                   FALSE),
    ('Fitness & Health',           'fitness',     'Workout routines, nutrition, yoga, gym training, health tips, sports performance',                                        FALSE);

-- ── Topics for Programming industry (id = 1) ─────────────────────────────────
INSERT INTO topics (industry_id, name, slug, keywords)
SELECT 1, t.name, t.slug, t.keywords
FROM (VALUES
    ('Python',           'python',         ARRAY['python','django','flask','fastapi','pandas','numpy','pytorch','tensorflow']),
    ('JavaScript',       'javascript',     ARRAY['javascript','js','nodejs','npm','typescript','es6']),
    ('React',            'react',          ARRAY['react','reactjs','jsx','hooks','redux','next.js','nextjs']),
    ('Web Development',  'web-dev',        ARRAY['html','css','bootstrap','tailwind','sass','web development']),
    ('DevOps',           'devops',         ARRAY['docker','kubernetes','ci/cd','jenkins','terraform','aws','devops']),
    ('Databases',        'databases',      ARRAY['sql','postgresql','mysql','mongodb','redis','database','orm']),
    ('Machine Learning', 'ml',             ARRAY['machine learning','deep learning','neural network','ai','nlp','computer vision']),
    ('Algorithms',       'algorithms',     ARRAY['algorithm','data structure','leetcode','sorting','graph','dynamic programming']),
    ('Java',             'java',           ARRAY['java','spring','springboot','jvm','maven','gradle']),
    ('Go',               'go',             ARRAY['golang','go','goroutine','concurrency']),
    ('Rust',             'rust',           ARRAY['rust','cargo','ownership','borrow checker']),
    ('Mobile Dev',       'mobile',         ARRAY['android','ios','flutter','react native','swift','kotlin','mobile']),
    ('System Design',    'system-design',  ARRAY['system design','architecture','microservices','scalability','caching']),
    ('Git & Version Control', 'git',       ARRAY['git','github','gitlab','version control','branching']),
    ('Linux & Shell',    'linux',          ARRAY['linux','bash','shell','terminal','command line','unix']),
    ('Security',         'security',       ARRAY['cybersecurity','hacking','penetration testing','encryption','oauth','jwt']),
    ('Cloud',            'cloud',          ARRAY['aws','gcp','azure','cloud computing','serverless','lambda']),
    ('Testing',          'testing',        ARRAY['unit test','integration test','pytest','jest','tdd','selenium']),
    ('API Design',       'api',            ARRAY['rest api','graphql','grpc','swagger','openapi','api design'])
) AS t(name, slug, keywords);