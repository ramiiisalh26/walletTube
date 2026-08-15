from pydantic import BaseModel, Field


class CreateClipRequest(BaseModel):
    youtube_video_id: str = Field(..., min_length=1, max_length=20)
    start_time: float = Field(..., ge=0)
    end_time: float
    transcript_text: str = Field(..., min_length=1)
    session_id: str | None = None


class CreateClipResponse(BaseModel):
    slug: str
    share_url: str
    view_count: int = 0


class ClipData(BaseModel):
    slug: str
    youtube_video_id: str
    title: str
    channel_name: str | None
    thumbnail_url: str | None
    embed_url: str
    transcript_text: str
    start_time: float
    end_time: float
    view_count: int
