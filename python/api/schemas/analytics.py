from pydantic import BaseModel, Field


class ClickEventRequest(BaseModel):
    search_event_id: int
    video_id: str           # youtube_video_id string (matches SearchResult.video_id)
    result_position: int = Field(..., ge=1)
    clicked_timestamp: float
    session_id: str | None = None
