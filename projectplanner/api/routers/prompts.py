"""API router exposing project planner endpoints."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from typing import Iterator, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from projectplanner.logging_utils import get_log_manager, get_logger, get_prompt_audit_path
from projectplanner.models import (
    ExportRequest,
    IngestionRequest,
    IngestionResponse,
    PlanRequest,
    LogsResponse,
    ObservabilityResponse,
    StepsResponse,
    StepUpdateRequest,
)
from projectplanner.services import ingest as ingest_service
from projectplanner.services import plan as plan_service
from projectplanner.services.observability import build_observability_snapshot

router = APIRouter()
LOGGER = get_logger(__name__)


def _parse_query_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO8601 datetime string from query parameters."""

    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    normalized = candidate.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: {value!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=2000,
        description="Maximum number of log records to return when specified.",
    ),
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
    start: Optional[str] = Query(
        None,
        description="Inclusive ISO8601 timestamp specifying the earliest log to include.",
        alias="start",
    ),
    end: Optional[str] = Query(
        None,
        description="Inclusive ISO8601 timestamp specifying the latest log to include.",
        alias="end",
    ),
) -> LogsResponse:
    """Expose captured logs for debugging and observability."""

    level_filter = level.upper() if level else None
    manager = get_log_manager()
    normalized_type = log_type.lower() if log_type else "runtime"
    start_dt = _parse_query_datetime(start)
    end_dt = _parse_query_datetime(end)
    logs = manager.get_logs(
        after=after,
        limit=limit,
        level=level_filter,
        log_type=normalized_type,
        start=start_dt,
        end=end_dt,
    )
    cursor = manager.latest_cursor()
    LOGGER.debug(
        "Served %s log records (after=%s, limit=%s, level=%s, type=%s, start=%s, end=%s)",
        len(logs),
        after,
        limit,
        level_filter,
        normalized_type,
        start_dt.isoformat() if start_dt else None,
        end_dt.isoformat() if end_dt else None,
    )
    return LogsResponse(logs=logs, cursor=cursor)


@router.get("/logs/export")
async def export_logs(
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
    start: Optional[str] = Query(
        None,
        description="Inclusive ISO8601 timestamp specifying the earliest log to include.",
    ),
    end: Optional[str] = Query(
        None,
        description="Inclusive ISO8601 timestamp specifying the latest log to include.",
    ),
) -> StreamingResponse:
    level_filter = level.upper() if level else None
    manager = get_log_manager()
    normalized_type = log_type.lower() if log_type else "runtime"
    start_dt = _parse_query_datetime(start)
    end_dt = _parse_query_datetime(end)
    logs = manager.get_logs(
        level=level_filter,
        log_type=normalized_type,
        start=start_dt,
        end=end_dt,
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"projectplanner-logs-{normalized_type}-{timestamp}.jsonl"

    def iterator() -> Iterator[str]:
        for record in logs:
            yield json.dumps(record, ensure_ascii=False) + "\n"

    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    return StreamingResponse(iterator(), media_type="application/x-ndjson", headers=headers)



@router.get("/prompts/download")
async def download_prompt_audit() -> StreamingResponse:
    """Download the full prompt audit log with untruncated content."""

    path = get_prompt_audit_path()
    if not path.exists() or path.stat().st_size == 0:
        raise HTTPException(status_code=404, detail="Prompt audit log is not available.")

    def iterator() -> Iterator[bytes]:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                yield chunk

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"projectplanner-prompts-{timestamp}.jsonl"
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    LOGGER.info(
        "Prompt audit log download prepared",
        extra={"event": "api.prompts.audit_download", "payload": {"filename": filename}},
    )
    return StreamingResponse(iterator(), media_type="application/x-ndjson", headers=headers)


@router.get("/observability", response_model=ObservabilityResponse)
async def observability_snapshot(
    limit: Optional[int] = Query(
        None,
        ge=100,
        le=2000,
        description="Maximum number of log records inspected per stream when specified.",
    ),
    calls: int = Query(
        120,
        ge=10,
        le=500,
        description="Maximum number of recent module calls to include.",
    ),
    start: Optional[str] = Query(
        None,
        description="Inclusive ISO8601 timestamp specifying the earliest log to include.",
    ),
    end: Optional[str] = Query(
        None,
        description="Inclusive ISO8601 timestamp specifying the latest log to include.",
    ),
) -> ObservabilityResponse:
    LOGGER.debug(
        "Observability snapshot requested (limit=%s, calls=%s, start=%s, end=%s)",
        limit,
        calls,
        start,
        end,
    )
    start_dt = _parse_query_datetime(start)
    end_dt = _parse_query_datetime(end)
    snapshot = build_observability_snapshot(limit=limit, max_calls=calls, start=start_dt, end=end_dt)
    return snapshot


@router.get("/observability/export")
async def export_observability(
    limit: Optional[int] = Query(
        None,
        ge=100,
        le=5000,
        description="Maximum number of log records inspected per stream when specified.",
    ),
    calls: Optional[int] = Query(
        None,
        ge=10,
        le=2000,
        description="Maximum number of recent module calls to include when specified.",
    ),
    start: Optional[str] = Query(
        None,
        description="Inclusive ISO8601 timestamp specifying the earliest log to include.",
    ),
    end: Optional[str] = Query(
        None,
        description="Inclusive ISO8601 timestamp specifying the latest log to include.",
    ),
) -> StreamingResponse:
    start_dt = _parse_query_datetime(start)
    end_dt = _parse_query_datetime(end)
    call_limit = calls if calls is not None else 500
    snapshot = build_observability_snapshot(
        limit=limit,
        max_calls=max(1, call_limit),
        start=start_dt,
        end=end_dt,
    )
    payload = snapshot.model_dump_json(indent=2)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"projectplanner-observability-{timestamp}.json"
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    return StreamingResponse(iter([payload.encode("utf-8")]), media_type="application/json", headers=headers)


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
