"""
Kafka consumer — listens for events published by the Java side and routes them
to the correct handler.

Topics consumed:
  search.miss              → immediately index the missed video(s)
  user.video.watched       → queue video for background indexing
  video.index.request      → explicit index request (user submitted a URL)
  channel.discover.request → add a new channel to the crawler list

Run:
    python -m workers.kafka_consumer
"""
import json
import logging
import signal
import sys

from confluent_kafka import Consumer, KafkaError, KafkaException, Producer

from config import settings
from services.indexing.worker import process_video
from services.crawler.channel_crawler import YouTubeCrawler
from db.session import AsyncSessionLocal
from db.models import Channel, IndexingJob

import asyncio

logger = logging.getLogger(__name__)

TOPICS = [
    "search.miss",
    "user.video.watched",
    "video.index.request",
    "channel.discover.request",
]


class KafkaConsumerService:

    def __init__(self) -> None:
        self._consumer = Consumer({
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": settings.kafka_consumer_group,
            "auto.offset.reset": settings.kafka_auto_offset_reset,
            "enable.auto.commit": False,    # manual commit after successful processing
            "max.poll.interval.ms": 300_000,
        })
        self._producer = Producer({
            "bootstrap.servers": settings.kafka_bootstrap_servers,
        })
        self._running = False

    def start(self) -> None:
        self._consumer.subscribe(TOPICS)
        self._running = True
        logger.info("Kafka consumer started. Topics: %s", TOPICS)

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        try:
            while self._running:
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    raise KafkaException(msg.error())

                self._handle(msg.topic(), msg.value())
                self._consumer.commit(message=msg)

        finally:
            self._consumer.close()
            logger.info("Kafka consumer stopped")

    def _shutdown(self, *_) -> None:
        logger.info("Shutdown signal received")
        self._running = False

    def _handle(self, topic: str, raw: bytes) -> None:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error("Bad payload on topic %s: %s", topic, exc)
            return

        handlers = {
            "search.miss":              self._on_search_miss,
            "user.video.watched":       self._on_video_watched,
            "video.index.request":      self._on_index_request,
            "channel.discover.request": self._on_channel_discover,
        }
        handler = handlers.get(topic)
        if handler:
            try:
                handler(payload)
            except Exception as exc:
                logger.exception("Handler error on topic %s: %s", topic, exc)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _on_search_miss(self, payload: dict) -> None:
        """
        Java found < 5 results for a query.
        Payload: { "query": str, "youtube_video_ids": [str, ...] }
        Priority 10 (highest) — user is waiting on search results.
        """
        video_ids: list[str] = payload.get("youtube_video_ids", [])
        logger.info("search.miss → indexing %d videos at high priority", len(video_ids))
        for vid in video_ids:
            process_video.apply_async(
                kwargs={"youtube_video_id": vid, "priority": 10, "queued_by": "search.miss"},
                queue="high_priority",
            )

    def _on_video_watched(self, payload: dict) -> None:
        """
        User clicked / opened a video result.
        Payload: { "user_id": int, "video_id": str }
        Priority 7 — background, not time-sensitive.
        """
        vid = payload.get("video_id")
        if vid:
            process_video.apply_async(
                kwargs={"youtube_video_id": vid, "priority": 7, "queued_by": "user.video.watched"},
                queue="default",
            )

    def _on_index_request(self, payload: dict) -> None:
        """
        Explicit request to index a video (user submitted a URL).
        Payload: { "video_id": str, "priority": int }
        Priority scale: 1 (lowest) – 10 (highest). Jobs at 8+ go to high_priority queue.
        """
        vid = payload.get("video_id")
        pri = int(payload.get("priority", 5))
        if vid:
            process_video.apply_async(
                kwargs={"youtube_video_id": vid, "priority": pri, "queued_by": "video.index.request"},
                queue="high_priority" if pri >= 8 else "default",
            )

    def _on_channel_discover(self, payload: dict) -> None:
        """
        Admin added a new channel to monitor.
        Payload: { "channel_id": str, "industry_id": int }
        Fetches all video IDs and queues them for indexing.
        """
        channel_id = payload.get("channel_id")
        industry_id = payload.get("industry_id")
        if not channel_id:
            return

        logger.info("channel.discover.request → crawling channel %s", channel_id)
        asyncio.run(self._crawl_new_channel(channel_id, industry_id))

    async def _crawl_new_channel(self, youtube_channel_id: str, industry_id: int | None) -> None:
        crawler = YouTubeCrawler()
        meta = crawler.get_channel_meta(youtube_channel_id)
        if not meta:
            return

        async with AsyncSessionLocal() as session:
            channel = Channel(
                youtube_channel_id=youtube_channel_id,
                name=meta.name,
                description=meta.description,
                thumbnail_url=meta.thumbnail_url,
                subscriber_count=meta.subscriber_count,
                primary_industry_id=industry_id,
                is_monitored=True,
            )
            session.add(channel)
            await session.flush()

            video_ids = crawler.get_channel_video_ids(meta.uploads_playlist_id)
            jobs = [
                IndexingJob(
                    youtube_video_id=vid,
                    channel_id=channel.id,
                    industry_id=industry_id,
                    priority=8,
                    queued_by="channel.discover.request",
                )
                for vid in video_ids
            ]
            session.add_all(jobs)
            await session.commit()

        logger.info("Queued %d videos from channel %s", len(video_ids), youtube_channel_id)

        # Publish completion event so Java can update the channel list
        self._producer.produce(
            "video.index.complete",
            json.dumps({"channel_id": youtube_channel_id, "queued": len(video_ids)}).encode(),
        )
        self._producer.flush()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    KafkaConsumerService().start()