"""
gRPC client for the Python embedding service.
Wraps the blocking stub in asyncio.to_thread() so it doesn't stall the event loop.
"""
import asyncio
import logging
from functools import lru_cache

import grpc

from config import settings
from services.embedding import embedding_pb2, embedding_pb2_grpc  # type: ignore

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_stub() -> embedding_pb2_grpc.EmbeddingServiceStub:
    channel = grpc.insecure_channel(
        f"{settings.embedding_grpc_client_host}:{settings.embedding_grpc_client_port}",
        options=[
            ("grpc.max_send_message_length", 10 * 1024 * 1024),
            ("grpc.max_receive_message_length", 10 * 1024 * 1024),
        ],
    )
    return embedding_pb2_grpc.EmbeddingServiceStub(channel)


def _embed_one_sync(text: str) -> list[float]:
    stub = _get_stub()
    response = stub.EmbedOne(embedding_pb2.EmbedOneRequest(text=text))
    return list(response.vector)


async def embed_one(text: str) -> list[float]:
    """Embed a single query string; returns a 768-dim float list."""
    return await asyncio.to_thread(_embed_one_sync, text)


def _embed_batch_sync(texts: list[str]) -> list[list[float]]:
    stub = _get_stub()
    response = stub.EmbedBatch(embedding_pb2.EmbedBatchRequest(texts=texts))
    return [list(e.vector) for e in response.embeddings]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts; returns a list of 768-dim float lists."""
    return await asyncio.to_thread(_embed_batch_sync, texts)
