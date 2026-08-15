"""
gRPC server exposing the Embedding Service.
Java calls this for every search query (EmbedOne) and Python workers use
EmbedBatch internally during indexing.

Run:
    python -m services.embedding.server

Generate pb2 stubs (run once after editing embedding.proto):
    python -m grpc_tools.protoc -I proto \
        --python_out=services/embedding \
        --grpc_python_out=services/embedding \
        proto/embedding.proto
"""
import asyncio
import logging
from concurrent import futures

import grpc

# These modules are generated from embedding.proto — run the protoc command above.
from services.embedding import embedding_pb2, embedding_pb2_grpc  # type: ignore
from services.embedding.model import EmbeddingModel
from config import settings

logger = logging.getLogger(__name__)


class EmbeddingServicer(embedding_pb2_grpc.EmbeddingServiceServicer):

    def __init__(self) -> None:
        self._model = EmbeddingModel.get()
        logger.info("Embedding model loaded: %s", settings.embedding_model_name)

    def EmbedOne(
        self,
        request: embedding_pb2.EmbedOneRequest,
        context: grpc.ServicerContext,
    ) -> embedding_pb2.EmbedOneResponse:
        vector = self._model.encode_one(request.text)
        return embedding_pb2.EmbedOneResponse(vector=vector)

    def EmbedBatch(
        self,
        request: embedding_pb2.EmbedBatchRequest,
        context: grpc.ServicerContext,
    ) -> embedding_pb2.EmbedBatchResponse:
        vectors = self._model.encode_batch(list(request.texts))
        embeddings = [embedding_pb2.EmbedOneResponse(vector=v) for v in vectors]
        return embedding_pb2.EmbedBatchResponse(embeddings=embeddings)


def serve() -> None:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=8),
        options=[
            ("grpc.max_send_message_length",    50 * 1024 * 1024),
            ("grpc.max_receive_message_length", 50 * 1024 * 1024),
        ],
    )
    embedding_pb2_grpc.add_EmbeddingServiceServicer_to_server(EmbeddingServicer(), server)

    address = f"{settings.embedding_grpc_host}:{settings.embedding_grpc_port}"
    server.add_insecure_port(address)
    server.start()
    logger.info("gRPC Embedding Service listening on %s", address)
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve()