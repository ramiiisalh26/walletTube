"""
Singleton wrapper around BAAI/bge-base-en-v1.5 (768-dim).
One instance is shared across the whole process — model loading is expensive.
"""
import threading
import numpy as np
from sentence_transformers import SentenceTransformer

from config import settings


class EmbeddingModel:
    _instance: "EmbeddingModel | None" = None
    # CPU encoding is single-threaded at the PyTorch level — running multiple
    # encode_batch calls concurrently causes core contention and makes each one
    # ~6x slower. This lock serialises all encode calls so each runs at full speed.
    _encode_lock = threading.Lock()

    def __init__(self) -> None:
        import os
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        self._model = SentenceTransformer(
            settings.embedding_model_name,
            device=settings.device,   # "cpu" dev | "cuda" GPU prod
        )
        self._model.max_seq_length = 128

    @classmethod
    def get(cls) -> "EmbeddingModel":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def encode_one(self, text: str) -> list[float]:
        with self._encode_lock:
            vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._encode_lock:
            vectors = self._model.encode(
                texts,
                batch_size=settings.embedding_batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        return vectors.tolist()

    def mean_vector(self, vectors: list[list[float]]) -> list[float]:
        """Compute the mean of multiple embeddings (used for video-level embedding)."""
        arr = np.array(vectors, dtype=np.float32)
        mean = arr.mean(axis=0)
        # Re-normalise so cosine similarity remains valid
        norm = np.linalg.norm(mean)
        if norm > 0:
            mean = mean / norm
        return mean.tolist()