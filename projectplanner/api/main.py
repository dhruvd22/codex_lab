"""FastAPI application factory for the project planner module."""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from html import escape
from pathlib import Path
from textwrap import dedent
from typing import Deque, DefaultDict, Sequence

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from projectplanner.api.routers import prompts
from projectplanner.logging_utils import configure_logging, get_logger
from projectplanner.services.store import ProjectPlannerStore

MAX_BODY_SIZE_BYTES = 2 * 1024 * 1024  # 2 MiB upper bound for uploads
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60


configure_logging(
    logger_name=os.getenv("PROJECTPLANNER_LOGGER_NAME") or None,
    level=os.getenv("PROJECTPLANNER_LOG_LEVEL"),
)
LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class FrontendModule:
    """Metadata describing a discovered frontend bundle."""

    slug: str
    title: str
    dist_path: Path

    @property
    def launch_href(self) -> str:
        return f"/{self.slug}/"


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
            LOGGER.warning(
                "Rate limit exceeded for host %s",
                host,
                extra={
                    "event": "api.rate_limit",
                    "payload": {"attempts": len(bucket), "window_seconds": RATE_LIMIT_WINDOW_SECONDS},
                },
            )
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Try later.")
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = MAX_BODY_SIZE_BYTES + 1
            if declared_size > MAX_BODY_SIZE_BYTES:
                LOGGER.warning(
                    "Rejected oversized payload from %s (%s bytes)",
                    host,
                    content_length,
                    extra={
                        "event": "api.payload_too_large",
                        "payload": {"bytes": declared_size, "limit": MAX_BODY_SIZE_BYTES},
                    },
                )
                raise HTTPException(status_code=413, detail="Request entity too large.")
        return await call_next(request)


def create_app() -> FastAPI:
    """Instantiate and configure the FastAPI application."""

    LOGGER.info(
        "Creating Project Planner FastAPI application.",
        extra={"event": "api.bootstrap"},
    )
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
    LOGGER.info(
        "Persistence layer ready using %s dialect.",
        store.engine.dialect.name,
        extra={"event": "store.schema.ready"},
    )
    app.state.store = store

    app.include_router(prompts.router, prefix="/api/projectplanner", tags=["projectplanner"])

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    _configure_frontend(app)
    return app


def _configure_frontend(app: FastAPI) -> None:
    """Serve the static UI build when available and expose a landing page."""

    modules = _discover_frontend_modules()
    module_slugs = [module.slug for module in modules]
    LOGGER.debug(
        "Discovered %s frontend module(s): %s",
        len(modules),
        module_slugs or ["<none>"],
        extra={"event": "frontend.discover", "payload": {"modules": module_slugs}},
    )
    app.state.frontend_modules = modules

    global_next_static_mounted = False

    for module in modules:
        frontend_app = StaticFiles(directory=str(module.dist_path), html=True)
        app.mount(f"/{module.slug}", frontend_app, name=f"{module.slug}-frontend")

        next_static_dir = module.dist_path / "_next"
        if next_static_dir.is_dir():
            app.mount(
                f"/{module.slug}/_next",
                StaticFiles(directory=str(next_static_dir)),
                name=f"{module.slug}-frontend-assets",
            )
            if not global_next_static_mounted:
                app.mount("/_next", StaticFiles(directory=str(next_static_dir)), name="frontend-assets")
                global_next_static_mounted = True

    @app.get("/", include_in_schema=False)
    async def landing_page() -> HTMLResponse:
        discovered: Sequence[FrontendModule] = getattr(app.state, "frontend_modules", modules)
        return HTMLResponse(content=_render_landing_page(discovered), media_type="text/html")


def _discover_frontend_modules() -> list[FrontendModule]:
    """Locate all built frontend bundles within the repository."""

    override_path = os.getenv("PROJECT_PLANNER_UI_DIST")
    if override_path:
        dist = Path(override_path).expanduser().resolve()
        if (dist / "index.html").is_file():
            return [FrontendModule(slug="projectplanner", title="Project Planner", dist_path=dist)]
        return []

    modules: list[FrontendModule] = []
    repo_root = Path(__file__).resolve().parents[2]
    for candidate in sorted(repo_root.iterdir(), key=lambda path: path.name.lower()):
        if not candidate.is_dir():
            continue
        dist = (candidate / "ui" / "out").resolve()
        slug = candidate.name.replace("_", "-").lower()
        title = _humanize_module_name(candidate.name)

        if (dist / "index.html").is_file():
            modules.append(FrontendModule(slug=slug, title=title, dist_path=dist))
            continue

        nested_dist = dist / slug
        if (nested_dist / "index.html").is_file():
            modules.append(FrontendModule(slug=slug, title=title, dist_path=nested_dist))
    return modules


