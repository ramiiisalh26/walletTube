"""
SQLAlchemy ORM models — all tables.
Python owns schema migrations (Alembic); the FastAPI layer reads/writes here.
"""
from datetime import datetime, date
from decimal import Decimal
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, ForeignKey,
    Integer, Numeric, SmallInteger, String, Text, ARRAY,
    TIMESTAMP, UniqueConstraint, CHAR,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.session import Base


# =============================================================================
# CONFIGURATION
# =============================================================================

class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id: Mapped[int]                     = mapped_column(Integer, primary_key=True)
    name: Mapped[str]                   = mapped_column(String(50), nullable=False)
    slug: Mapped[str]                   = mapped_column(String(50), nullable=False, unique=True)
    price_monthly_usd: Mapped[Decimal]  = mapped_column(Numeric(8, 2), nullable=False, default=0)
    searches_per_day: Mapped[int]       = mapped_column(Integer, nullable=False, default=10)
    has_api_access: Mapped[bool]        = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool]             = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime]        = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="plan")


class Industry(Base):
    __tablename__ = "industries"

    id: Mapped[int]                     = mapped_column(Integer, primary_key=True)
    name: Mapped[str]                   = mapped_column(String(100), nullable=False)
    slug: Mapped[str]                   = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[Optional[str]]  = mapped_column(Text)
    embedding: Mapped[Optional[list]]   = mapped_column(Vector(768))
    is_active: Mapped[bool]             = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime]        = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime]        = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    topics: Mapped[list["Topic"]] = relationship(back_populates="industry")


class Language(Base):
    __tablename__ = "languages"

    id: Mapped[int]             = mapped_column(Integer, primary_key=True)
    code: Mapped[str]           = mapped_column(String(10), nullable=False, unique=True)
    name: Mapped[str]           = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool]     = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int]             = mapped_column(Integer, primary_key=True)
    industry_id: Mapped[int]    = mapped_column(Integer, ForeignKey("industries.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str]           = mapped_column(String(150), nullable=False)
    slug: Mapped[str]           = mapped_column(String(150), nullable=False)
    keywords: Mapped[list]      = mapped_column(ARRAY(Text), nullable=False, default=list)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(768))
    is_active: Mapped[bool]     = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    industry: Mapped["Industry"] = relationship(back_populates="topics")

    __table_args__ = (UniqueConstraint("industry_id", "slug"),)


# =============================================================================
# USERS
# =============================================================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[int]                         = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str]                      = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[Optional[str]]             = mapped_column(String(255))
    avatar_url: Mapped[Optional[str]]       = mapped_column(String(500))
    provider: Mapped[str]                   = mapped_column(String(20), nullable=False, default="email")
    provider_id: Mapped[Optional[str]]      = mapped_column(String(255))
    password_hash: Mapped[Optional[str]]    = mapped_column(String(255))
    plan_id: Mapped[int]                    = mapped_column(Integer, ForeignKey("subscription_plans.id"), nullable=False)
    is_active: Mapped[bool]                 = mapped_column(Boolean, nullable=False, default=True)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    plan: Mapped["SubscriptionPlan"]            = relationship(back_populates="users")
    sessions: Mapped[list["UserSession"]]       = relationship(back_populates="user", cascade="all, delete-orphan")
    subscriptions: Mapped[list["UserSubscription"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    search_history: Mapped[list["SearchHistory"]]   = relationship(back_populates="user")
    saved_videos: Mapped[list["SavedVideo"]]         = relationship(back_populates="user", cascade="all, delete-orphan")
    daily_usages: Mapped[list["DailyUsage"]]         = relationship(back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("provider IN ('email','google','github')"),
        UniqueConstraint("provider", "provider_id"),
    )


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int]                         = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int]                    = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    refresh_token_hash: Mapped[str]         = mapped_column(String(64), nullable=False, unique=True)
    ip_address: Mapped[Optional[str]]       = mapped_column(String(45))
    user_agent: Mapped[Optional[str]]       = mapped_column(Text)
    expires_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="sessions")


