"""
Chrome-extension endpoints — per-video status, fast indexing, video-scoped search.

GET  /api/video/{yt_id}/status
GET  /api/video/{yt_id}/index/stream?title=&channel=
GET  /api/video/{yt_id}/search/stream?q=&generate_answer=
"""
import asyncio
import json
import logging

import numpy as np
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from api.dependencies import DbDep, OptionalUser, RedisDep
from api.schemas.search import SearchRequest, SearchResult
from api.services import embedding_client, rag_service, search_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/video", tags=["video"])

_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_VIDEO_CHUNK_SQL = """
SELECT
    v.youtube_video_id,
    v.title,
    v.thumbnail_url,
    ch.name          AS channel_name,
    tc.text,
    tc.start_time,
    tc.end_time,
    v.view_count,
    1 - (tc.embedding <=> CAST(:vec AS vector)) AS vector_score,
    pc.text          AS parent_text,
    pc.start_time    AS parent_start_time,
    pc.end_time      AS parent_end_time
FROM transcript_chunks tc
JOIN videos  v  ON tc.video_id = v.id
LEFT JOIN channels ch ON v.channel_id = ch.id
LEFT JOIN transcript_parent_chunks pc ON tc.parent_chunk_id = pc.id
WHERE v.youtube_video_id = :yt_id
  AND 1 - (tc.embedding <=> CAST(:vec AS vector)) >= :min_score
ORDER BY tc.embedding <=> CAST(:vec AS vector)
LIMIT :limit
"""

async def _flush_search_cache(redis) -> None:
    """Delete all search cache keys so a newly indexed video appears immediately."""
    try:
        keys = [k async for k in redis.scan_iter("cache:search:*")]
        if keys:
            await redis.delete(*keys)
            logger.info("Flushed %d search cache entries after indexing", len(keys))
    except Exception as exc:
        logger.warning("search cache flush failed: %s", exc)


# ── Extension classify helper ────────────────────────────────────────────────

async def _classify_extension_video(
    session,
    video_db_id: int,
    yt_id: str,
    title: str | None,
    channel_id: int | None,
) -> None:
    """
    Classify industry and assign nearest topic for extension-indexed videos.
    Mirrors IndexingPipeline._classify_video() + _assign_topic() but uses the
    gRPC embedding client (already running) instead of the local EmbeddingModel.

    Level 1 — inherit from channel.primary_industry_id (if set)
    Level 3 — keyword match on title
    Level 4 — embedding cosine similarity against industry vectors
    """
    from services.classifier.classifier import VideoClassifier

    if not title or title == yt_id:
        return

    try:
        # Level 1: inherit from channel
        channel_slug: str | None = None
        if channel_id:
            ch_row = (await session.execute(
                text("""
                    SELECT ind.slug FROM channels c
                    LEFT JOIN industries ind ON c.primary_industry_id = ind.id
                    WHERE c.id = :cid
                """),
                {"cid": channel_id},
            )).fetchone()
            if ch_row and ch_row[0]:
                channel_slug = ch_row[0]

        # Level 4 embeddings: load from DB
        ind_rows = (await session.execute(
            text("SELECT slug, embedding FROM industries WHERE embedding IS NOT NULL AND is_active = TRUE")
        )).fetchall()

        classifier = VideoClassifier()
        if ind_rows:
            classifier.set_industry_embeddings({r.slug: list(r.embedding) for r in ind_rows})

        result = classifier.classify(title=title, description="", channel_industry_slug=channel_slug)
        if not result.industry_slug:
            return

        ind_id_row = (await session.execute(
            text("SELECT id FROM industries WHERE slug = :s"),
            {"s": result.industry_slug},
        )).fetchone()
        if not ind_id_row:
            return

        industry_id = ind_id_row[0]
        await session.execute(
            text("UPDATE videos SET primary_industry_id = :ind WHERE id = :id AND primary_industry_id IS NULL"),
            {"ind": industry_id, "id": video_db_id},
        )

        # Assign nearest topic via gRPC embedding client
        vec = await embedding_client.embed_one(title)
        vec_str = "[" + ",".join(str(v) for v in vec) + "]"
        topic_row = (await session.execute(
            text("""
                SELECT id FROM topics
                WHERE industry_id = :ind_id AND is_active = TRUE AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT 1
            """),
            {"ind_id": industry_id, "vec": vec_str},
        )).fetchone()
        if topic_row:
            await session.execute(
                text("""
                    INSERT INTO video_topics (video_id, topic_id, confidence)
                    VALUES (:vid, :tid, 0.80)
                    ON CONFLICT DO NOTHING
                """),
                {"vid": video_db_id, "tid": topic_row[0]},
            )

        logger.info("Extension classify %s → %s (method=%s)", yt_id, result.industry_slug, result.method)

    except Exception as exc:
        logger.warning("Extension classify failed for %s: %s", yt_id, exc)


# ── status ────────────────────────────────────────────────────────────────────

@router.get("/{yt_id}/status")
async def video_status(yt_id: str, session: DbDep) -> dict:
    row = (await session.execute(
        text("""
            SELECT v.indexing_status,
                   v.title,
                   ch.name                 AS channel,
                   ch.youtube_channel_id   AS channel_yt_id,
                   COUNT(tc.id)            AS chunks,
                   ind.slug                AS industry
            FROM videos v
            LEFT JOIN channels           ch  ON v.channel_id          = ch.id
            LEFT JOIN transcript_chunks  tc  ON tc.video_id           = v.id
            LEFT JOIN industries         ind ON v.primary_industry_id = ind.id
            WHERE v.youtube_video_id = :yt_id
            GROUP BY v.indexing_status, v.title, ch.name,
                     ch.youtube_channel_id, ind.slug
        """),
        {"yt_id": yt_id},
    )).fetchone()

    # Check whether the channel is already in our DB.
    # Only mark channel_known=True when the video itself is in DB and has a channel FK —
    # we can't reliably match @handles to UC IDs without a YouTube API call, so for
    # unknown videos we always return channel_known=False to let the extension queue it.
    channel_known = False
    channel_video_count = 0
    if row and row.channel_yt_id:
        channel_known = True
        channel_video_count = (await session.execute(
            text("SELECT COUNT(*) FROM videos v JOIN channels ch ON ch.id = v.channel_id "
                 "WHERE ch.youtube_channel_id = :cid"),
            {"cid": row.channel_yt_id},
        )).scalar() or 0

    if row is None:
        return {
            "indexed": False, "status": "unknown",
            "channel_known": channel_known,
        }
    return {
        "indexed":             row.indexing_status == "indexed",
        "status":              row.indexing_status,
        "title":               row.title if row.title != yt_id else None,
        "channel":             row.channel,
        "chunks":              row.chunks,
        "industry":            row.industry,
        "channel_known":       channel_known,
        "channel_video_count": channel_video_count,
    }


# ── fast index ────────────────────────────────────────────────────────────────