def _humanize_module_name(name: str) -> str:
    """Convert a directory or package name into a display label."""

    words = name.replace("-", " ").replace("_", " ").split()
    return " ".join(word.capitalize() for word in words) if words else name


def _render_landing_page(modules: Sequence[FrontendModule]) -> str:
    """Render the HTML for the landing page with module launch buttons."""

    module_cards = []
    for module in modules:
        module_cards.append(
            """
            <a class="module-card" href="{href}">
              <span class="module-name">{title}</span>
              <span class="module-action">Launch</span>
            </a>
            """.format(href=module.launch_href, title=escape(module.title))
        )

    if module_cards:
        modules_markup = "\n".join(card.strip() for card in module_cards)
        status_text = "Online"
    else:
        modules_markup = (
            '<p class="empty">No UI modules were detected. Build a module UI '
            'by running <code>npm run build --prefix &lt;module&gt;/ui</code> to expose it here.</p>'
        )
        status_text = "Waiting for UI builds"

    return dedent(
        f"""
        <!DOCTYPE html>
        <html lang="en">
          <head>
            <meta charset="utf-8" />
            <title>Project Planner Services</title>
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <style>
              :root {{
                color-scheme: dark;
                font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                background-color: #0f172a;
                color: #e2e8f0;
              }}
              * {{
                box-sizing: border-box;
              }}
              body {{
                margin: 0;
                min-height: 100vh;
                display: flex;
                flex-direction: column;
              }}
              main {{
                width: min(960px, 92vw);
                margin: auto;
                padding: 3rem 0 4rem;
              }}
              h1 {{
                font-size: clamp(2rem, 3vw + 1rem, 3rem);
                margin-bottom: 0.75rem;
              }}
              p.lead {{
                margin-top: 0;
                margin-bottom: 2rem;
                color: #94a3b8;
              }}
              .modules {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: 1.5rem;
              }}
              .module-card {{
                display: flex;
                flex-direction: column;
                justify-content: space-between;
                padding: 1.5rem;
                border-radius: 1rem;
                text-decoration: none;
                background: linear-gradient(145deg, rgba(30,41,59,0.9), rgba(15,23,42,0.9));
                border: 1px solid rgba(148, 163, 184, 0.15);
                box-shadow: 0 12px 28px rgba(15, 23, 42, 0.35);
                color: inherit;
                transition: transform 120ms ease, border-color 120ms ease;
              }}
              .module-card:hover {{
                transform: translateY(-4px);
                border-color: rgba(129, 140, 248, 0.6);
              }}
              .module-name {{
                font-size: 1.2rem;
                font-weight: 600;
                margin-bottom: 0.75rem;
              }}
              .module-action {{
                font-size: 0.9rem;
                font-weight: 500;
                color: #a855f7;
              }}
              .empty {{
                padding: 1.5rem;
                border-radius: 1rem;
                background: rgba(30,41,59,0.6);
                border: 1px dashed rgba(148, 163, 184, 0.4);
              }}
              footer {{
                margin-top: 4rem;
                font-size: 0.85rem;
                color: #64748b;
                display: flex;
                gap: 1rem;
                flex-wrap: wrap;
              }}
              code {{
                font-family: "Fira Code", "Menlo", monospace;
              }}
              a {{
                color: #38bdf8;
              }}
              a:hover {{
                color: #7dd3fc;
              }}
            </style>
          </head>
          <body>
            <main>
              <h1>Project Planner Services</h1>
              <p class="lead">Launch an available UI module or explore the API via <a href="/docs">Swagger</a>.</p>
              <div class="modules">
                {modules_markup}
              </div>
              <footer>
                <span>Status: {status_text}</span>
                <span>API root: <code>/api/projectplanner</code></span>
              </footer>
            </main>
          </body>
        </html>
        """
    )


app = create_app()