class UserIndustry(Base):
    __tablename__ = "user_industries"

    user_id: Mapped[int]        = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    industry_id: Mapped[int]    = mapped_column(Integer, ForeignKey("industries.id", ondelete="CASCADE"), primary_key=True)
    interest_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0.50)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class UserTopic(Base):
    __tablename__ = "user_topics"

    user_id: Mapped[int]        = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    topic_id: Mapped[int]       = mapped_column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True)
    interest_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0.50)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)


# =============================================================================
# PAYMENTS
# =============================================================================

class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id: Mapped[int]                             = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int]                        = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id: Mapped[int]                        = mapped_column(Integer, ForeignKey("subscription_plans.id"), nullable=False)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    stripe_customer_id: Mapped[Optional[str]]   = mapped_column(String(255))
    status: Mapped[str]                         = mapped_column(String(20), nullable=False, default="active")
    current_period_start: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    current_period_end: Mapped[Optional[datetime]]   = mapped_column(TIMESTAMP(timezone=True))
    cancelled_at: Mapped[Optional[datetime]]    = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime]                = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime]                = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="subscriptions")

    __table_args__ = (
        CheckConstraint("status IN ('active','cancelled','past_due','trialing')"),
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int]                             = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int]                        = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subscription_id: Mapped[Optional[int]]      = mapped_column(BigInteger, ForeignKey("user_subscriptions.id"))
    stripe_payment_intent_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    amount_cents: Mapped[int]                   = mapped_column(Integer, nullable=False)
    currency: Mapped[str]                       = mapped_column(CHAR(3), nullable=False, default="usd")
    status: Mapped[str]                         = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime]                = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("status IN ('pending','succeeded','failed','refunded')"),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int]                     = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int]                = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key_hash: Mapped[str]               = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str]                   = mapped_column(String(100), nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    is_active: Mapped[bool]             = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime]        = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)


# =============================================================================
# CONTENT
# =============================================================================

class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int]                         = mapped_column(Integer, primary_key=True)
    youtube_channel_id: Mapped[str]         = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str]                       = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]]      = mapped_column(Text)
    thumbnail_url: Mapped[Optional[str]]    = mapped_column(String(500))
    subscriber_count: Mapped[Optional[int]] = mapped_column(BigInteger, default=0)
    primary_industry_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("industries.id", ondelete="SET NULL"))
    language_id: Mapped[Optional[int]]      = mapped_column(Integer, ForeignKey("languages.id", ondelete="SET NULL"))
    is_monitored: Mapped[bool]              = mapped_column(Boolean, nullable=False, default=False)
    quality_score: Mapped[Optional[float]]  = mapped_column(Numeric(3, 2), default=0.50)
    last_crawled_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    videos: Mapped[list["Video"]] = relationship(back_populates="channel")


class ChannelIndustry(Base):
    __tablename__ = "channel_industries"

    channel_id: Mapped[int]     = mapped_column(Integer, ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True)
    industry_id: Mapped[int]    = mapped_column(Integer, ForeignKey("industries.id", ondelete="CASCADE"), primary_key=True)
    is_primary: Mapped[bool]    = mapped_column(Boolean, nullable=False, default=False)


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int]                         = mapped_column(Integer, primary_key=True)
    youtube_video_id: Mapped[str]           = mapped_column(String(20), nullable=False, unique=True)
    channel_id: Mapped[Optional[int]]       = mapped_column(Integer, ForeignKey("channels.id", ondelete="SET NULL"))
    title: Mapped[str]                      = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]]      = mapped_column(Text)
    thumbnail_url: Mapped[Optional[str]]    = mapped_column(String(500))
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    view_count: Mapped[Optional[int]]       = mapped_column(BigInteger, default=0)
    published_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    tags: Mapped[list]                      = mapped_column(ARRAY(Text), nullable=False, default=list)
    yt_category_id: Mapped[Optional[str]]   = mapped_column(String(10))
    primary_industry_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("industries.id", ondelete="SET NULL"))
    language_id: Mapped[Optional[int]]      = mapped_column(Integer, ForeignKey("languages.id", ondelete="SET NULL"))
    quality_score: Mapped[Optional[float]]  = mapped_column(Numeric(3, 2), default=0.50)
    embedding: Mapped[Optional[list]]       = mapped_column(Vector(768))
    indexing_status: Mapped[str]            = mapped_column(String(20), nullable=False, default="pending")
    indexed_at: Mapped[Optional[datetime]]  = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    channel: Mapped[Optional["Channel"]]    = relationship(back_populates="videos")
    transcripts: Mapped[list["Transcript"]] = relationship(back_populates="video", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("indexing_status IN ('pending','processing','indexed','failed','skipped')"),
    )


