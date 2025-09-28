"""API router exposing project planner endpoints."""
from __future__ import annotations

import json

from typing import Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from projectplanner.models import (
    ExportRequest,
    IngestionRequest,
    IngestionResponse,
    PlanRequest,
    StepsResponse,
    StepUpdateRequest,
)
from projectplanner.services import ingest as ingest_service
from projectplanner.services import plan as plan_service

router = APIRouter()


def _format_sse(event_type: str, payload: dict) -> str:
    """Render a server-sent event chunk."""

    data = json.dumps(payload)
    return f"event: {event_type}
data: {data}

"


@router.post("/ingest", response_model=IngestionResponse)
async def ingest_endpoint(payload: IngestionRequest, request: Request) -> IngestionResponse:
    """Ingest a document and return run metadata."""

    store = request.app.state.store
    return await ingest_service.ingest_document(payload, store=store)


@router.post("/plan")
async def plan_endpoint(payload: PlanRequest, request: Request) -> StreamingResponse:
    """Execute the multi-agent planning workflow as a server-sent event stream."""

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
    return response


@router.put("/steps/{run_id}", response_model=StepsResponse)
async def update_steps(run_id: str, payload: StepUpdateRequest, request: Request) -> StepsResponse:
    """Persist a new ordered list of steps."""

    store = request.app.state.store
    if not store.run_exists(run_id):
        raise HTTPException(status_code=404, detail="Run not found.")
    store.upsert_steps(run_id, payload.steps)
    return StepsResponse(run_id=run_id, steps=payload.steps)


@router.get("/steps/{run_id}", response_model=StepsResponse)
async def get_steps(run_id: str, request: Request) -> StepsResponse:
    """Return the stored ordered prompt steps for a run."""

    store = request.app.state.store
    steps = store.get_steps(run_id)
    if not steps:
        raise HTTPException(status_code=404, detail="Run not found or steps unavailable.")
    return StepsResponse(run_id=run_id, steps=steps)


@router.post("/export")
async def export_prompts(payload: ExportRequest, request: Request) -> StreamingResponse:
    """Generate an export file in the requested format and stream it back."""

    store = request.app.state.store
    export_bundle = await plan_service.export_prompts(payload, store=store)
    response = StreamingResponse(
        content=(chunk.encode("utf-8") for chunk in [export_bundle.content]),
        media_type=export_bundle.metadata.content_type,
    )
    response.headers["Content-Disposition"] = f"attachment; filename={export_bundle.metadata.filename}"
    return response