@router.get("/{yt_id}/index/stream")
async def index_video_stream(
    yt_id: str,
    session: DbDep,
    redis: RedisDep,
    title: str | None = None,
    channel: str | None = None,
) -> StreamingResponse:

    async def generate():
        from db.models import Channel, Video, Transcript, TranscriptParentChunk
        from services.transcript.fetcher import fetch_transcript, TranscriptRateLimitError
        from services.transcript.chunker import chunk_transcript_with_parents

        def ev(data: dict) -> str:
            return f"data: {json.dumps(data)}\n\n"

        video_db_id: int | None = None
        committed = False

        async def _reset_status() -> None:
            """Reset a stuck video back to pending using a fresh connection."""
            if video_db_id is None or committed:
                return
            try:
                from db.session import engine as _engine
                async with _engine.connect() as conn:
                    await conn.execute(
                        text(
                            "UPDATE videos SET indexing_status = 'pending'"
                            " WHERE id = :id AND indexing_status = 'processing'"
                        ),
                        {"id": video_db_id},
                    )
                    await conn.commit()
            except Exception:
                pass

        try:
            yield ev({"type": "progress", "step": "prepare", "message": "Preparing video record…"})

            # ── Already indexed? ───────────────────────────────────────────────
            row = (await session.execute(
                text("SELECT id, indexing_status, channel_id FROM videos WHERE youtube_video_id = :id"),
                {"id": yt_id},
            )).fetchone()

            if row and row.indexing_status == "indexed":
                n = (await session.execute(
                    text("SELECT COUNT(*) FROM transcript_chunks WHERE video_id = :v"),
                    {"v": row.id},
                )).scalar()
                yield ev({"type": "already_indexed", "chunks": n})
                return

            # ── Prepare video row ──────────────────────────────────────────────
            video_channel_id: int | None = None
            thumb = f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg"

            # Prevent indefinite blocking if another transaction holds a lock on
            # this video row (e.g. pipeline worker or a previous dropped connection).
            await session.execute(text("SET LOCAL lock_timeout = '8s'"))

            try:
                if row is None:
                    channel_id = None
                    if channel:
                        ch_row = (await session.execute(
                            text("SELECT id FROM channels WHERE name = :n LIMIT 1"),
                            {"n": channel},
                        )).fetchone()
                        if ch_row:
                            channel_id = ch_row[0]
                        else:
                            ch = Channel(
                                youtube_channel_id=f"ext_{yt_id[:12]}",
                                name=channel,
                            )
                            session.add(ch)
                            await session.flush()
                            channel_id = ch.id

                    video = Video(
                        youtube_video_id=yt_id,
                        title=title or yt_id,
                        channel_id=channel_id,
                        thumbnail_url=thumb,
                        indexing_status="processing",
                    )
                    session.add(video)
                    await session.flush()
                    video_db_id = video.id
                    video_channel_id = channel_id
                else:
                    video_db_id = row.id
                    video_channel_id = row.channel_id
                    await session.execute(
                        text("UPDATE videos SET indexing_status='processing' WHERE id = :id"),
                        {"id": video_db_id},
                    )
            except Exception as lock_exc:
                # PostgreSQL lock_timeout raises error code 55P03
                err = str(lock_exc)
                if "55P03" in err or "lock_timeout" in err.lower() or "LockNotAvailable" in err:
                    yield ev({"type": "error", "message": "This video is currently being indexed by another process. Please wait a moment and try again."})
                else:
                    yield ev({"type": "error", "message": f"Failed to prepare video record: {lock_exc}"})
                return

            # ── Classify industry + assign topic (mirrors pipeline step 3) ─────
            yield ev({"type": "progress", "step": "classifying", "message": "Classifying video…"})
            await _classify_extension_video(session, video_db_id, yt_id, title, video_channel_id)

            # ── Fetch transcript ───────────────────────────────────────────────
            yield ev({"type": "progress", "step": "transcript", "message": "Fetching transcript…"})
            # heartbeats keep the SSE connection alive during the slow YouTube request
            fetch_task = asyncio.create_task(fetch_transcript(yt_id, fast=True))
            while not fetch_task.done():
                await asyncio.sleep(2)
                if not fetch_task.done():
                    yield ev({"type": "heartbeat"})
            try:
                result = fetch_task.result()
            except TranscriptRateLimitError:
                yield ev({"type": "error", "message": "YouTube rate limit — try again in a moment"})
                return

            if result is None:
                await session.execute(
                    text("UPDATE videos SET indexing_status='skipped' WHERE id = :id"),
                    {"id": video_db_id},
                )
                # Queue for Whisper retry — same as run_indexing.py does
                await session.execute(
                    text("""
                        INSERT INTO indexing_retry_jobs
                            (youtube_video_id, channel_id, reason, error_message,
                             retry_after, status)
                        VALUES
                            (:yvid, :cid, 'skipped', 'No transcript available',
                             NOW() + INTERVAL '1 hour', 'pending')
                        ON CONFLICT DO NOTHING
                    """),
                    {"yvid": yt_id, "cid": video_channel_id},
                )
                await session.commit()
                yield ev({"type": "error", "message": "No transcript available — queued for Whisper transcription (will be ready within a few hours)"})
                return

            # ── Chunk (two-level) ──────────────────────────────────────────────
            # Parents (~120 s, no overlap) provide search-time context.
            # Children (30–90 s, 5–8 s overlap) are embedded and searched.
            # Children are always smaller than parents (child_secs capped at 90).
            yield ev({"type": "progress", "step": "chunking", "message": "Splitting into chunks…"})
            n_segs = len(result.segments)
            chunk_secs, overlap_secs = (90, 8) if n_segs > 800 else (60, 8)
            parents, chunks = chunk_transcript_with_parents(
                result.segments,
                parent_seconds=120,
                child_seconds=chunk_secs,
                child_overlap=overlap_secs,
            )
            if not chunks:
                yield ev({"type": "error", "message": "Transcript produced no usable chunks"})
                return

            total = len(chunks)

            # ── Embed — single gRPC call, progress streamed per batch ─────────
            yield ev({"type": "progress", "step": "embedding",
                      "message": f"Embedding {total} chunks…"})
            TICK = 32  # report progress every N chunks
            all_vecs: list[list[float]] = []
            for i in range(0, total, TICK):
                batch = chunks[i: i + TICK]
                embed_texts = [
                    (c.prev_text[-80:] + " " + c.text if c.prev_text else c.text)
                    for c in batch
                ]
                vecs  = await embedding_client.embed_batch(embed_texts)
                all_vecs.extend(vecs)
                done = min(i + TICK, total)
                yield ev({"type": "progress", "step": "embedding",
                          "message": f"Embedding {done}/{total} chunks…"})
                yield ev({"type": "heartbeat"})

            # ── Resolve language row (default: English) ────────────────────────
            lang_code = result.language_code or "en"
            lang_row = (await session.execute(
                text("SELECT id FROM languages WHERE code = :c LIMIT 1"),
                {"c": lang_code},
            )).fetchone()
            if lang_row is None:
                lang_row = (await session.execute(
                    text("SELECT id FROM languages WHERE code = 'en' LIMIT 1"),
                )).fetchone()
            if lang_row is None:
                # Create English language row if missing
                await session.execute(
                    text("INSERT INTO languages (code, name, is_active) VALUES ('en','English',TRUE) ON CONFLICT DO NOTHING")
                )
                await session.flush()
                lang_row = (await session.execute(
                    text("SELECT id FROM languages WHERE code = 'en'"),
                )).fetchone()
            lang_id = lang_row[0]

            # ── Save to DB ─────────────────────────────────────────────────────
            yield ev({"type": "progress", "step": "saving", "message": "Saving to database…"})
            full_text = " ".join(c.text for c in chunks)
            transcript = Transcript(
                video_id=video_db_id,
                language_id=lang_id,
                source=result.source,
                full_text=full_text,
                word_count=len(full_text.split()),
                is_primary=True,
                model_version="BAAI/bge-base-en-v1.5",
            )
            session.add(transcript)
            await session.flush()  # get transcript.id

            # ── Insert parent chunks; CTE returns IDs ordered by chunk_index ──
            parent_id_rows = (await session.execute(
                text("""
                    WITH ins AS (
                        INSERT INTO transcript_parent_chunks
                            (transcript_id, video_id, chunk_index, text,
                             start_time, end_time, word_count)
                        SELECT :tid, :vid, t.idx, t.txt, t.t0, t.t1, t.wc
                        FROM unnest(
                            CAST(:idxs AS int[]),
                            CAST(:txts AS text[]),
                            CAST(:t0s  AS float[]),
                            CAST(:t1s  AS float[]),
                            CAST(:wcs  AS int[])
                        ) AS t(idx, txt, t0, t1, wc)
                        RETURNING id, chunk_index
                    )
                    SELECT id, chunk_index FROM ins ORDER BY chunk_index
                """),
                {
                    "tid":  transcript.id,
                    "vid":  video_db_id,
                    "idxs": [p.index      for p in parents],
                    "txts": [p.text       for p in parents],
                    "t0s":  [p.start_time for p in parents],
                    "t1s":  [p.end_time   for p in parents],
                    "wcs":  [p.word_count for p in parents],
                },
            )).fetchall()
            parent_id_map = {row.chunk_index: row.id for row in parent_id_rows}

            # ── Insert child chunks (bulk unnest with parent FK) ───────────────
            vec_strs = ["[" + ",".join(str(v) for v in vec) + "]" for vec in all_vecs]
            await session.execute(
                text("""
                    INSERT INTO transcript_chunks
                        (transcript_id, video_id, chunk_index, text,
                         start_time, end_time, prev_text, next_text,
                         embedding, parent_chunk_id)
                    SELECT
                        :tid, :vid, t.idx, t.txt,
                        t.t0, t.t1, t.prev, t.nxt,
                        CAST(t.vec AS vector), t.pid
                    FROM unnest(
                        CAST(:idxs  AS int[]),
                        CAST(:txts  AS text[]),
                        CAST(:t0s   AS float[]),
                        CAST(:t1s   AS float[]),
                        CAST(:prevs AS text[]),
                        CAST(:nxts  AS text[]),
                        CAST(:vecs  AS text[]),
                        CAST(:pids  AS bigint[])
                    ) AS t(idx, txt, t0, t1, prev, nxt, vec, pid)
                """),
                {
                    "tid":   transcript.id,
                    "vid":   video_db_id,
                    "idxs":  [c.index                          for c in chunks],
                    "txts":  [c.text                           for c in chunks],
                    "t0s":   [c.start_time                     for c in chunks],
                    "t1s":   [c.end_time                       for c in chunks],
                    "prevs": [c.prev_text or ""                for c in chunks],
                    "nxts":  [c.next_text  or ""               for c in chunks],
                    "vecs":  vec_strs,
                    "pids":  [parent_id_map.get(c.parent_index) for c in chunks],
                },
            )

            arr     = np.array(all_vecs, dtype=np.float32)
            mean    = arr.mean(axis=0)
            norm    = np.linalg.norm(mean)
            vid_vec = (mean / norm if norm > 0 else mean).tolist()
            vec_str = "[" + ",".join(str(v) for v in vid_vec) + "]"

            await session.execute(
                text("""
                    UPDATE videos
                    SET indexing_status = 'indexed',
                        embedding       = CAST(:vec AS vector),
                        indexed_at      = NOW(),
                        title           = CASE WHEN title = :yt_id THEN :new_title ELSE title END,
                        thumbnail_url   = COALESCE(thumbnail_url, :thumb)
                    WHERE id = :id
                """),
                {"vec": vec_str, "yt_id": yt_id,
                 "new_title": title or yt_id, "thumb": thumb, "id": video_db_id},
            )
            await session.commit()
            committed = True
            await _flush_search_cache(redis)
            yield ev({"type": "done", "chunks": total})

        except asyncio.CancelledError:
            # Client disconnected mid-stream — reset so the next attempt isn't blocked.
            await _reset_status()
            raise

        except Exception as exc:
            logger.exception("fast-index error for %s", yt_id)
            await _reset_status()
            yield ev({"type": "error", "message": str(exc)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── video-scoped search ───────────────────────────────────────────────────────

@router.get("/{yt_id}/search/stream")
async def video_search_stream(
    yt_id: str,
    session: DbDep,
    redis: RedisDep,
    user: OptionalUser,
    q: str,
    min_score: float = 0.55,
    limit: int = 8,
    generate_answer: bool = False,
    session_id: str | None = None,
) -> StreamingResponse:
    user_id = user.id if user else None
    want_answer = generate_answer and rag_service.is_available()

    async def generate():
        def ev(data: dict) -> str:
            return f"data: {json.dumps(data)}\n\n"

        # Everything actually shown to the user, in rank order — this is the
        # context the answer is grounded in, so it can never cite a result the
        # user isn't looking at.
        shown: list[SearchResult] = []

        try:
            vector = await embedding_client.embed_one(_BGE_QUERY_PREFIX + q)
            vec_str = "[" + ",".join(str(v) for v in vector) + "]"

            # ── Search within this video ───────────────────────────────────────
            rows = (await session.execute(
                text(_VIDEO_CHUNK_SQL),
                {"vec": vec_str, "yt_id": yt_id,
                 "min_score": min_score, "limit": limit},
            )).mappings().all()

            video_hits = []
            for row in rows:
                vid_id = row["youtube_video_id"]
                t_sec  = int(float(row["start_time"]))
                context = None
                if row["parent_text"]:
                    context = {
                        "text":       row["parent_text"],
                        "start_time": float(row["parent_start_time"]),
                        "end_time":   float(row["parent_end_time"]),
                    }
                video_hits.append({
                    "source":        "video",
                    "video_id":      vid_id,
                    "title":         row["title"],
                    "thumbnail_url": row["thumbnail_url"],
                    "channel_name":  row["channel_name"],
                    "text":          row["text"],
                    "start_time":    float(row["start_time"]),
                    "end_time":      float(row["end_time"]),
                    "view_count":    int(row["view_count"] or 0),
                    "similarity":    float(row["vector_score"]),
                    "youtube_url":   f"https://www.youtube.com/watch?v={vid_id}&t={t_sec}",
                    "embed_url":     f"https://www.youtube.com/embed/{vid_id}?start={t_sec}&autoplay=1",
                    "context":       context,
                })

            yield ev({"type": "meta",
                      "video_hits": len(video_hits),
                      "has_fallback": len(video_hits) == 0,
                      "answer_pending": want_answer})

            for r in video_hits:
                yield ev({"type": "result", **r})
                shown.append(SearchResult(**{k: v for k, v in r.items() if k != "source"}))
                await asyncio.sleep(0.06)

            # ── Global fallback — only when this video has zero results ────────
            # Do NOT fall back just because scores are < 0.60 or there is only
            # one result: any hit above min_score means the topic IS in this
            # video and showing other-video results would be wrong/confusing.
            if not video_hits:
                yield ev({"type": "progress", "message": "No matches in this video — searching all indexed videos…"})
                global_resp = await search_service.search(
                    session, redis, SearchRequest(query=q), user_id
                )
                for r in global_resp.results:
                    if r.video_id == yt_id:
                        continue
                    yield ev({"type": "result", "source": "global", **r.model_dump()})
                    shown.append(r)
                    await asyncio.sleep(0.06)

            # ── RAG answer ────────────────────────────────────────────────────
            # Streams after the cards so results are readable immediately; the
            # answer fills in above them. Soft-fails — a broken LLM call never
            # costs the user their search results.
            if want_answer:
                if await rag_service.consume_credit(redis, session_id):
                    async for kind, payload in rag_service.stream(q, shown):
                        if kind == "token":
                            yield ev({"type": "answer_token", "text": payload})
                        elif kind == "done":
                            yield ev({"type": "answer_done", **payload})
                        else:
                            yield ev({"type": "answer_skipped", "reason": payload})
                else:
                    yield ev({"type": "answer_skipped", "reason": rag_service.SKIP_LIMIT})

            yield ev({"type": "done"})

        except Exception as exc:
            logger.exception("video_search_stream error")
            yield ev({"type": "error", "message": str(exc)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
