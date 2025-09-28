"""FastAPI application factory for the project planner module."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, DefaultDict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from projectplanner.api.routers import prompts
from projectplanner.services.store import ProjectPlannerStore

MAX_BODY_SIZE_BYTES = 2 * 1024 * 1024  # 2 MiB upper bound for uploads
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter keyed by client host."""

    def __init__(self, app: FastAPI) -> None:  # type: ignore[override]
        super().__init__(app)
        self._buckets: DefaultDict[str, Deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        host = request.client.host if request.client else "anonymous"
        bucket = self._buckets[host]
        now = time.monotonic()
        bucket.append(now)
        while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) > RATE_LIMIT_REQUESTS:
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Try later.")
        if request.headers.get("content-length") and int(request.headers["content-length"]) > MAX_BODY_SIZE_BYTES:
            raise HTTPException(status_code=413, detail="Request entity too large.")
        return await call_next(request)


def create_app() -> FastAPI:
    """Instantiate and configure the FastAPI application."""

    app = FastAPI(title="Project Planner API", version="0.1.0")

    # Basic CORS defaults suitable for local development and preview deployments.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimiterMiddleware)

    store = ProjectPlannerStore.from_env()
    store.ensure_schema()
    app.state.store = store

    app.include_router(prompts.router, prefix="/api/projectplanner", tags=["projectplanner"])
    return app


app = create_app()