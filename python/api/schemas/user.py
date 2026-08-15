from datetime import datetime
from pydantic import BaseModel


class PlanInfo(BaseModel):
    id: int
    name: str
    slug: str
    searches_per_day: int
    has_api_access: bool


class UserProfile(BaseModel):
    id: int
    email: str
    name: str | None
    avatar_url: str | None
    plan: PlanInfo
    created_at: datetime


class SearchHistoryItem(BaseModel):
    id: int
    query: str
    result_count: int
    source: str
    industry_id: int | None
    latency_ms: int | None
    created_at: datetime


class PaginatedHistory(BaseModel):
    items: list[SearchHistoryItem]
    total: int
    page: int
    size: int


class ClickRequest(BaseModel):
    search_id: int
    video_id: int
    chunk_id: int | None = None
    position: int
