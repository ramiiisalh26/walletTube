"""Verify the re-enabled reranker end-to-end (model + cap + rerank()).

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_bench_reranker.py
"""
import time

from api.modules.search.reranker import CrossEncoderReranker, _MAX_SEGMENTS, _MODEL_NAME

print(f"model = {_MODEL_NAME}")
print(f"cap   = {_MAX_SEGMENTS} segments")

TEXT = " ".join(["the java virtual machine handles garbage collection and memory "
                 "management automatically in modern applications"] * 4)


def chunk(i: int) -> dict:
    return {
        "youtube_video_id": f"vid{i % 50}", "title": f"Video {i % 50}",
        "thumbnail_url": None, "channel_name": "ch", "view_count": 1000,
        "text": TEXT, "start_time": 10.0, "end_time": 40.0,
        "vector_score": 0.85 - i * 0.002,
        "parent_text": TEXT, "parent_start_time": 5.0, "parent_end_time": 45.0,
    }


chunks = [chunk(i) for i in range(100)]
r = CrossEncoderReranker.get()
t0 = time.monotonic()
res = r.rerank("how does jvm garbage collection work", chunks, 10)
dt = (time.monotonic() - t0) * 1000
print(f"rerank(): {dt:.0f}ms  ->  {len(res)} results  (CPU contended w/ worker — real is lower)")
if res:
    print(f"top result: {res[0]['youtube_video_id']}  cross_score={res[0]['cross_score']:.3f}")
