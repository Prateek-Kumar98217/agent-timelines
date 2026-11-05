# Built from the repository root: docker build -f browser_service/Dockerfile .
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/pyproject.toml
COPY browser_service/ browser_service/
RUN uv sync --locked --package agent-timelines-browser --no-dev

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN /app/.venv/bin/playwright install --with-deps chromium

RUN useradd --create-home --uid 1000 browser
USER browser
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app/browser_service
