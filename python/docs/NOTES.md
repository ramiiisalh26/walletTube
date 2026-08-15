# YTSearch — Dev Notes

## Embedding Performance

### Why indexing is slow locally
The gRPC embedding server runs BAAI/bge-small-en-v1.5 on CPU.
On a local machine, embedding 300+ chunks takes 60–120 seconds.
This is a local-only problem — not a code issue.

### Production speedup expectations
| Environment | Hardware | 300-chunk video |
|---|---|---|
| Local dev | Laptop CPU | 60–120 s |
| CPU server | AWS c6i.4xlarge (16 vCPUs) | 10–15 s |
| GPU server | AWS g4dn.xlarge (T4 GPU) | < 2 s |

**Recommended production setup:** run the gRPC embedding server on a GPU instance.
Even a low-end T4 GPU embeds the full batch in under 2 seconds regardless of video length.

### Local optimizations applied (dev only)
- `max_seq_length = 128` (model.py) — ~4× faster per chunk, slight quality tradeoff
- Adaptive chunk windows (video.py) — 60s / 90s / 120s based on video length
- `embedding_batch_size = 64` (config.py) — better CPU utilization

### Before going to production
- Remove or raise `max_seq_length` back to 256–512 for better search quality on long chunks
- Switch gRPC server to GPU instance
- Re-enable the original `batch_size=32` or tune per GPU memory

---

## Videos Without Transcripts
Some videos have no captions at all (no manual, no auto-generated).
Current behavior: indexing fails with "No transcript available for this video".

**Options to handle this:**
1. **Whisper AI** (best quality) — download audio via yt-dlp, transcribe locally.
   - `openai-whisper` or `faster-whisper` (ONNX, 3–5× faster).
   - `tiny` model: ~30s for a 10-min video on CPU; ~3s on GPU.
   - Uncomment `# openai-whisper==20231117` in requirements.txt to enable.
2. **Video description fallback** — index the YouTube description as a single chunk.
   Fast but limited; good enough for music videos or short clips.

---

## Chrome Extension
- Extension files: `chrome-extension/`
- Requires API server running on `localhost:8080`
- Requires gRPC embedding server running on `localhost:50051`
- Load in Chrome: `chrome://extensions` → Developer mode → Load unpacked → select `chrome-extension/`
- After any file change: disable/enable the extension + hard-reload the YouTube tab (Ctrl+Shift+R)

---

## Starting the Stack (local dev)

```bash
# Terminal 1 — gRPC embedding server
.venv\Scripts\python -m services.embedding.server

# Terminal 2 — FastAPI
.venv\Scripts\uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
```
