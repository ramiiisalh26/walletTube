import html as _html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from api.dependencies import DbDep, OptionalUser
from api.schemas.clips import ClipData, CreateClipRequest, CreateClipResponse
from api.services import analytics_service, clips_service

router = APIRouter(tags=["clips"])


# ── REST API endpoints ────────────────────────────────────────────────────────

@router.post("/api/v1/clips", response_model=CreateClipResponse)
async def create_clip(
    req: CreateClipRequest,
    session: DbDep,
    request: Request,
    user: OptionalUser,
) -> CreateClipResponse:
    """Create a shareable clip snapshot. No auth required."""
    user_id = user.id if user else None
    slug, share_url = await clips_service.create_clip(
        session,
        req.youtube_video_id,
        req.start_time,
        req.end_time,
        req.transcript_text,
        user_id,
        request,
    )
    return CreateClipResponse(slug=slug, share_url=share_url)


@router.get("/api/v1/clips/{slug}", response_model=ClipData)
async def get_clip_data(slug: str, session: DbDep) -> ClipData:
    """Return clip data as JSON (for programmatic / extension use). Increments view_count."""
    data = await clips_service.get_clip(session, slug)
    analytics_service.fire_and_forget(clips_service.increment_view_count(session, slug))
    return ClipData(**data)


# ── Public clip page (HTML) ───────────────────────────────────────────────────

@router.get("/clips/{slug}", response_class=HTMLResponse, include_in_schema=False)
async def clip_page(slug: str, session: DbDep) -> HTMLResponse:
    """Public no-auth clip page with OG meta tags and search CTA."""
    data = await clips_service.get_clip(session, slug)
    analytics_service.fire_and_forget(clips_service.increment_view_count(session, slug))
    return HTMLResponse(_render_clip_page(data))


# ── HTML renderer ─────────────────────────────────────────────────────────────

