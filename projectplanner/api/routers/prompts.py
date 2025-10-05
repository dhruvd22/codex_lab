"""API router exposing project planner endpoints."""
from __future__ import annotations

import json

from typing import Iterator, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from projectplanner.logging_utils import get_log_manager, get_logger
from projectplanner.models import (
    ExportRequest,
    IngestionRequest,
    IngestionResponse,
    PlanRequest,
    LogsResponse,
    StepsResponse,
    StepUpdateRequest,
)
from projectplanner.services import ingest as ingest_service
from projectplanner.services import plan as plan_service

router = APIRouter()
LOGGER = get_logger(__name__)


def _format_sse(event_type: str, payload: dict) -> str:
    """Render a server-sent event chunk."""

    data = json.dumps(payload)
    return f"event: {event_type}\n" f"data: {data}\n\n"


@router.post("/ingest", response_model=IngestionResponse)
async def ingest_endpoint(payload: IngestionRequest, request: Request) -> IngestionResponse:
    """Ingest a document and return run metadata."""

    LOGGER.info(
        "Ingest request received",
        extra={
            "event": "api.ingest.start",
            "payload": {
                "has_text": bool(payload.text),
                "has_url": bool(payload.url),
                "has_file": bool(payload.file_id),
                "text_chars": len(payload.text or "") if payload.text else 0,
            },
        },
    )
    store = request.app.state.store
    response = await ingest_service.ingest_document(payload, store=store)
    LOGGER.info(
        "Ingest request completed",
        extra={"event": "api.ingest.complete", "run_id": response.run_id},
    )
    return response


@router.post("/plan")
async def plan_endpoint(payload: PlanRequest, request: Request) -> StreamingResponse:
    """Execute the multi-agent planning workflow as a server-sent event stream."""

    LOGGER.info(
        "Planning request received for run %s",
        payload.run_id,
        extra={
            "event": "api.plan.start",
            "run_id": payload.run_id,
            "payload": {"style": payload.style},
        },
    )
    store = request.app.state.store

    def event_stream() -> Iterator[str]:
        generator = plan_service.planning_event_stream(payload, store=store)
        while True:
            try:
                event_type, event_payload = next(generator)
                yield _format_sse(event_type, event_payload)
            except StopIteration:
                break

    response = StreamingResponse(event_stream(), media_type="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    LOGGER.info(
        "Planning stream established for run %s",
        payload.run_id,
        extra={"event": "api.plan.stream", "run_id": payload.run_id},
    )
    return response


@router.put("/steps/{run_id}", response_model=StepsResponse)
async def update_steps(run_id: str, payload: StepUpdateRequest, request: Request) -> StepsResponse:
    """Persist a new ordered list of steps."""

    LOGGER.info(
        "Update steps request for run %s",
        run_id,
        extra={"event": "api.steps.update", "run_id": run_id, "payload": {"count": len(payload.steps)}},
    )
    store = request.app.state.store
    if not store.run_exists(run_id):
        raise HTTPException(status_code=404, detail="Run not found.")
    store.upsert_steps(run_id, payload.steps)
    return StepsResponse(run_id=run_id, steps=payload.steps)


@router.get("/steps/{run_id}", response_model=StepsResponse)
async def get_steps(run_id: str, request: Request) -> StepsResponse:
    """Return the stored ordered prompt steps for a run."""

    LOGGER.debug(
        "Fetching steps for run %s",
        run_id,
        extra={"event": "api.steps.fetch", "run_id": run_id},
    )
    store = request.app.state.store
    steps = store.get_steps(run_id)
    if not steps:
        raise HTTPException(status_code=404, detail="Run not found or steps unavailable.")
    return StepsResponse(run_id=run_id, steps=steps)


@router.get("/logs", response_model=LogsResponse)
async def list_logs(
    after: Optional[int] = Query(
        None, ge=0, description="Return records with a sequence id greater than this value.",
    ),
    limit: int = Query(200, ge=1, le=2000, description="Maximum number of log records to return."),
    level: Optional[str] = Query(
        None,
        pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
        description="Minimum severity level to include.",
    ),
    log_type: str = Query(
        "runtime",
        alias="type",
        pattern="^(?i)(runtime|prompts)$",
        description="Log stream to return (runtime or prompts).",
    ),
) -> LogsResponse:
    """Expose captured logs for debugging and observability."""

    level_filter = level.upper() if level else None
    manager = get_log_manager()
    normalized_type = log_type.lower() if log_type else "runtime"
    logs = manager.get_logs(after=after, limit=limit, level=level_filter, log_type=normalized_type)
    cursor = manager.latest_cursor()
    LOGGER.debug(
        "Served %s log records (after=%s, limit=%s, level=%s, type=%s)",
        len(logs),
        after,
        limit,
        level_filter,
        normalized_type,
    )
    return LogsResponse(logs=logs, cursor=cursor)


@router.post("/export")
async def export_prompts(payload: ExportRequest, request: Request) -> StreamingResponse:
    """Generate an export file in the requested format and stream it back."""

    LOGGER.info(
        "Export request received for run %s",
        payload.run_id,
        extra={"event": "api.export.start", "run_id": payload.run_id, "payload": {"format": payload.format}},
    )
    store = request.app.state.store
    export_bundle = await plan_service.export_prompts(payload, store=store)
    response = StreamingResponse(
        content=(chunk.encode("utf-8") for chunk in [export_bundle.content]),
        media_type=export_bundle.metadata.content_type,
    )
    response.headers["Content-Disposition"] = f"attachment; filename={export_bundle.metadata.filename}"
    LOGGER.info(
        "Export response ready for run %s",
        payload.run_id,
        extra={"event": "api.export.complete", "run_id": payload.run_id, "payload": {"filename": export_bundle.metadata.filename}},
    )
    return response
