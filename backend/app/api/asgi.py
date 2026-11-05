"""ASGI entry point: `uvicorn app.api.asgi:app`."""

from app.api.main import create_app

app = create_app()
