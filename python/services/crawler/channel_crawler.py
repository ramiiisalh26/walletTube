"""
Discovers video IDs from YouTube using the cheapest API methods:

  Primary  — Playlist API  (1 quota unit / 50 videos) via channel uploads playlist
  Secondary — Search API   (100 quota units / request) for discovering new channels
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings

logger = logging.getLogger(__name__)

# Set to True when any API call hits quotaExceeded — prevents hammering the API
# with calls that will all fail for the rest of the day. Cleared on worker restart.
_quota_exhausted: bool = False


def _check_quota(exc: HttpError) -> bool:
    """Returns True and sets the flag if this error is a quota exhaustion."""
    global _quota_exhausted
    if "quotaExceeded" in str(exc):
        if not _quota_exhausted:
            _quota_exhausted = True
            logger.warning(
                "YouTube Data API quota exhausted — all API calls disabled for this session. "
                "Quota resets at midnight Pacific."
            )
        return True
    return False


@dataclass
class VideoMeta:
    youtube_video_id: str
    title: str
    description: str
    thumbnail_url: str | None
    duration_seconds: int | None
    view_count: int
    published_at: str | None   # ISO 8601 string
    yt_category_id: str | None
    tags: list[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


@dataclass
class ChannelMeta:
    youtube_channel_id: str
    name: str
    description: str
    thumbnail_url: str | None
    subscriber_count: int
    uploads_playlist_id: str


class YouTubeCrawler:
    def __init__(self) -> None:
        self._yt = build("youtube", "v3", developerKey=settings.youtube_api_key)

    # ── Channel discovery ─────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def get_channel_meta(self, channel_id: str) -> ChannelMeta | None:
        """
        Fetch metadata + uploads playlist ID for a channel. Costs 1 quota unit.
        Accepts either a channel ID (UCxxxxxx) or a handle (@channelname).
        """
        if _quota_exhausted:
            return None
        # Handles start with @; everything else is treated as a channel ID
        if channel_id.startswith("@"):
            lookup = {"forHandle": channel_id}
        else:
            lookup = {"id": channel_id}
        try:
            resp = self._yt.channels().list(
                part="snippet,contentDetails,statistics",
                maxResults=1,
                **lookup,
            ).execute()
        except HttpError as exc:
            if _check_quota(exc):
                return None
            logger.error("channels.list failed for %s: %s", channel_id, exc)
            return None

        items = resp.get("items", [])
        if not items:
            return None

        item = items[0]
        snippet = item["snippet"]
        stats = item.get("statistics", {})
        uploads_playlist = item["contentDetails"]["relatedPlaylists"]["uploads"]

        return ChannelMeta(
            youtube_channel_id=item["id"],   # always the real UCxxx ID from the API
            name=snippet["title"],
            description=snippet.get("description", ""),
            thumbnail_url=snippet.get("thumbnails", {}).get("default", {}).get("url"),
            subscriber_count=int(stats.get("subscriberCount", 0)),
            uploads_playlist_id=uploads_playlist,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def discover_channels_by_topic(self, query: str, max_results: int = 25) -> list[str]:
        """
        Search for channels matching a topic query.
        Costs 100 quota units — use sparingly (weekly cron).
        Returns a list of youtube_channel_ids.
        """
        if _quota_exhausted:
            return []
        try:
            resp = self._yt.search().list(
                part="snippet",
                q=query,
                type="channel",
                maxResults=max_results,
                relevanceLanguage="en",
            ).execute()
        except HttpError as exc:
            if _check_quota(exc):
                return []
            logger.error("channels search failed for '%s': %s", query, exc)
            return []

        return [item["snippet"]["channelId"] for item in resp.get("items", [])]

    # ── Video ID collection ───────────────────────────────────────────────────

    def get_channel_video_ids(
        self,
        uploads_playlist_id: str,
        since: datetime | None = None,
    ) -> list[str]:
        """
        Retrieve video IDs from a channel's uploads playlist.
        Cost: 1 quota unit per 50 videos.

        since=None  → full history (first crawl)
        since=<dt>  → only videos published after that timestamp (incremental crawl)

        YouTube returns items newest-first, so we stop paginating as soon as we
        encounter a video older than `since` — no wasted quota.
        """
        # Ensure since is timezone-aware for comparison
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

        video_ids: list[str] = []
        next_page_token: str | None = None
        done = False

        if _quota_exhausted:
            return []

        while not done:
            try:
                resp = self._yt.playlistItems().list(
                    part="contentDetails",
                    playlistId=uploads_playlist_id,
                    maxResults=50,
                    pageToken=next_page_token,
                ).execute()
            except HttpError as exc:
                if _check_quota(exc):
                    break
                logger.error("playlistItems.list failed for %s: %s", uploads_playlist_id, exc)
                break

            for item in resp.get("items", []):
                details = item["contentDetails"]
                vid = details.get("videoId")
                if not vid:
                    continue

                if since is not None:
                    published_str = details.get("videoPublishedAt", "")
                    if published_str:
                        published_at = datetime.fromisoformat(
                            published_str.replace("Z", "+00:00")
                        )
                        if published_at <= since:
                            # Everything from here on is older — stop
                            done = True
                            break

                video_ids.append(vid)

            next_page_token = resp.get("nextPageToken")
            if not next_page_token:
                break

        logger.info(
            "Collected %d video IDs from playlist %s%s",
            len(video_ids),
            uploads_playlist_id,
            f" (since {since.isoformat()})" if since else " (full history)",
        )
        return video_ids

    def get_videos_meta(self, video_ids: list[str]) -> list[VideoMeta]:
        """
        Fetch metadata for up to 50 videos in a single request.
        Cost: 1 quota unit per request (pass up to 50 IDs at once).
        """
        if not video_ids:
            return []

        results: list[VideoMeta] = []

        # API allows max 50 per call
        for batch_start in range(0, len(video_ids), 50):
            batch = video_ids[batch_start : batch_start + 50]
            try:
                resp = self._yt.videos().list(
                    part="snippet,contentDetails,statistics",
                    id=",".join(batch),
                ).execute()
            except HttpError as exc:
                if "quotaExceeded" in str(exc):
                    raise  # let caller set the quota-exhausted flag
                logger.error("videos.list failed: %s", exc)
                continue

            for item in resp.get("items", []):
                snippet = item["snippet"]
                stats = item.get("statistics", {})

                duration_str = item.get("contentDetails", {}).get("duration", "")
                duration_sec = _parse_iso8601_duration(duration_str)

                results.append(VideoMeta(
                    youtube_video_id=item["id"],
                    title=snippet["title"],
                    description=snippet.get("description", ""),
                    thumbnail_url=snippet.get("thumbnails", {}).get("medium", {}).get("url"),
                    duration_seconds=duration_sec,
                    view_count=int(stats.get("viewCount", 0)),
                    published_at=snippet.get("publishedAt"),
                    yt_category_id=snippet.get("categoryId"),
                    tags=snippet.get("tags", []),
                ))

        return results

    def get_trending_video_ids(self, region_code: str = "US", max_results: int = 50) -> list[str]:
        """
        Fetch trending videos for a region.
        Cost: 1 quota unit per request.
        Called by the hourly cron job.
        """
        if _quota_exhausted:
            return []
        try:
            resp = self._yt.videos().list(
                part="id",
                chart="mostPopular",
                regionCode=region_code,
                maxResults=max_results,
            ).execute()
        except HttpError as exc:
            if _check_quota(exc):
                return []
            logger.error("trending fetch failed for %s: %s", region_code, exc)
            return []

        return [item["id"] for item in resp.get("items", [])]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_iso8601_duration(duration: str) -> int | None:
    """Convert ISO 8601 duration (PT1H2M3S) to total seconds."""
    import re
    if not duration:
        return None
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return None
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds