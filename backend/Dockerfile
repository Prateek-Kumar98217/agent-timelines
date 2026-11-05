# Built from the repository root: docker build -f backend/Dockerfile .
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/pyproject.toml
RUN uv sync --locked --package agent-timelines-backend --no-dev --no-install-workspace

COPY backend/ backend/
RUN uv sync --locked --package agent-timelines-backend --no-dev

RUN useradd --create-home --uid 1000 app
USER app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app/backend