class VideoTopic(Base):
    __tablename__ = "video_topics"

    video_id: Mapped[int]   = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True)
    topic_id: Mapped[int]   = mapped_column(Integer, ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(3, 2), default=0.50)


# =============================================================================
# TRANSCRIPTS
# =============================================================================

class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[int]                         = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int]                   = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    language_id: Mapped[int]                = mapped_column(Integer, ForeignKey("languages.id", ondelete="RESTRICT"), nullable=False)
    source: Mapped[str]                     = mapped_column(String(20), nullable=False)
    full_text: Mapped[Optional[str]]        = mapped_column(Text)
    confidence_score: Mapped[Optional[float]] = mapped_column(Numeric(3, 2))
    word_count: Mapped[Optional[int]]       = mapped_column(Integer)
    is_primary: Mapped[bool]                = mapped_column(Boolean, nullable=False, default=False)
    model_version: Mapped[Optional[str]]    = mapped_column(String(50))
    created_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    video: Mapped["Video"]                                  = relationship(back_populates="transcripts")
    chunks: Mapped[list["TranscriptChunk"]]                 = relationship(back_populates="transcript", cascade="all, delete-orphan")
    parent_chunks: Mapped[list["TranscriptParentChunk"]]    = relationship(back_populates="transcript", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("video_id", "language_id", "source"),
        CheckConstraint("source IN ('youtube_auto','youtube_manual','whisper')"),
    )


class TranscriptParentChunk(Base):
    """Wide context spans (~2 min). Text-only — no embedding column."""
    __tablename__ = "transcript_parent_chunks"

    id: Mapped[int]             = mapped_column(BigInteger, primary_key=True)
    transcript_id: Mapped[int]  = mapped_column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False, index=True)
    video_id: Mapped[int]       = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index: Mapped[int]    = mapped_column(Integer, nullable=False)
    text: Mapped[str]           = mapped_column(Text, nullable=False)
    start_time: Mapped[float]   = mapped_column(Numeric(10, 3), nullable=False)
    end_time: Mapped[float]     = mapped_column(Numeric(10, 3), nullable=False)
    word_count: Mapped[int]     = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    transcript: Mapped["Transcript"]             = relationship(back_populates="parent_chunks")
    children: Mapped[list["TranscriptChunk"]]    = relationship(back_populates="parent_chunk")

    __table_args__ = (UniqueConstraint("transcript_id", "chunk_index"),)


class TranscriptChunk(Base):
    __tablename__ = "transcript_chunks"

    id: Mapped[int]             = mapped_column(BigInteger, primary_key=True)
    transcript_id: Mapped[int]  = mapped_column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)
    video_id: Mapped[int]       = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int]    = mapped_column(Integer, nullable=False)
    text: Mapped[str]           = mapped_column(Text, nullable=False)
    start_time: Mapped[float]   = mapped_column(Numeric(10, 3), nullable=False)
    end_time: Mapped[float]     = mapped_column(Numeric(10, 3), nullable=False)
    prev_text: Mapped[Optional[str]] = mapped_column(Text)
    next_text: Mapped[Optional[str]] = mapped_column(Text)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(768))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    parent_chunk_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("transcript_parent_chunks.id", ondelete="SET NULL"), nullable=True, index=True)

    transcript: Mapped["Transcript"]                        = relationship(back_populates="chunks")
    parent_chunk: Mapped[Optional["TranscriptParentChunk"]] = relationship(back_populates="children")

    __table_args__ = (UniqueConstraint("transcript_id", "chunk_index"),)


