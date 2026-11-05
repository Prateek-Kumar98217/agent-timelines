"""HTTP interface: `uvicorn browser_service.main:app`. Only the tool gateway can reach it."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from browser_service.browser import BrowserService, PageUnavailableError, UrlRefusedError


class FetchRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


def create_app(service: BrowserService | None = None) -> FastAPI:
    browser = service or BrowserService()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await browser.start()
        yield
        await browser.stop()

    app = FastAPI(title="agent-timelines browser", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/fetch")
    async def fetch(body: FetchRequest) -> dict[str, Any]:
        try:
            return asdict(await browser.fetch(body.url))
        except UrlRefusedError as exc:
            raise HTTPException(403, str(exc)) from exc
        except PageUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc

    return app


app = create_app()
