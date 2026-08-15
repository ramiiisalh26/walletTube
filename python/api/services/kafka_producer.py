"""
Thin Kafka producer wrapper.
All publish calls are fire-and-forget; failures are logged but never raised.
"""
import asyncio
import json
import logging
from functools import lru_cache

from confluent_kafka import Producer

from config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_producer() -> Producer:
    return Producer({"bootstrap.servers": settings.kafka_bootstrap_servers})


def _delivery_cb(err, msg) -> None:
    if err:
        logger.warning("Kafka delivery failed topic=%s: %s", msg.topic(), err)


def _publish_sync(topic: str, key: str, payload: dict) -> None:
    try:
        producer = _get_producer()
        producer.produce(
            topic,
            key=key.encode(),
            value=json.dumps(payload).encode(),
            callback=_delivery_cb,
        )
        producer.poll(0)
    except Exception as exc:
        logger.warning("Kafka publish failed topic=%s key=%s: %s", topic, key, exc)


async def publish(topic: str, key: str, payload: dict) -> None:
    await asyncio.to_thread(_publish_sync, topic, key, payload)


async def publish_search_miss(query: str, youtube_video_ids: list[str]) -> None:
    await publish("search.miss", query, {"query": query, "youtube_video_ids": youtube_video_ids})


async def publish_index_request(youtube_video_id: str, priority: int = 5) -> None:
    await publish("video.index.request", youtube_video_id, {"video_id": youtube_video_id, "priority": priority})


async def publish_video_watched(user_id: int, youtube_video_id: str) -> None:
    await publish("user.video.watched", youtube_video_id, {"user_id": user_id, "video_id": youtube_video_id})


async def publish_channel_discover(channel_id: str, industry_id: int) -> None:
    await publish("channel.discover.request", channel_id, {"channel_id": channel_id, "industry_id": industry_id})