# =============================================================================
# ANALYTICS
# =============================================================================

class SearchHistory(Base):
    __tablename__ = "search_history"

    id: Mapped[int]                         = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[Optional[int]]          = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    session_id: Mapped[Optional[str]]       = mapped_column(Text, index=True)
    query: Mapped[str]                      = mapped_column(Text, nullable=False)
    query_embedding: Mapped[Optional[list]] = mapped_column(Vector(768))
    result_count: Mapped[int]               = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str]                     = mapped_column(String(20), nullable=False, default="indexed")
    industry_id: Mapped[Optional[int]]      = mapped_column(Integer, ForeignKey("industries.id", ondelete="SET NULL"))
    latency_ms: Mapped[Optional[int]]       = mapped_column(Integer)
    top_score: Mapped[Optional[float]]      = mapped_column(Numeric(7, 4))
    referrer_clip_slug: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, index=True)

    user: Mapped[Optional["User"]] = relationship(back_populates="search_history")

    __table_args__ = (
        CheckConstraint("source IN ('cache','indexed','hybrid')"),
    )


class SearchClicks(Base):
    __tablename__ = "search_clicks"

    id: Mapped[int]                       = mapped_column(BigInteger, primary_key=True)
    search_id: Mapped[int]                = mapped_column(BigInteger, ForeignKey("search_history.id", ondelete="CASCADE"), nullable=False, index=True)
    video_id: Mapped[int]                 = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[Optional[int]]       = mapped_column(BigInteger, ForeignKey("transcript_chunks.id", ondelete="SET NULL"))
    position: Mapped[int]                 = mapped_column(SmallInteger, nullable=False)
    user_id: Mapped[Optional[int]]        = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    session_id: Mapped[Optional[str]]     = mapped_column(Text)
    clicked_timestamp: Mapped[Optional[float]] = mapped_column(Numeric(10, 3))
    clicked_at: Mapped[datetime]          = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class VideoInteraction(Base):
    __tablename__ = "video_interactions"

    id: Mapped[int]                     = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[Optional[int]]      = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    video_id: Mapped[int]               = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    interaction_type: Mapped[str]       = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime]        = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("interaction_type IN ('view','save','share','skip')"),
    )


class SavedVideo(Base):
    __tablename__ = "saved_videos"

    user_id: Mapped[int]    = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    video_id: Mapped[int]   = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    user: Mapped["User"]    = relationship(back_populates="saved_videos")
    video: Mapped["Video"]  = relationship()


class DailyUsage(Base):
    __tablename__ = "daily_usage"

    user_id: Mapped[int]        = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[date]          = mapped_column(Date, primary_key=True)
    search_count: Mapped[int]   = mapped_column(Integer, nullable=False, default=0)

    user: Mapped["User"] = relationship(back_populates="daily_usages")


class PopularSearch(Base):
    __tablename__ = "popular_searches"

    id: Mapped[int]                     = mapped_column(BigInteger, primary_key=True)
    query: Mapped[str]                  = mapped_column(String(500), nullable=False, unique=True)
    query_embedding: Mapped[Optional[list]] = mapped_column(Vector(768))
    industry_id: Mapped[Optional[int]]  = mapped_column(Integer, ForeignKey("industries.id", ondelete="SET NULL"))
    search_count: Mapped[int]           = mapped_column(BigInteger, nullable=False, default=1)
    last_searched_at: Mapped[datetime]  = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class SharedClip(Base):
    __tablename__ = "shared_clips"

    id: Mapped[int]                          = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str]                        = mapped_column(Text, nullable=False, unique=True, index=True)
    video_id: Mapped[int]                    = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True)
    start_time: Mapped[float]                = mapped_column(Numeric(10, 3), nullable=False)
    end_time: Mapped[float]                  = mapped_column(Numeric(10, 3), nullable=False)
    transcript_text: Mapped[str]             = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    view_count: Mapped[int]                  = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime]             = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, index=True)

    video: Mapped["Video"]              = relationship()
    created_by: Mapped[Optional["User"]] = relationship()


