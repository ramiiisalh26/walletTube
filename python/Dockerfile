FROM python:3.11-slim

WORKDIR /app

# System deps: libpq for asyncpg, build-essential for compiling C extensions, curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer cached until requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model so every container starts without an internet hit.
# Stored in /root/.cache/huggingface/ inside this layer.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-base-en-v1.5')"

# Copy application source
COPY . .

# gRPC embedding server (50051) and FastAPI admin API (8001)
EXPOSE 50051 8001
