"""
4-level industry classification cascade (decided in conversation):

  Level 1 — Channel inheritance (95% accuracy, ~80% of videos)
             If the channel already has a primary_industry_id, use it.

  Level 2 — YouTube categoryId mapping (70% accuracy, ~15% of videos)
             Map the numeric category YouTube assigns to every video.

  Level 3 — Keyword matching on title + description (60% accuracy, ~4%)
             Count industry keyword hits; require at least 2 matches.

  Level 4 — Embedding similarity (80% accuracy, fallback)
             Cosine similarity between the video's title+description embedding
             and each industry's stored embedding vector.
"""
import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# YouTube categoryId → industry slug
# Only the categories we care about for our active industries
_YT_CATEGORY_MAP: dict[str, str] = {
    "28": "programming",   # Science & Technology
    "27": "education",     # Education
    "26": "education",     # Howto & Style
    "22": "education",     # People & Blogs (broad)
    "17": "fitness",       # Sports
    "10": "creative",      # Music
    "1":  "creative",      # Film & Animation
    "24": "creative",      # Entertainment
    "25": "education",     # News & Politics
}

# Keyword lists per industry slug — used in Level 3
_INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "programming": [
        "python", "javascript", "typescript", "react", "nodejs", "coding",
        "programming", "developer", "api", "database", "html", "css",
        "web development", "software", "algorithm", "data structure",
        "machine learning", "docker", "kubernetes", "github", "devops",
        "java", "golang", "rust", "flutter", "android", "ios",
    ],
    "business": [
        "marketing", "startup", "entrepreneur", "sales", "revenue",
        "business", "investment", "finance", "strategy", "ecommerce",
        "shopify", "amazon fba", "passive income", "digital marketing",
    ],
    "education": [
        "lecture", "university", "course", "tutorial", "lesson",
        "math", "physics", "chemistry", "biology", "history",
        "science", "study", "exam", "academic",
    ],
    "creative": [
        "photoshop", "illustrator", "premiere", "after effects", "blender",
        "figma", "design", "animation", "music production", "fl studio",
        "ableton", "photography", "video editing", "ui ux",
    ],
    "fitness": [
        "workout", "exercise", "gym", "muscle", "weight loss",
        "nutrition", "diet", "fitness", "training", "health",
        "yoga", "cardio", "strength", "bodybuilding",
    ],
}


@dataclass
class ClassificationResult:
    industry_slug: str | None
    confidence: float          # 0.0 – 1.0
    method: str                # 'channel' | 'category' | 'keyword' | 'embedding' | 'unknown'


class VideoClassifier:
    """
    Stateless classifier. Industry embeddings are injected at startup by the
    Classifier service, which loads them from the `industries` table.
    """

    def __init__(self, industry_embeddings: dict[str, list[float]] | None = None) -> None:
        # {slug: vector[768]}  — populated after DB is available
        self._industry_embeddings: dict[str, list[float]] = industry_embeddings or {}

    def set_industry_embeddings(self, embeddings: dict[str, list[float]]) -> None:
        self._industry_embeddings = embeddings

    def classify(
        self,
        title: str,
        description: str,
        yt_category_id: str | None = None,
        channel_industry_slug: str | None = None,
        title_description_embedding: list[float] | None = None,
    ) -> ClassificationResult:

        # Level 1: channel inheritance
        if channel_industry_slug:
            return ClassificationResult(channel_industry_slug, 0.95, "channel")

        # Level 2: YouTube category
        if yt_category_id and yt_category_id in _YT_CATEGORY_MAP:
            slug = _YT_CATEGORY_MAP[yt_category_id]
            return ClassificationResult(slug, 0.70, "category")

        # Level 3: keyword matching
        result = self._classify_by_keywords(title, description)
        if result:
            return result

        # Level 4: embedding similarity
        if title_description_embedding and self._industry_embeddings:
            result = self._classify_by_embedding(title_description_embedding)
            if result:
                return result

        return ClassificationResult(None, 0.0, "unknown")

    def _classify_by_keywords(self, title: str, description: str) -> ClassificationResult | None:
        text = f"{title} {description}".lower()
        scores: dict[str, int] = {}

        for slug, keywords in _INDUSTRY_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in text)
            if count >= 2:
                scores[slug] = count

        if not scores:
            return None

        best = max(scores, key=lambda s: scores[s])
        confidence = min(0.60 + scores[best] * 0.02, 0.85)
        return ClassificationResult(best, confidence, "keyword")

    def _classify_by_embedding(self, query_vector: list[float]) -> ClassificationResult | None:
        q = np.array(query_vector, dtype=np.float32)
        best_slug, best_score = None, -1.0

        for slug, ind_vector in self._industry_embeddings.items():
            v = np.array(ind_vector, dtype=np.float32)
            score = float(np.dot(q, v) / (np.linalg.norm(q) * np.linalg.norm(v) + 1e-9))
            if score > best_score:
                best_score, best_slug = score, slug

        if best_slug and best_score >= 0.50:
            return ClassificationResult(best_slug, best_score, "embedding")

        return None
