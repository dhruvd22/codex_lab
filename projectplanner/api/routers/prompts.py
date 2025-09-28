"""API router exposing project planner endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from projectplanner.models import (
    ExportRequest,
    IngestionRequest,
    IngestionResponse,
    PlanRequest,
    PlanResponse,
    StepsResponse,
)
from projectplanner.services import ingest as ingest_service
from projectplanner.services import plan as plan_service

router = APIRouter()


@router.post("/ingest", response_model=IngestionResponse)
async def ingest_endpoint(payload: IngestionRequest, request: Request) -> IngestionResponse:
    """Ingest a document and return run metadata."""

    store = request.app.state.store
    return await ingest_service.ingest_document(payload, store=store)


@router.post("/plan", response_model=PlanResponse)
async def plan_endpoint(payload: PlanRequest, request: Request) -> PlanResponse:
    """Execute the multi-agent planning workflow."""

    store = request.app.state.store
    return await plan_service.run_planning_workflow(payload, store=store)



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
