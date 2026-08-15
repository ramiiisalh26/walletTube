"""
YTSearch Admin Dashboard
Run: streamlit run dashboard.py
"""
import asyncio
import os

import pandas as pd
import psycopg2
import psycopg2.extras
import streamlit as st

os.environ.setdefault("HF_HUB_OFFLINE", "1")

st.set_page_config(
    page_title="YTSearch Admin",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── DB helpers (sync psycopg2 — no asyncio loop conflicts with Streamlit) ─────

def _conn():
    from config import settings
    url = settings.database_url.replace("postgresql+asyncpg://", "")
    userpass, hostdb = url.split("@", 1)
    user, password = userpass.split(":", 1)
    hostport, dbname = hostdb.rsplit("/", 1)
    host, port = (hostport.split(":", 1) if ":" in hostport else (hostport, "5432"))
    return psycopg2.connect(host=host, port=int(port), dbname=dbname,
                            user=user, password=password)


def query(sql: str, params: dict | None = None) -> list[dict]:
    """Run a SELECT and return list of dicts. Use %(name)s placeholders."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or {})
            return [dict(r) for r in cur.fetchall()]


def execute(sql: str, params: dict | None = None) -> int:
    """Run INSERT/UPDATE/DELETE and return rowcount. Use %(name)s placeholders."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            rowcount = cur.rowcount
        conn.commit()
    return rowcount


def crawl_channel_sync(channel_id: str) -> dict:
    """
    Call crawl_channel (async) with a fresh engine each time so it never
    reuses connections from a stale asyncio event loop.
    """
    async def _run():
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from config import settings
        from api.services.ingestion_service import crawl_channel
        engine = create_async_engine(settings.database_url, pool_size=1, max_overflow=0)
        Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as session:
            result = await crawl_channel(session, channel_id)
        await engine.dispose()
        return result
    return asyncio.run(_run())


# ── Sidebar navigation ────────────────────────────────────────────────────────

st.sidebar.title("YTSearch Admin")
page = st.sidebar.radio(
    "Navigate",
    ["Overview", "Channels", "Videos", "Indexing Queue", "Industries"],
    index=0,
)
st.sidebar.divider()
if st.sidebar.button("Refresh data"):
    st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════

if page == "Overview":
    st.title("Overview")

    stats = query("""
        SELECT
            (SELECT COUNT(*) FROM videos)                                          AS total_videos,
            (SELECT COUNT(*) FROM videos WHERE indexing_status = 'indexed')        AS indexed_videos,
            (SELECT COUNT(*) FROM videos WHERE indexing_status = 'pending')        AS pending_videos,
            (SELECT COUNT(*) FROM videos WHERE indexing_status = 'failed')         AS failed_videos,
            (SELECT COUNT(*) FROM videos WHERE indexing_status = 'skipped')        AS skipped_videos,
            (SELECT COUNT(*) FROM channels)                                        AS total_channels,
            (SELECT COUNT(*) FROM channels WHERE is_monitored = TRUE)              AS monitored_channels,
            (SELECT COUNT(*) FROM transcript_chunks)                               AS total_chunks,
            (SELECT COUNT(*) FROM transcript_chunks WHERE embedding IS NOT NULL)   AS embedded_chunks,
            (SELECT COUNT(*) FROM transcripts)                                     AS total_transcripts,
            (SELECT COUNT(*) FROM indexing_jobs WHERE status = 'pending')          AS jobs_pending,
            (SELECT COUNT(*) FROM indexing_jobs WHERE status = 'failed')           AS jobs_failed
    """)[0]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Videos", f"{stats['total_videos']:,}")
    c2.metric("Indexed", f"{stats['indexed_videos']:,}")
    c3.metric("Pending", f"{stats['pending_videos']:,}")
    c4.metric("Failed", f"{stats['failed_videos']:,}")
    c5.metric("Skipped", f"{stats['skipped_videos']:,}")

    st.divider()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Channels", f"{stats['total_channels']:,}")
    c2.metric("Monitored", f"{stats['monitored_channels']:,}")
    c3.metric("Total Chunks", f"{stats['total_chunks']:,}")
    c4.metric("Embedded Chunks", f"{stats['embedded_chunks']:,}")
    embed_pct = (stats['embedded_chunks'] / max(stats['total_chunks'], 1)) * 100
    c5.metric("Embed %", f"{embed_pct:.1f}%")

    st.divider()

    c1, c2, c3 = st.columns(3)
    c1.metric("Transcripts", f"{stats['total_transcripts']:,}")
    c2.metric("Jobs Pending", f"{stats['jobs_pending']:,}")
    c3.metric("Jobs Failed", f"{stats['jobs_failed']:,}")

    st.divider()

    st.subheader("Videos by Industry")
    industry_data = query("""
        SELECT
            COALESCE(i.name, 'Unclassified') AS industry,
            COUNT(v.id)                       AS videos,
            SUM(CASE WHEN v.indexing_status = 'indexed' THEN 1 ELSE 0 END) AS indexed,
            SUM(CASE WHEN v.embedding IS NOT NULL THEN 1 ELSE 0 END)        AS has_embedding
        FROM videos v
        LEFT JOIN industries i ON i.id = v.primary_industry_id
        GROUP BY i.name
        ORDER BY videos DESC
    """)
    if industry_data:
        df = pd.DataFrame(industry_data)
        df["indexed %"] = (df["indexed"] / df["videos"] * 100).round(1).astype(str) + "%"
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Recent Indexing Activity (last 7 days)")
    activity = query("""
        SELECT DATE(indexed_at) AS day, COUNT(*) AS videos_indexed
        FROM videos
        WHERE indexed_at >= NOW() - INTERVAL '7 days'
        GROUP BY DATE(indexed_at)
        ORDER BY day
    """)
    if activity:
        df_act = pd.DataFrame(activity).set_index("day")
        st.bar_chart(df_act["videos_indexed"])
    else:
        st.info("No indexing activity in the last 7 days.")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: CHANNELS
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "Channels":
    st.title("Channels")

    with st.expander("Add New Channel", expanded=False):
        with st.form("add_channel_form"):
            handle = st.text_input(
                "Channel handle or ID",
                placeholder="@coreyms  or  UCxxxxxxxxxxxxxxxxxxxxxx",
            )
            submitted = st.form_submit_button("Add Channel & Queue Videos")

        if submitted and handle.strip():
            with st.spinner(f"Fetching {handle.strip()} from YouTube..."):
                try:
                    result = crawl_channel_sync(handle.strip())
                    if "error" in result:
                        st.error(result["error"])
                    else:
                        st.success(
                            f"**{result['channel_name']}** — "
                            f"{result['total_fetched']} videos fetched, "
                            f"**{result['queued']} queued** for indexing."
                        )
                except Exception as e:
                    st.error(f"Error: {e}")

    st.divider()

    channels = query("""
        SELECT
            ch.id,
            ch.name,
            ch.youtube_channel_id,
            ch.subscriber_count,
            ch.is_monitored,
            i.name                                                              AS industry,
            ch.last_crawled_at,
            COUNT(v.id)                                                         AS total_videos,
            SUM(CASE WHEN v.indexing_status = 'indexed' THEN 1 ELSE 0 END)     AS indexed,
            SUM(CASE WHEN v.indexing_status = 'pending' THEN 1 ELSE 0 END)     AS pending,
            SUM(CASE WHEN v.indexing_status = 'failed'  THEN 1 ELSE 0 END)     AS failed
        FROM channels ch
        LEFT JOIN videos v ON v.channel_id = ch.id
        LEFT JOIN industries i ON i.id = ch.primary_industry_id
        GROUP BY ch.id, ch.name, ch.youtube_channel_id, ch.subscriber_count,
                 ch.is_monitored, i.name, ch.last_crawled_at
        ORDER BY total_videos DESC
    """)

    if not channels:
        st.info("No channels in database yet.")
    else:
        col1, col2 = st.columns([2, 1])
        search = col1.text_input("Search channels", placeholder="Type channel name...")
        filter_monitored = col2.selectbox("Monitoring", ["All", "Monitored only", "Not monitored"])

        filtered = channels
        if search:
            filtered = [c for c in filtered if search.lower() in c["name"].lower()]
        if filter_monitored == "Monitored only":
            filtered = [c for c in filtered if c["is_monitored"]]
        elif filter_monitored == "Not monitored":
            filtered = [c for c in filtered if not c["is_monitored"]]

        st.markdown(f"**{len(filtered)} channels**")

        for ch in filtered:
            icon = "🟢" if ch["is_monitored"] else "⚫"
            total   = ch["total_videos"] or 0
            indexed = ch["indexed"] or 0
            pending = ch["pending"] or 0
            failed  = ch["failed"] or 0
            subs    = f"{ch['subscriber_count']:,}" if ch["subscriber_count"] else "—"
            crawled = str(ch["last_crawled_at"])[:10] if ch["last_crawled_at"] else "Never"

            with st.expander(
                f"{icon} **{ch['name']}** — {total:,} videos | "
                f"{indexed:,} indexed | {pending:,} pending | subs: {subs}"
            ):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Total", total)
                c2.metric("Indexed", indexed)
                c3.metric("Pending", pending)
                c4.metric("Failed", failed)
                st.caption(
                    f"ID: `{ch['youtube_channel_id']}` | "
                    f"Industry: {ch['industry'] or 'Unclassified'} | "
                    f"Last crawled: {crawled}"
                )

                b1, b2, b3 = st.columns(3)

                if ch["is_monitored"]:
                    if b1.button("Pause monitoring", key=f"pause_{ch['id']}"):
                        execute("UPDATE channels SET is_monitored = FALSE WHERE id = %(id)s",
                                {"id": ch["id"]})
                        st.rerun()
                else:
                    if b1.button("Enable monitoring", key=f"enable_{ch['id']}"):
                        execute("UPDATE channels SET is_monitored = TRUE WHERE id = %(id)s",
                                {"id": ch["id"]})
                        st.rerun()

                if b2.button("Re-crawl now", key=f"recrawl_{ch['id']}"):
                    with st.spinner("Crawling..."):
                        try:
                            r = crawl_channel_sync(ch["youtube_channel_id"])
                            st.success(f"Queued {r.get('queued', 0)} new videos.")
                        except Exception as e:
                            st.error(str(e))

                if b3.button("Delete channel", key=f"del_{ch['id']}", type="secondary"):
                    execute("DELETE FROM channels WHERE id = %(id)s", {"id": ch["id"]})
                    st.warning(f"Channel '{ch['name']}' deleted.")
                    st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: VIDEOS
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "Videos":
    st.title("Videos")

    col1, col2, col3 = st.columns(3)
    search        = col1.text_input("Search title", placeholder="keyword...")
    status_filter = col2.selectbox("Status", ["All", "indexed", "pending", "failed", "skipped", "processing"])
    trans_filter  = col3.selectbox("Transcript", ["All", "Has transcript", "No transcript"])

    col4, col5 = st.columns(2)
    channel_rows    = query("SELECT id, name FROM channels ORDER BY name")
    channel_options = {"All": None} | {c["name"]: c["id"] for c in channel_rows}
    channel_filter  = col4.selectbox("Channel", list(channel_options.keys()))
    embed_filter    = col5.selectbox("Embedding", ["All", "Embedded", "Not embedded"])

    where, params = ["1=1"], {}

    if search:
        where.append("v.title ILIKE %(search)s")
        params["search"] = f"%{search}%"
    if status_filter != "All":
        where.append("v.indexing_status = %(status)s")
        params["status"] = status_filter
    if trans_filter == "Has transcript":
        where.append("EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = v.id)")
    elif trans_filter == "No transcript":
        where.append("NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = v.id)")
    if channel_filter != "All":
        where.append("v.channel_id = %(channel_id)s")
        params["channel_id"] = channel_options[channel_filter]
    if embed_filter == "Embedded":
        where.append("v.embedding IS NOT NULL")
    elif embed_filter == "Not embedded":
        where.append("v.embedding IS NULL")

    where_sql = " AND ".join(where)

    total_count = query(f"SELECT COUNT(*) AS n FROM videos v WHERE {where_sql}", params)[0]["n"]
    st.markdown(f"**{total_count:,} videos** match filters")

    page_size = 50
    offset    = st.number_input("Page offset", min_value=0, step=page_size, value=0)

    videos = query(f"""
        SELECT
            v.id,
            v.youtube_video_id,
            v.title,
            ch.name                                                               AS channel,
            v.indexing_status,
            (v.embedding IS NOT NULL)                                             AS has_embedding,
            EXISTS (SELECT 1 FROM transcripts t WHERE t.video_id = v.id)         AS has_transcript,
            (SELECT COUNT(*) FROM transcript_chunks tc WHERE tc.video_id = v.id) AS chunk_count,
            (SELECT COUNT(*) FROM transcript_chunks tc
             WHERE tc.video_id = v.id AND tc.embedding IS NOT NULL)              AS embedded_chunks,
            v.published_at,
            v.duration_seconds,
            i.name                                                               AS industry
        FROM videos v
        LEFT JOIN channels ch ON ch.id = v.channel_id
        LEFT JOIN industries i ON i.id = v.primary_industry_id
        WHERE {where_sql}
        ORDER BY v.created_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """, {**params, "limit": page_size, "offset": int(offset)})

    if not videos:
        st.info("No videos match the current filters.")
    else:
        df = pd.DataFrame(videos)
        df["has_embedding"]  = df["has_embedding"].map({True: "✅", False: "❌"})
        df["has_transcript"] = df["has_transcript"].map({True: "✅", False: "❌"})
        df["duration"] = df["duration_seconds"].apply(
            lambda s: f"{int(s)//3600}h {(int(s)%3600)//60}m" if s and not pd.isna(s) else "—"
        )
        df["published"] = df["published_at"].astype(str).str[:10]
        df["chunks"]    = df["chunk_count"].astype(str) + " / " + df["embedded_chunks"].astype(str) + " emb"
        st.dataframe(
            df[["id", "title", "channel", "industry", "indexing_status",
                "has_transcript", "has_embedding", "chunks", "duration", "published"]],
            use_container_width=True, hide_index=True,
        )

        st.divider()
        st.subheader("Video Actions")
        action_id = st.number_input("Video ID", min_value=1, step=1)
        b1, b2, b3 = st.columns(3)

        if b1.button("Re-queue for indexing"):
            n = execute("""
                UPDATE indexing_jobs SET status = 'pending', attempts = 0, error_message = NULL
                WHERE video_id = %(vid)s AND status IN ('failed','skipped')
            """, {"vid": action_id})
            if n == 0:
                vrow = query("SELECT youtube_video_id, channel_id FROM videos WHERE id = %(id)s",
                             {"id": action_id})
                if vrow:
                    execute("""
                        INSERT INTO indexing_jobs (youtube_video_id, channel_id, priority, queued_by)
                        VALUES (%(yvid)s, %(cid)s, 8, 'dashboard')
                    """, {"yvid": vrow[0]["youtube_video_id"], "cid": vrow[0]["channel_id"]})
            st.success(f"Video {action_id} re-queued.")

        if b2.button("View transcript chunks"):
            chunks = query("""
                SELECT chunk_index, start_time, end_time,
                       (embedding IS NOT NULL) AS embedded,
                       LEFT(text, 120)         AS preview
                FROM transcript_chunks
                WHERE video_id = %(vid)s
                ORDER BY chunk_index
                LIMIT 100
            """, {"vid": action_id})
            if chunks:
                st.dataframe(pd.DataFrame(chunks), use_container_width=True, hide_index=True)
            else:
                st.info("No chunks found.")

        if b3.button("Delete video", type="secondary"):
            execute("DELETE FROM videos WHERE id = %(id)s", {"id": action_id})
            st.warning(f"Video {action_id} deleted.")
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: INDEXING QUEUE
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "Indexing Queue":
    st.title("Indexing Queue")

    counts = query("SELECT status, COUNT(*) AS n FROM indexing_jobs GROUP BY status ORDER BY n DESC")
    if counts:
        cols = st.columns(len(counts))
        for col, row in zip(cols, counts):
            col.metric(row["status"].capitalize(), f"{row['n']:,}")

    st.divider()
    tab1, tab2, tab3 = st.tabs(["Pending", "Failed / Skipped", "Recently Done"])

    with tab1:
        pending = query("""
            SELECT j.id, j.youtube_video_id, ch.name AS channel,
                   j.priority, j.attempts, j.queued_by, j.created_at
            FROM indexing_jobs j
            LEFT JOIN channels ch ON ch.id = j.channel_id
            WHERE j.status = 'pending'
            ORDER BY j.priority DESC, j.created_at ASC
            LIMIT 100
        """)
        if pending:
            st.dataframe(pd.DataFrame(pending), use_container_width=True, hide_index=True)
        else:
            st.success("No pending jobs.")

    with tab2:
        failed = query("""
            SELECT j.id, j.youtube_video_id, ch.name AS channel,
                   j.status, j.attempts, j.error_message, j.updated_at
            FROM indexing_jobs j
            LEFT JOIN channels ch ON ch.id = j.channel_id
            WHERE j.status IN ('failed','skipped')
            ORDER BY j.updated_at DESC
            LIMIT 100
        """)
        if failed:
            st.dataframe(pd.DataFrame(failed), use_container_width=True, hide_index=True)
            if st.button("Re-queue all failed jobs"):
                n = execute("""
                    UPDATE indexing_jobs SET status = 'pending', attempts = 0, error_message = NULL
                    WHERE status = 'failed'
                """)
                st.success(f"Re-queued {n} failed jobs.")
                st.rerun()
        else:
            st.success("No failed or skipped jobs.")

    with tab3:
        done = query("""
            SELECT j.id, j.youtube_video_id, ch.name AS channel, j.queued_by, j.updated_at
            FROM indexing_jobs j
            LEFT JOIN channels ch ON ch.id = j.channel_id
            WHERE j.status = 'done'
            ORDER BY j.updated_at DESC
            LIMIT 50
        """)
        if done:
            st.dataframe(pd.DataFrame(done), use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: INDUSTRIES
# ═══════════════════════════════════════════════════════════════════════════════

elif page == "Industries":
    st.title("Industries")

    industries = query("""
        SELECT
            i.id, i.name, i.slug, i.is_active,
            COUNT(DISTINCT ch.id)                                               AS channels,
            COUNT(DISTINCT v.id)                                                AS videos,
            SUM(CASE WHEN v.indexing_status = 'indexed' THEN 1 ELSE 0 END)     AS indexed,
            (SELECT COUNT(*) FROM transcript_chunks tc
             JOIN videos vv ON vv.id = tc.video_id
             WHERE vv.primary_industry_id = i.id
               AND tc.embedding IS NOT NULL)                                    AS embedded_chunks
        FROM industries i
        LEFT JOIN videos v  ON v.primary_industry_id  = i.id
        LEFT JOIN channels ch ON ch.primary_industry_id = i.id
        GROUP BY i.id, i.name, i.slug, i.is_active
        ORDER BY videos DESC
    """)

    for ind in industries:
        icon    = "🟢" if ind["is_active"] else "⚫"
        videos  = ind["videos"] or 0
        indexed = ind["indexed"] or 0
        chunks  = ind["embedded_chunks"] or 0

        with st.expander(
            f"{icon} **{ind['name']}** (`{ind['slug']}`) — "
            f"{videos:,} videos | {chunks:,} embedded chunks"
        ):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Channels", ind["channels"] or 0)
            c2.metric("Total Videos", videos)
            c3.metric("Indexed", indexed)
            c4.metric("Embedded Chunks", f"{chunks:,}")
            if videos > 0:
                st.progress(indexed / videos, text=f"{indexed/videos*100:.1f}% indexed")

    unclassified = query("SELECT COUNT(*) AS n FROM videos WHERE primary_industry_id IS NULL")[0]["n"]
    if unclassified > 0:
        st.warning(f"⚠️ {unclassified:,} videos have no industry assigned.")
        if st.button("Fix: assign all to Programming & Development"):
            n = execute("UPDATE videos SET primary_industry_id = 1 WHERE primary_industry_id IS NULL")
            st.success(f"Fixed {n} videos.")
            st.rerun()
