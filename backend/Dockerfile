# Built from the repository root: docker build -f backend/Dockerfile .
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/pyproject.toml
COPY browser_service/pyproject.toml browser_service/pyproject.toml
RUN uv sync --locked --package agent-timelines-backend --no-dev --no-install-workspace

COPY backend/ backend/
COPY kb/corpus.jsonl kb/corpus.jsonl
RUN uv sync --locked --package agent-timelines-backend --no-dev

# Bake in the embedding model, so containers need no download at start-up.
ENV APP_EMBEDDING_CACHE_DIR=/opt/embeddings
RUN /app/.venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/embeddings')"

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown -R app /data /opt/embeddings
USER app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app/backend
