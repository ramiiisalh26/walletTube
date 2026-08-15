"""
YTSearch — FastAPI user-facing API (replaces Java Spring Boot on port 8080).

Start (from the python/ directory):
    uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload

Or via Docker:
    command: uvicorn api.main:app --host 0.0.0.0 --port 8080 --workers 4
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routers import analytics, auth, billing, clips, ingestion, search, user, video
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load cross-encoder at startup so the first search request isn't slow
    from api.modules.search.reranker import CrossEncoderReranker
    CrossEncoderReranker.get()  # loads model + runs warmup prediction
    yield
    # nothing to clean up — model lives in process memory


app = FastAPI(
    title="YTSearch API",
    description="YouTube AI semantic search — user-facing REST API.",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"chrome-extension://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Frontend ──────────────────────────────────────────────────────────────────
import pathlib as _pl
_frontend = _pl.Path(__file__).parent.parent / "frontend"
if _frontend.exists():
    app.mount("/static", StaticFiles(directory=str(_frontend)), name="static")

@app.get("/", include_in_schema=False)
async def frontend_root() -> FileResponse:
    return FileResponse(str(_frontend / "index.html"))

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(search.router)
app.include_router(video.router)
app.include_router(user.router)
app.include_router(billing.router)
app.include_router(ingestion.router)
app.include_router(analytics.router)
app.include_router(clips.router)

# ── Global exception handlers ─────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health() -> dict:
    from sqlalchemy import text
    from db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "version": "2.0.0",
    }
