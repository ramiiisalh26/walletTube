"""
FastAPI app — internal/admin endpoints only.
The user-facing search API lives in the Java side.

Start:
    uvicorn main:app --host 0.0.0.0 --port 8001 --reload
"""
import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text

from config import settings
from db.models import IndexingJob, Video
from db.session import AsyncSessionLocal
from services.indexing.worker import process_video

logger = logging.getLogger(__name__)

app = FastAPI(
    title="YTSearch — Python Ingestion Service",
    description="Internal API for indexing administration. Not user-facing.",
    version="1.0.0",
)


# =============================================================================
# Health
# =============================================================================

@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Used by Docker / load balancer health checks."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "embedding_model": settings.embedding_model_name,
    }


# =============================================================================
# Queue stats
# =============================================================================

@app.get("/admin/queue/stats", tags=["admin"])
async def queue_stats() -> dict:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(IndexingJob.status, func.count().label("count"))
            .group_by(IndexingJob.status)
        )
        rows = result.fetchall()

    return {"queue": {row.status: row.count for row in rows}}


@app.get("/admin/index/stats", tags=["admin"])
async def index_stats() -> dict:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Video.indexing_status, func.count().label("count"))
            .group_by(Video.indexing_status)
        )
        rows = result.fetchall()

        total = await session.execute(select(func.count()).select_from(Video))

    return {
        "total_videos": total.scalar(),
        "by_status": {row.indexing_status: row.count for row in rows},
    }


# =============================================================================
# Manual indexing triggers (admin use)
# =============================================================================

class IndexVideoRequest(BaseModel):
    youtube_video_id: str
    priority: int = 3


@app.post("/admin/index/video", tags=["admin"])
async def index_video(req: IndexVideoRequest) -> dict:
    """Manually trigger indexing of a single video."""
    if not (1 <= req.priority <= 10):
        raise HTTPException(status_code=400, detail="priority must be between 1 and 10")

    process_video.apply_async(
        kwargs={
            "youtube_video_id": req.youtube_video_id,
            "priority": req.priority,
            "queued_by": "admin_api",
        }
    )
    return {"queued": req.youtube_video_id}


class ReindexFailedRequest(BaseModel):
    limit: int = 100


@app.post("/admin/index/retry-failed", tags=["admin"])
async def retry_failed(req: ReindexFailedRequest) -> dict:
    """Reset failed jobs back to 'pending' so workers pick them up again."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                UPDATE indexing_jobs
                SET status = 'pending', attempts = 0, error_message = NULL
                WHERE status = 'failed' AND attempts < max_attempts
                LIMIT :lim
                RETURNING id
            """),
            {"lim": req.limit},
        )
        ids = [row[0] for row in result.fetchall()]
        await session.commit()

    return {"reset_count": len(ids)}


# =============================================================================
# Embedding probe (sanity check)
# =============================================================================

class EmbedRequest(BaseModel):
    text: str


@app.post("/admin/embed/test", tags=["admin"])
async def embed_test(req: EmbedRequest) -> dict:
    """Check that the embedding model is working correctly."""
    from services.embedding.model import EmbeddingModel
    model = EmbeddingModel.get()
    vector = model.encode_one(req.text)
    return {"dimensions": len(vector), "sample": vector[:5]}