# =============================================================================
# SYSTEM
# =============================================================================

class IndexingJob(Base):
    __tablename__ = "indexing_jobs"

    id: Mapped[int]                         = mapped_column(BigInteger, primary_key=True)
    video_id: Mapped[Optional[int]]         = mapped_column(Integer, ForeignKey("videos.id", ondelete="CASCADE"))
    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(20))
    channel_id: Mapped[Optional[int]]       = mapped_column(Integer, ForeignKey("channels.id", ondelete="SET NULL"))
    industry_id: Mapped[Optional[int]]      = mapped_column(Integer, ForeignKey("industries.id", ondelete="SET NULL"))
    status: Mapped[str]                     = mapped_column(String(20), nullable=False, default="pending")
    priority: Mapped[int]                   = mapped_column(SmallInteger, nullable=False, default=5)
    attempts: Mapped[int]                   = mapped_column(SmallInteger, nullable=False, default=0)
    max_attempts: Mapped[int]               = mapped_column(SmallInteger, nullable=False, default=3)
    error_message: Mapped[Optional[str]]    = mapped_column(Text)
    queued_by: Mapped[Optional[str]]        = mapped_column(String(50))
    created_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("status IN ('pending','processing','done','failed','skipped')"),
        CheckConstraint("priority BETWEEN 1 AND 10"),
    )


class IndexingRetryJob(Base):
    __tablename__ = "indexing_retry_jobs"

    id: Mapped[int]                         = mapped_column(BigInteger, primary_key=True)
    original_job_id: Mapped[Optional[int]]  = mapped_column(BigInteger, ForeignKey("indexing_jobs.id", ondelete="SET NULL"))
    youtube_video_id: Mapped[str]           = mapped_column(String(20), nullable=False)
    channel_id: Mapped[Optional[int]]       = mapped_column(Integer, ForeignKey("channels.id", ondelete="SET NULL"))
    reason: Mapped[str]                     = mapped_column(String(10), nullable=False)   # 'failed' | 'skipped'
    error_message: Mapped[Optional[str]]    = mapped_column(Text)
    attempts: Mapped[int]                   = mapped_column(SmallInteger, nullable=False, default=0)
    max_attempts: Mapped[int]               = mapped_column(SmallInteger, nullable=False, default=3)
    retry_after: Mapped[datetime]           = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    retry_exhausted: Mapped[bool]           = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str]                     = mapped_column(String(30), nullable=False, default="pending")
    created_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime]            = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("reason IN ('failed','skipped')"),
        CheckConstraint("status IN ('pending','done','permanently_failed')"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int]                     = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[Optional[int]]      = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str]                 = mapped_column(String(100), nullable=False)
    entity_type: Mapped[Optional[str]]  = mapped_column(String(50))
    entity_id: Mapped[Optional[int]]    = mapped_column(BigInteger)
    ip_address: Mapped[Optional[str]]   = mapped_column(String(45))
    created_at: Mapped[datetime]        = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class GdprRequest(Base):
    __tablename__ = "gdpr_requests"

    id: Mapped[int]             = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int]        = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    request_type: Mapped[str]   = mapped_column(String(10), nullable=False)
    status: Mapped[str]         = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint("request_type IN ('export','delete')"),
        CheckConstraint("status IN ('pending','processing','done','failed')"),
    )