def _fmt_time(secs: float) -> str:
    s = int(secs)
    h, remainder = divmod(s, 3600)
    m, sec = divmod(remainder, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _render_clip_page(data: dict) -> str:
    e = _html.escape  # HTML-escape helper

    title       = data["title"]
    channel     = data["channel_name"] or "Unknown Channel"
    transcript  = data["transcript_text"]
    snippet     = transcript[:200] + ("…" if len(transcript) > 200 else "")
    thumbnail   = data["thumbnail_url"] or ""
    embed_url   = data["embed_url"]
    slug        = data["slug"]
    ts_range    = f"{_fmt_time(data['start_time'])} – {_fmt_time(data['end_time'])}"
    view_count  = data["view_count"]
    yt_id       = data["youtube_video_id"]
    search_q    = transcript[:120].replace('"', "'")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{e(title)} — YTSearch Clip</title>

  <!-- Open Graph -->
  <meta property="og:type"        content="video.other">
  <meta property="og:site_name"   content="YTSearch">
  <meta property="og:title"       content="{e(title)} — clip at {e(ts_range)}">
  <meta property="og:description" content="{e(snippet)}">
  <meta property="og:image"       content="{e(thumbnail)}">
  <meta property="og:url"         content="/clips/{e(slug)}">

  <!-- Twitter Card -->
  <meta name="twitter:card"        content="summary_large_image">
  <meta name="twitter:title"       content="{e(title)} — clip at {e(ts_range)}">
  <meta name="twitter:description" content="{e(snippet)}">
  <meta name="twitter:image"       content="{e(thumbnail)}">

  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', system-ui, sans-serif;
      background: #09090b;
      color: #f4f4f5;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
    }}
    .wrap {{ width: 100%; max-width: 760px; padding: 28px 16px 60px; }}
    /* header */
    .brand {{
      display: flex; align-items: center; gap: 10px;
      margin-bottom: 28px;
    }}
    .brand-logo {{
      width: 32px; height: 32px;
      background: linear-gradient(135deg, #8b5cf6, #ec4899);
      border-radius: 9px;
      display: grid; place-items: center;
      font-size: 16px; flex-shrink: 0;
    }}
    .brand-name {{ font-weight: 700; font-size: 18px; letter-spacing: -.02em; }}
    /* video embed */
    .embed-wrap {{
      width: 100%; aspect-ratio: 16/9;
      background: #000;
      border-radius: 12px;
      overflow: hidden;
      margin-bottom: 20px;
      box-shadow: 0 0 40px rgba(0,0,0,.6);
    }}
    .embed-wrap iframe {{ width: 100%; height: 100%; border: none; display: block; }}
    /* meta */
    .video-title {{
      font-size: 20px; font-weight: 700; line-height: 1.3;
      margin-bottom: 6px; letter-spacing: -.01em;
    }}
    .video-meta {{
      font-size: 13px; color: #71717a;
      display: flex; align-items: center; gap: 8px;
      margin-bottom: 20px; flex-wrap: wrap;
    }}
    .badge {{
      display: inline-flex; align-items: center; gap: 4px;
      padding: 3px 9px; border-radius: 99px; font-size: 12px; font-weight: 600;
    }}
    .badge-time {{
      background: rgba(139,92,246,.12); color: #a78bfa;
      border: 1px solid rgba(139,92,246,.25);
    }}
    .badge-views {{
      background: rgba(255,255,255,.05); color: #a1a1aa;
      border: 1px solid rgba(255,255,255,.1);
    }}
    /* transcript */
    .transcript-card {{
      background: #17171a;
      border: 1px solid rgba(255,255,255,.08);
      border-left: 3px solid #8b5cf6;
      border-radius: 0 10px 10px 0;
      padding: 14px 16px;
      margin-bottom: 28px;
      font-size: 14px; line-height: 1.75; color: #d4d4d8;
    }}
    /* CTA */
    .cta-card {{
      background: linear-gradient(135deg, rgba(139,92,246,.15), rgba(236,72,153,.1));
      border: 1px solid rgba(139,92,246,.3);
      border-radius: 14px;
      padding: 24px;
      text-align: center;
    }}
    .cta-card h2 {{ font-size: 18px; font-weight: 700; margin-bottom: 8px; }}
    .cta-card p {{ font-size: 14px; color: #a1a1aa; margin-bottom: 18px; line-height: 1.6; }}
    .cta-form {{ display: flex; gap: 8px; max-width: 480px; margin: 0 auto; }}
    .cta-input {{
      flex: 1;
      padding: 10px 14px;
      background: rgba(0,0,0,.4);
      border: 1px solid rgba(255,255,255,.15);
      border-radius: 9px;
      color: #f4f4f5;
      font: 14px/1.4 inherit;
      outline: none;
    }}
    .cta-input:focus {{ border-color: rgba(139,92,246,.6); }}
    .cta-btn {{
      padding: 10px 20px;
      background: #8b5cf6;
      border: none; border-radius: 9px;
      color: #fff; font: 14px/1 inherit; font-weight: 600;
      cursor: pointer; white-space: nowrap;
      transition: background .15s;
    }}
    .cta-btn:hover {{ background: #7c3aed; }}
    .yt-link {{
      display: inline-block; margin-top: 14px;
      font-size: 13px; color: #71717a; text-decoration: none;
    }}
    .yt-link:hover {{ color: #a1a1aa; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="brand">
      <div class="brand-logo">▶</div>
      <span class="brand-name">YTSearch</span>
    </div>

    <div class="embed-wrap">
      <iframe
        src="{e(embed_url)}"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowfullscreen
        loading="eager">
      </iframe>
    </div>

    <h1 class="video-title">{e(title)}</h1>
    <div class="video-meta">
      <span>{e(channel)}</span>
      <span>·</span>
      <span class="badge badge-time">⏱ {e(ts_range)}</span>
      <span class="badge badge-views">👁 {view_count:,} views</span>
    </div>

    <div class="transcript-card">{e(transcript)}</div>

    <div class="cta-card">
      <h2>Find more moments like this</h2>
      <p>YTSearch lets you search across thousands of videos by meaning, not just keywords.</p>
      <div class="cta-form">
        <input
          id="cta-q"
          class="cta-input"
          type="text"
          placeholder="Search any topic…"
          value="{e(search_q)}"
        >
        <button class="cta-btn" onclick="doSearch()">Search</button>
      </div>
      <a class="yt-link" href="https://www.youtube.com/watch?v={e(yt_id)}&t={int(data['start_time'])}" target="_blank" rel="noopener">
        ▶ Watch on YouTube
      </a>
    </div>
  </div>

  <script>
    function doSearch() {{
      const q = document.getElementById('cta-q').value.trim();
      if (!q) return;
      const params = new URLSearchParams({{
        q,
        from_clip: '{e(slug)}',
      }});
      window.location.href = '/?' + params.toString();
    }}
    document.getElementById('cta-q').addEventListener('keydown', function(e) {{
      if (e.key === 'Enter') doSearch();
    }});
  </script>
</body>
</html>"""
