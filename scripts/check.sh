#!/usr/bin/env sh
# Runs every check a commit must pass. Integration tests need `docker compose up -d --wait postgres`.
set -e
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest backend -q -W ignore
uv run pytest browser_service -q -W ignore
uv run pytest evals/tests -q -W ignore -p no:cacheprovider
