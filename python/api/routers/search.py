import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.dependencies import DbDep, OptionalUser, RedisDep
from api.schemas.search import SearchRequest, SearchResponse
from api.schemas.user import ClickRequest
from api.services import analytics_service, rag_service, search_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    session: DbDep,
    redis: RedisDep,
    user: OptionalUser,
) -> SearchResponse:
    user_id = user.id if user else None
    return await search_service.search(session, redis, req, user_id)


@router.get("/stream")
async def search_stream(
    session: DbDep,
    redis: RedisDep,
    user: OptionalUser,
    q: str,
    industry: str | None = None,
    page: int = 0,
    size: int = 10,
    session_id: str | None = None,
    referrer_clip_slug: str | None = None,
    generate_answer: bool = False,
) -> StreamingResponse:
    # generate_answer stays off on the SearchRequest: the non-streaming service
    # would block on the LLM before emitting a single result. Results go out
    # first, then the answer streams token-by-token below.
    req = SearchRequest(
        query=q, industry=industry, page=page, size=size,
        session_id=session_id, referrer_clip_slug=referrer_clip_slug,
    )
    user_id = user.id if user else None

    async def generate():
        try:
            response = await search_service.search(session, redis, req, user_id)
            meta = {
                "type": "meta",
                "query": response.query,
                "total": response.total_results,
                "detected_industry": response.detected_industry,
                "latency_ms": response.latency_ms,
                "indexing_more": response.indexing_more,
                "search_event_id": response.search_event_id,
                "answer_pending": generate_answer and rag_service.is_available(),
            }
            yield f"data: {json.dumps(meta)}\n\n"
            for result in response.results:
                yield f"data: {json.dumps({'type': 'result', **result.model_dump()})}\n\n"
                await asyncio.sleep(0.07)

            if generate_answer and rag_service.is_available():
                if await rag_service.consume_credit(redis, session_id):
                    async for kind, payload in rag_service.stream(q, response.results):
                        if kind == "token":
                            yield f"data: {json.dumps({'type': 'answer_token', 'text': payload})}\n\n"
                        elif kind == "done":
                            yield f"data: {json.dumps({'type': 'answer_done', **payload})}\n\n"
                        else:
                            yield f"data: {json.dumps({'type': 'answer_skipped', 'reason': payload})}\n\n"
                else:
                    skipped = {"type": "answer_skipped", "reason": rag_service.SKIP_LIMIT}
                    yield f"data: {json.dumps(skipped)}\n\n"

            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as exc:
            logger.exception("search_stream error")
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/click")
async def record_click(req: ClickRequest, session: DbDep, user: OptionalUser) -> dict:
    """Record a result click for re-ranking signal collection."""
    analytics_service.fire_and_forget(
        analytics_service.record_click(req.search_id, req.video_id, req.chunk_id, req.position)
    )
    if user:
        analytics_service.fire_and_forget(
            analytics_service.record_interaction(user.id, req.video_id, "view")
        )
    return {"ok": True}
